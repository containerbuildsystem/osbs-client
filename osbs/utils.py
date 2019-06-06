"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals
from functools import wraps
import contextlib
import copy
import logging
import os
import os.path
import re
import shutil
import string
import subprocess
import sys
import tempfile
import tarfile
import time
import requests
from collections import namedtuple
from datetime import datetime
from io import BytesIO
from hashlib import sha256
from osbs.repo_utils import RepoConfiguration, RepoInfo, AdditionalTagsConfig
from osbs.constants import (OS_CONFLICT_MAX_RETRIES, OS_CONFLICT_WAIT,
                            GIT_MAX_RETRIES, GIT_BACKOFF_FACTOR,
                            GIT_FETCH_RETRY)


from six.moves import http_client
from six.moves.urllib.parse import urlparse

try:
    # py3
    if not hasattr(datetime.now(), 'timestamp'):
        raise ImportError

    import dateutil.parser
except ImportError:
    # py2 workaround in get_time_from_rfc3339() below
    from time import strptime
    from calendar import timegm

from dockerfile_parse import DockerfileParser
from osbs.exceptions import OsbsException, OsbsResponseException, OsbsValidationException

logger = logging.getLogger(__name__)
ClonedRepoData = namedtuple('ClonedRepoData', ['repo_path', 'commit_id', 'commit_depth'])


class RegistryURI(object):
    # Group 0: URI without path -- allowing empty value -- including:
    # - Group 1: optional 'http://' / 'https://'
    # - Group 2: hostname and port
    # Group 3: path, including:
    # - Group 4: optional API version, 'v' followed by a number
    versionre = re.compile(r'((https?://)?([^/]*))(/(v\d+)?)?$')

    def __init__(self, uri):
        groups = self.versionre.match(uri).groups()
        self.docker_uri = groups[2]
        self.version = groups[4] or 'v2'
        self.scheme = groups[1] or ''

        if self.version == 'v1':
            raise OsbsValidationException('Invalid API version requested in {}'.format(uri))

    @property
    def uri(self):
        return self.scheme + self.docker_uri

    def __repr__(self):
        return self.uri


class TarWriter(object):
    def __init__(self, outfile, directory=None):
        mode = "w|bz2"
        if hasattr(outfile, "write"):
            self.tarfile = tarfile.open(fileobj=outfile, mode=mode)
        else:
            self.tarfile = tarfile.open(name=outfile, mode=mode)
        self.directory = directory or ""

    def __enter__(self):
        return self

    def __exit__(self, typ, val, tb):
        self.tarfile.close()

    def write_file(self, name, content):
        buf = BytesIO(content)
        arcname = os.path.join(self.directory, name)

        ti = tarfile.TarInfo(arcname)
        ti.size = len(content)
        self.tarfile.addfile(ti, fileobj=buf)


class TarReader(object):
    TarFile = namedtuple('TarFile', ['filename', 'fileobj'])

    def __init__(self, infile):
        mode = "r|bz2"
        if hasattr(infile, "read"):
            self.tarfile = tarfile.open(fileobj=infile, mode=mode)
        else:
            self.tarfile = tarfile.open(name=infile, mode=mode)

    def __iter__(self):
        return self

    def __next__(self):
        ti = self.tarfile.next()    # pylint: disable=next-method-called

        if ti is None:
            self.close()
            raise StopIteration()

        return self.TarFile(ti.name, self.tarfile.extractfile(ti))

    next = __next__     # py2 compatibility

    def close(self):
        self.tarfile.close()


def graceful_chain_get(d, *args):
    if not d:
        return None
    t = copy.deepcopy(d)
    for arg in args:
        try:
            t = t[arg]
        except (IndexError, KeyError):
            return None
    return t


def graceful_chain_del(d, *args):
    if not d:
        return
    for arg in args[:-1]:
        try:
            d = d[arg]
        except (IndexError, KeyError):
            return
    try:
        del d[args[-1]]
    except (IndexError, KeyError):
        pass


def has_triggers(build_config):
    return graceful_chain_get(build_config, 'spec', 'triggers') is not None


def clean_triggers(orig, new):
    if not has_triggers(new) and has_triggers(orig):
        orig['spec']['triggers'] = [t for t in orig['spec']['triggers']
                                    if t.get('type', None) != 'ImageChange']


def buildconfig_update(orig, new, remove_nonexistent_keys=False):
    """Performs update of given `orig` BuildConfig with values from `new` BuildConfig.
    Both BuildConfigs have to be represented as `dict`s.

    This function:
    - adds all key/value pairs to `orig` from `new` that are missing
    - replaces values in `orig` for keys that are in both
    - removes key/value pairs from `orig` for keys that are not in `new`,
      but only in dicts nested inside `strategy` key
      (see https://github.com/containerbuildsystem/osbs-client/pull/273#issuecomment-148038314)
    """
    if isinstance(orig, dict) and isinstance(new, dict):
        clean_triggers(orig, new)
        if remove_nonexistent_keys:
            missing = set(orig.keys()) - set(new.keys())
            for k in missing:
                orig.pop(k)
        for k, v in new.items():
            if k == 'strategy':
                remove_nonexistent_keys = True
            if isinstance(orig.get(k), dict) and isinstance(v, dict):
                buildconfig_update(orig[k], v, remove_nonexistent_keys)
            else:
                orig[k] = v


@contextlib.contextmanager
def checkout_git_repo(git_url, target_dir=None, commit=None, retry_times=GIT_MAX_RETRIES,
                      branch=None, depth=None):
    """
    clone provided git repo to target_dir, optionally checkout provided commit
    yield the ClonedRepoData and delete the repo when finished

    :param git_url: str, git repo to clone
    :param target_dir: str, filesystem path where the repo should be cloned
    :param commit: str, commit to checkout, SHA-1 or ref
    :param retry_times: int, number of retries for git clone
    :param branch: str, optional branch of the commit, required if depth is provided
    :param depth: int, optional expected depth
    :return: str, int, commit ID of HEAD
    """
    tmpdir = tempfile.mkdtemp()
    target_dir = target_dir or os.path.join(tmpdir, "repo")
    try:
        yield clone_git_repo(git_url, target_dir, commit, retry_times, branch, depth)
    finally:
        shutil.rmtree(tmpdir)


def clone_git_repo(git_url, target_dir=None, commit=None, retry_times=GIT_MAX_RETRIES, branch=None,
                   depth=None):
    """
    clone provided git repo to target_dir, optionally checkout provided commit

    :param git_url: str, git repo to clone
    :param target_dir: str, filesystem path where the repo should be cloned
    :param commit: str, commit to checkout, SHA-1 or ref
    :param retry_times: int, number of retries for git clone
    :param branch: str, optional branch of the commit, required if depth is provided
    :param depth: int, optional expected depth
    :return: str, int, commit ID of HEAD
    """
    retry_delay = GIT_BACKOFF_FACTOR
    target_dir = target_dir or os.path.join(tempfile.mkdtemp(), "repo")
    commit = commit or "master"
    logger.info("cloning git repo '%s'", git_url)
    logger.debug("url = '%s', dir = '%s', commit = '%s'",
                 git_url, target_dir, commit)

    cmd = ["git", "clone"]
    if branch:
        cmd += ["-b", branch, "--single-branch"]
        if depth:
            cmd += ["--depth", str(depth)]
    elif depth:
        logger.warning("branch not provided for %s, depth setting ignored", git_url)
        depth = None

    cmd += [git_url, target_dir]

    logger.debug("cloning '%s'", cmd)
    repo_commit = ''
    repo_depth = None
    for counter in range(retry_times + 1):
        try:
            # we are using check_output, even though we aren't using
            # the return value, but we will get 'output' in exception
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            repo_commit, repo_depth = reset_git_repo(target_dir, commit, depth)
            break
        except subprocess.CalledProcessError as exc:
            if counter != retry_times:
                logger.info("retrying command '%s':\n '%s'", cmd, exc.output)
                time.sleep(retry_delay * (2 ** counter))
            else:
                raise OsbsException("Unable to clone git repo '%s' "
                                    "branch '%s'" % (git_url, branch),
                                    cause=exc, traceback=sys.exc_info()[2])

    return ClonedRepoData(target_dir, repo_commit, repo_depth)


def reset_git_repo(target_dir, git_reference, retry_depth=None):
    """
    hard reset git clone in target_dir to given git_reference

    :param target_dir: str, filesystem path where the repo is cloned
    :param git_reference: str, any valid git reference
    :param retry_depth: int, if the repo was cloned with --shallow, this is the expected
                        depth of the commit
    :return: str and int, commit ID of HEAD and commit depth of git_reference
    """
    deepen = retry_depth or 0
    base_commit_depth = 0
    for _ in range(GIT_FETCH_RETRY):
        try:
            if not deepen:
                cmd = ['git', 'rev-list', '--count', git_reference]
                base_commit_depth = int(subprocess.check_output(cmd, cwd=target_dir)) - 1
            cmd = ["git", "reset", "--hard", git_reference]
            logger.debug("Resetting current HEAD: '%s'", cmd)
            subprocess.check_call(cmd, cwd=target_dir)
            break
        except subprocess.CalledProcessError:
            if not deepen:
                raise OsbsException('cannot find commit %s in repo %s' %
                                    (git_reference, target_dir))
            deepen *= 2
            cmd = ["git", "fetch", "--depth", str(deepen)]
            subprocess.check_call(cmd, cwd=target_dir)
            logger.debug("Couldn't find commit %s, increasing depth with '%s'", git_reference,
                         cmd)
    else:
        raise OsbsException('cannot find commit %s in repo %s' % (git_reference, target_dir))

    cmd = ["git", "rev-parse", "HEAD"]
    logger.debug("getting SHA-1 of provided ref '%s'", git_reference)
    commit_id = subprocess.check_output(cmd, cwd=target_dir, universal_newlines=True)
    commit_id = commit_id.strip()
    logger.info("commit ID = %s", commit_id)

    final_commit_depth = None
    if not deepen:
        cmd = ['git', 'rev-list', '--count', 'HEAD']
        final_commit_depth = int(subprocess.check_output(cmd, cwd=target_dir)) - base_commit_depth

    return commit_id, final_commit_depth


@contextlib.contextmanager
def paused_builds(osbs, quota_name=None, ignore_quota_errors=False):
    try:
        logger.info("pausing builds")
        try:
            osbs.pause_builds(quota_name=quota_name)
        except OsbsResponseException as e:
            if ignore_quota_errors and (e.status_code == requests.codes.FORBIDDEN):
                logger.warning("Ignoring resourcequota error")
            else:
                raise
        yield osbs
    finally:
        logger.info("resuming builds")
        try:
            osbs.resume_builds(quota_name=quota_name)
        except OsbsResponseException as e:
            if ignore_quota_errors and (e.status_code == requests.codes.FORBIDDEN):
                logger.warning("Ignoring resourcequota error")
            else:
                raise


def looks_like_git_hash(git_ref):
    return all(ch in string.hexdigits for ch in git_ref) and len(git_ref) == 40


def get_repo_info(git_uri, git_ref, git_branch=None, depth=None):
    with checkout_git_repo(git_uri, commit=git_ref, branch=git_branch,
                           depth=depth) as code_dir_info:
        code_dir = code_dir_info.repo_path
        depth = code_dir_info.commit_depth
        dfp = DockerfileParser(os.path.join(code_dir), cache_content=True)
        config = RepoConfiguration(dir_path=code_dir, depth=depth)
        tags_config = AdditionalTagsConfig(dir_path=code_dir,
                                           tags=config.container.get('tags', set()))
    return RepoInfo(dfp, config, tags_config)


def git_repo_humanish_part_from_uri(git_uri):
    git_uri = git_uri.rstrip('/')
    if git_uri.endswith("/.git"):
        git_uri = git_uri[:-5]
    elif git_uri.endswith(".git"):
        git_uri = git_uri[:-4]

    return os.path.basename(git_uri)


def strip_registry_from_image(image):
    # this duplicates some logic with atomic_reactor.util.ImageName,
    # but I don't think it's worth it to depend on AR just for this
    ret = image
    parts = image.split('/', 2)
    if len(parts) == 2:
        if '.' in parts[0] or ':' in parts[0]:
            ret = parts[1]
    elif len(parts) == 3:
        ret = '%s/%s' % (parts[1], parts[2])
    return ret


def strip_registry_and_tag_from_image(image):
    image_with_tag = strip_registry_from_image(image)
    parts = image_with_tag.split(':')
    return parts[0]


def get_imagestreamtag_from_image(image):
    """
    return ImageStreamTag, give a FROM value

    :param image: str, the FROM value from the Dockerfile
    :return: str, ImageStreamTag
    """
    ret = image

    # Remove the registry part
    ret = strip_registry_from_image(image)

    # ImageStream names cannot contain '/'
    ret = ret.replace('/', '-')

    # If there is no ':' suffix value, add one
    if ret.find(':') == -1:
        ret += ":latest"

    return ret


def get_time_from_rfc3339(rfc3339):
    """
    return time tuple from an RFC 3339-formatted time string

    :param rfc3339: str, time in RFC 3339 format
    :return: float, seconds since the Epoch
    """

    try:
        # py 3

        dt = dateutil.parser.parse(rfc3339, ignoretz=False)
        return dt.timestamp()
    except NameError:
        # py 2

        # Decode the RFC 3339 date with no fractional seconds (the
        # format Origin provides). Note that this will fail to parse
        # valid ISO8601 timestamps not in this exact format.
        time_tuple = strptime(rfc3339, '%Y-%m-%dT%H:%M:%SZ')
        return timegm(time_tuple)


def utcnow():
    """
    Return current time in UTC.

    This function is created to make mocking in unit tests easier.
    """
    return datetime.utcnow()


VALID_BUILD_CONFIG_NAME_CHARS = re.compile('[-a-z0-9]')
VALID_LABEL_CHARS = re.compile(r'[-a-z0-9\.]')
LABEL_MAX_CHARS = 63


def sanitize_strings_for_openshift(str1, str2='', limit=LABEL_MAX_CHARS, separator='-',
                                   label=True):
    """
    OpenShift requires labels to be no more than 64 characters and forbids any characters other
    than alphanumerics, ., and -. BuildConfig names are similar, but cannot contain /.

    Sanitize and concatanate one or two strings to meet OpenShift's requirements. include an
    equal number of characters from both strings if the combined length is more than the limit.
    """
    filter_chars = VALID_LABEL_CHARS if label else VALID_BUILD_CONFIG_NAME_CHARS
    str1_san = ''.join(filter(filter_chars.match, list(str1)))
    str2_san = ''.join(filter(filter_chars.match, list(str2)))

    str1_chars = []
    str2_chars = []
    groups = ((str1_san, str1_chars), (str2_san, str2_chars))

    size = len(separator)
    limit = min(limit, LABEL_MAX_CHARS)

    for i in range(max(len(str1_san), len(str2_san))):
        for group, group_chars in groups:
            if i < len(group):
                group_chars.append(group[i])
                size += 1
                if size >= limit:
                    break
        else:
            continue
        break

    final_str1 = ''.join(str1_chars).strip(separator)
    final_str2 = ''.join(str2_chars).strip(separator)
    return separator.join(filter(None, (final_str1, final_str2)))


def make_name_from_git(repo, branch, limit=53, separator='-', hash_size=5):
    """
    return name string representing the given git repo and branch
    to be used as a build name.

    NOTE: Build name will be used to generate pods which have a
    limit of 64 characters and is composed as:

        <buildname>-<buildnumber>-<podsuffix>
        rhel7-1-build

    Assuming '-XXXX' (5 chars) and '-build' (6 chars) as default
    suffixes, name should be limited to 53 chars (64 - 11).

    OpenShift is very peculiar in which BuildConfig names it
    allows. For this reason, only certain characters are allowed.
    Any disallowed characters will be removed from repo and
    branch names.

    :param repo: str, the git repository to be used
    :param branch: str, the git branch to be used
    :param limit: int, max name length
    :param separator: str, used to separate the repo and branch in name

    :return: str, name representing git repo and branch.
    """

    branch = branch or 'unknown'
    full = urlparse(repo).path.lstrip('/') + branch
    repo = git_repo_humanish_part_from_uri(repo)
    shaval = sha256(full.encode('utf-8')).hexdigest()
    hash_str = shaval[:hash_size]
    limit = limit - len(hash_str) - 1

    sanitized = sanitize_strings_for_openshift(repo, branch, limit, separator, False)
    return separator.join(filter(None, (sanitized, hash_str)))


def wrap_name_from_git(prefix, suffix, *args, **kwargs):
    """
    wraps the result of make_name_from_git in a suffix and postfix
    adding separators for each.

    see docstring for make_name_from_git for a full list of parameters
    """
    # 64 is maximum length allowed by OpenShift
    # 2 is the number of dashes that will be added
    prefix = ''.join(filter(VALID_BUILD_CONFIG_NAME_CHARS.match, list(prefix)))
    suffix = ''.join(filter(VALID_BUILD_CONFIG_NAME_CHARS.match, list(suffix)))
    kwargs['limit'] = kwargs.get('limit', 64) - len(prefix) - len(suffix) - 2
    name_from_git = make_name_from_git(*args, **kwargs)
    return '-'.join([prefix, name_from_git, suffix])


def get_instance_token_file_name(instance):
    """Return the token file name for the given instance."""
    return '{}/.osbs/{}.token'.format(os.path.expanduser('~'), instance)


def sanitize_version(version):
    """
    Take parse_version() output and standardize output from older
    setuptools' parse_version() to match current setuptools.
    """
    if hasattr(version, 'base_version'):
        if version.base_version:
            parts = version.base_version.split('.')
        else:
            parts = []
    else:
        parts = []
        for part in version:
            if part.startswith('*'):
                break
            parts.append(part)
    parts = [int(p) for p in parts]

    if len(parts) < 3:
        parts += [0] * (3 - len(parts))

    major, minor, micro = parts[:3]
    cleaned_version = '{}.{}.{}'.format(major, minor, micro)
    return cleaned_version


class Labels(object):
    """
    Provide access to a set of labels which have specific semantics

    The set of labels comes from here:
    https://github.com/projectatomic/ContainerApplicationGenericLabels

    Note that only a subset of label types (those used by OSBS) is supported:

    - LABEL_TYPE_NAME: repository name of the image
    - LABEL_TYPE_VERSION: version of the image
    - LABEL_TYPE_RELEASE: release number for this version
    - LABEL_TYPE_ARCH: architecture for the image
    - LABEL_TYPE_VENDOR: owner of the image
    - LABEL_TYPE_SOURCE: authoritative location for publishing
    - LABEL_TYPE_COMPONENT: Bugzilla (or other tracker) component
    - LABEL_TYPE_HOST: build host used to create the image
    - LABEL_TYPE_RUN: command to run the image
    - LABEL_TYPE_INSTALL: command to install the image
    - LABEL_TYPE_UNINSTALL: command to uninstall the image
    - LABEL_TYPE_OPERATOR_MANIFESTS: flags the presence of operators metadata
    """
    LABEL_TYPE_NAME = object()
    LABEL_TYPE_VERSION = object()
    LABEL_TYPE_RELEASE = object()
    LABEL_TYPE_ARCH = object()
    LABEL_TYPE_VENDOR = object()
    LABEL_TYPE_SOURCE = object()
    LABEL_TYPE_COMPONENT = object()
    LABEL_TYPE_HOST = object()
    LABEL_TYPE_RUN = object()
    LABEL_TYPE_INSTALL = object()
    LABEL_TYPE_UNINSTALL = object()
    LABEL_TYPE_OPERATOR_MANIFESTS = object()
    LABEL_NAMES = {
        LABEL_TYPE_NAME: ('name', 'Name'),
        LABEL_TYPE_VERSION: ('version', 'Version'),
        LABEL_TYPE_RELEASE: ('release', 'Release'),
        LABEL_TYPE_ARCH: ('architecture', 'Architecture'),
        LABEL_TYPE_VENDOR: ('vendor', 'Vendor'),
        LABEL_TYPE_SOURCE: ('authoritative-source-url', 'Authoritative_Registry'),
        LABEL_TYPE_COMPONENT: ('com.redhat.component', 'BZComponent'),
        LABEL_TYPE_HOST: ('com.redhat.build-host', 'Build_Host'),
        LABEL_TYPE_RUN: ('run', 'RUN'),
        LABEL_TYPE_INSTALL: ('install', 'INSTALL'),
        LABEL_TYPE_UNINSTALL: ('uninstall', 'UNINSTALL'),
        LABEL_TYPE_OPERATOR_MANIFESTS: ('com.redhat.delivery.appregistry',)
    }

    def __init__(self, df_labels):
        """
        Create a new Labels object
        providing access to actual newest labels as well as old ones
        """
        self._df_labels = df_labels
        self._label_values = {}
        for label_type, label_names in Labels.LABEL_NAMES.items():
            for lbl_name in label_names:
                if lbl_name in df_labels:
                    self._label_values[label_type] = (lbl_name, df_labels[lbl_name])
                    break

    def get_name(self, label_type):
        """
        returns the most preferred label name
        if there isn't any correct name in the list
        it will return newest label name
        """
        if label_type in self._label_values:
            return self._label_values[label_type][0]
        else:
            return Labels.LABEL_NAMES[label_type][0]

    @staticmethod
    def get_new_names_by_old():
        """Return dictionary, new label name indexed by old label name."""
        newdict = {}

        for label_type, label_names in Labels.LABEL_NAMES.items():
            for oldname in label_names[1:]:
                newdict[oldname] = Labels.LABEL_NAMES[label_type][0]
        return newdict

    def get_name_and_value(self, label_type):
        """
        Return tuple of (label name, label value)
        Raises KeyError if label doesn't exist
        """
        if label_type in self._label_values:
            return self._label_values[label_type]
        else:
            return (label_type, self._df_labels[label_type])


def retry_on_conflict(func):
    @wraps(func)
    def retry(*args, **kwargs):
        # Only retry when OsbsResponseException was raised due to a conflict
        def should_retry_cb(ex):
            return ex.status_code == http_client.CONFLICT

        retry_func = RetryFunc(OsbsResponseException, should_retry_cb=should_retry_cb)
        return retry_func.go(func, *args, **kwargs)

    return retry


def retry_on_exception(exception_type):
    def do_retry_on_exception(func):
        @wraps(func)
        def retry(*args, **kwargs):
            return RetryFunc(exception_type).go(func, *args, **kwargs)

        return retry

    return do_retry_on_exception


class RetryFunc(object):
    def __init__(self, exception_type, should_retry_cb=None):
        self.exception_type = exception_type
        self.should_retry_cb = should_retry_cb or (lambda ex: True)

        self.retry_times = OS_CONFLICT_MAX_RETRIES
        self.retry_delay = OS_CONFLICT_WAIT

    def go(self, func, *args, **kwargs):
        for counter in range(self.retry_times + 1):
            try:
                return func(*args, **kwargs)
            except self.exception_type as ex:
                if self.should_retry_cb(ex) and counter != self.retry_times:
                    logger.info("retrying on exception: %s", ex.message)
                    logger.debug("attempt %d to call %s", counter + 1, func.__name__)
                    time.sleep(self.retry_delay * (2 ** counter))
                else:
                    raise
