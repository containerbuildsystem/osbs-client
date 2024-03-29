"""
Copyright (c) 2015-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals
from functools import wraps
import contextlib
import json
import logging
import os
import os.path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from datetime import datetime
from hashlib import sha256
from osbs.repo_utils import RepoConfiguration, RepoInfo, AdditionalTagsConfig
from osbs.constants import (OS_CONFLICT_MAX_RETRIES, OS_CONFLICT_WAIT,
                            GIT_MAX_RETRIES, GIT_BACKOFF_FACTOR, GIT_FETCH_RETRY,
                            USER_WARNING_LEVEL, USER_WARNING_LEVEL_NAME, RAND_DIGITS)

# This was moved to a separate file - import here for external API compatibility
from osbs.utils.labels import Labels  # noqa: F401

from six.moves import http_client
from six.moves.urllib.parse import urlparse

from dockerfile_parse import DockerfileParser
from osbs.exceptions import (OsbsException, OsbsResponseException,
                             OsbsValidationException, OsbsCommitNotFound, OsbsLocallyModified)

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
        match = self.versionre.match(uri)
        if not match:
            raise ValueError('Invalid registry URI {}'.format(uri))
        groups = match.groups()
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
            try:
                repo_commit, repo_depth = reset_git_repo(target_dir, commit, depth)
            except OsbsCommitNotFound as exc:
                raise OsbsCommitNotFound("Commit {} is not reachable in branch {}, reason: {}"
                                         .format(commit, branch, exc))
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
    cmd = ["git", "status"]
    logger.debug("Checking if the repo is modified locally: '%s'", cmd)
    output = subprocess.check_output(cmd, cwd=target_dir, stderr=subprocess.STDOUT)
    if "nothing to commit" not in str(output):
        raise OsbsLocallyModified("'{}' source is locally modified: '{}'".format(target_dir,
                                                                                 output))
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
                raise OsbsCommitNotFound('cannot find commit {} in repo {}'.format(
                                         git_reference, target_dir))
            deepen *= 2
            cmd = ["git", "fetch", "--depth", str(deepen)]
            subprocess.check_call(cmd, cwd=target_dir)
            logger.debug("Couldn't find commit %s, increasing depth with '%s'", git_reference,
                         cmd)
    else:
        raise OsbsCommitNotFound('cannot find commit {} in repo {}'.format(
                                  git_reference, target_dir))

    logger.debug("getting SHA-1 of provided ref '%s'", git_reference)
    commit_id = get_commit_id(target_dir)
    logger.info("commit ID = %s", commit_id)

    final_commit_depth = None
    if not deepen:
        cmd = ['git', 'rev-list', '--count', 'HEAD']
        final_commit_depth = int(subprocess.check_output(cmd, cwd=target_dir)) - base_commit_depth

    return commit_id, final_commit_depth


def get_commit_id(repo_dir: str) -> str:
    cmd = ["git", "rev-parse", "HEAD"]
    commit_id = subprocess.check_output(cmd, cwd=repo_dir, universal_newlines=True).strip()
    return commit_id


def get_repo_info(git_uri, git_ref, git_branch=None, depth=None):
    with checkout_git_repo(git_uri, commit=git_ref, branch=git_branch,
                           depth=depth) as code_dir_info:
        code_dir = code_dir_info.repo_path
        depth = code_dir_info.commit_depth
        dfp = DockerfileParser(os.path.join(code_dir), cache_content=True)
        config = RepoConfiguration(git_uri=git_uri, git_ref=git_ref, git_branch=git_branch,
                                   dir_path=code_dir, depth=depth)
        tags_config = AdditionalTagsConfig(dir_path=code_dir,
                                           tags=config.container.get('tags', set()))
    repo_info = RepoInfo(dfp, config, tags_config)
    return repo_info


def git_repo_humanish_part_from_uri(git_uri):
    git_uri = git_uri.rstrip('/')
    if git_uri.endswith("/.git"):
        git_uri = git_uri[:-5]
    elif git_uri.endswith(".git"):
        git_uri = git_uri[:-4]

    return os.path.basename(git_uri)


def utcnow():
    """
    Return current time in UTC.

    This function is created to make mocking in unit tests easier.
    """
    return datetime.utcnow()


VALID_BUILD_CONFIG_NAME_CHARS = re.compile('[-a-z0-9]')
VALID_LABEL_CHARS = re.compile(r'[-a-z0-9_\.]')
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


def make_name_from_git(repo, branch, limit=43, separator='-', hash_size=5):
    """
    return name string representing the given git repo and branch
    to be used as a pipeline run name.

    NOTE: Pipeline run name has a limit of 63 characters

    We will also add random postfix generated by generate_random_postfix,
    which contains 20 characters, therefore the default limit is set to 43 (63 - 20).

    OpenShift is very peculiar in which Object names it
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
    name_from_git = separator.join(filter(None, (sanitized, hash_str)))
    return name_from_git + generate_random_postfix()


def get_instance_token_file_name(instance):
    """Return the token file name for the given instance."""
    return '{}/.osbs/{}.token'.format(os.path.expanduser('~'), instance)


def retry_on_conflict(func):
    @wraps(func)
    def retry(*args, **kwargs):
        # Only retry when OsbsResponseException was raised due to a conflict
        def should_retry_cb(ex):
            return ex.status_code == http_client.CONFLICT

        retry_func = RetryFunc(OsbsResponseException, should_retry_cb=should_retry_cb)
        return retry_func.go(func, *args, **kwargs)

    return retry


def user_warning_log_handler(self, message):
    """
    Take arguments to transform them into JSON data
    and send them into the logger with USER_WARNING level
    """
    assert isinstance(message, str)

    content = {
        'message': message,
    }
    msg = json.dumps(content)
    self._log(USER_WARNING_LEVEL, msg, None)


class RetryFunc(object):
    def __init__(self, exception_type, should_retry_cb=None,
                 retry_times=OS_CONFLICT_MAX_RETRIES, retry_delay=OS_CONFLICT_WAIT):
        self.exception_type = exception_type
        self.should_retry_cb = should_retry_cb or (lambda ex: True)

        self.retry_times = retry_times
        self.retry_delay = retry_delay

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


class ImageName(object):
    """Represent an image.

    Naming Conventions
    ==================
    registry.somewhere/namespace/image_name:tag
    |-----------------|                          registry, reg_uri
                      |---------|                namespace
    |--------------------------------------|     repository
                      |--------------------|     image name
                                            |--| tag
                      |------------------------| image
    |------------------------------------------| image
    """

    def __init__(self, registry=None, namespace=None, repo=None, tag=None):
        self.registry = registry
        self.namespace = namespace
        self.repo = repo
        self.tag = tag or 'latest'

    @classmethod
    def parse(cls, image_name):
        result = cls()

        if isinstance(image_name, cls):
            logger.debug("Attempting to parse ImageName %s as an ImageName", image_name)
            return image_name

        # registry.org/namespace/repo:tag
        s = image_name.split('/', 2)

        if len(s) == 2:
            if '.' in s[0] or ':' in s[0]:
                result.registry = s[0]
            else:
                result.namespace = s[0]
        elif len(s) == 3:
            result.registry = s[0]
            result.namespace = s[1]
        result.repo = s[-1]

        for sep in '@:':
            try:
                result.repo, result.tag = result.repo.rsplit(sep, 1)
            except ValueError:
                continue
            break

        return result

    def to_str(self, registry=True, tag=True, explicit_tag=False,
               explicit_namespace=False):
        if self.repo is None:
            raise RuntimeError('No image repository specified')

        result = self.get_repo(explicit_namespace)

        if tag and self.tag and ':' in self.tag:
            result = '{0}@{1}'.format(result, self.tag)
        elif tag and self.tag:
            result = '{0}:{1}'.format(result, self.tag)
        elif tag and explicit_tag:
            result = '{0}:{1}'.format(result, 'latest')

        if registry and self.registry:
            result = '{0}/{1}'.format(self.registry, result)

        return result

    def get_repo(self, explicit_namespace=False):
        result = self.repo
        if self.namespace:
            result = '{0}/{1}'.format(self.namespace, result)
        elif explicit_namespace:
            result = '{0}/{1}'.format('library', result)
        return result

    def enclose(self, organization):
        if self.namespace == organization:
            return

        repo_parts = [self.repo]
        if self.namespace:
            repo_parts.insert(0, self.namespace)

        self.namespace = organization
        self.repo = '-'.join(repo_parts)

    def __str__(self):
        return self.to_str(registry=True, tag=True)

    def __repr__(self):
        return (
            "ImageName(registry={s.registry!r}, namespace={s.namespace!r},"
            " repo={s.repo!r}, tag={s.tag!r})"
        ).format(s=self)

    def __eq__(self, other):
        return (type(self) == type(other) and    # pylint: disable=unidiomatic-typecheck
                self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.to_str())

    def copy(self):
        return ImageName(
            registry=self.registry,
            namespace=self.namespace,
            repo=self.repo,
            tag=self.tag)


class UserWarningsStore(object):
    def __init__(self):
        # (asctime (platform:arch)? - name) - levelname - message
        self.regex = r' - '.join((r'^.+', USER_WARNING_LEVEL_NAME, r'(\{.*\})$'))
        self._user_warnings = set()

    def is_user_warning(self, line):
        return re.match(self.regex, line)

    def store(self, line):
        """
        Extract data from given log record with USER_WARNING level
        and store an understandable message in set
        """
        data_search = re.search(self.regex, line)
        if not data_search:
            message = 'Incorrect given logline for storing user warnings: %s'
            logger.error(message, line)
            return

        try:
            data = json.loads(data_search.group(1))
        except ValueError:
            message = 'Incorrect JSON data input for a user warning: %s'
            logger.error(message, data_search.group(1))
            return

        message = data['message']

        self._user_warnings.add(message)

    def __iter__(self):
        for user_warning in self._user_warnings:
            yield user_warning

    def __str__(self):
        return '\n'.join(self._user_warnings)

    def __len__(self):
        return len(self._user_warnings)

    def __bool__(self):
        return bool(self._user_warnings)


def generate_random_postfix():
    timestamp = utcnow().strftime('%Y%m%d%H%M%S')
    # RNG is seeded once its imported, so in cli calls scratch builds would get unique name.
    # On brew builders we import osbs once - thus RNG is seeded once and `randrange`
    # returns the same values throughout the life of the builder.
    # Before each `randrange` call we should be calling `.seed` to prevent this
    random.seed()

    postfix_segments = [
        str(random.randrange(10**(RAND_DIGITS - 1), 10**RAND_DIGITS)),
        timestamp
    ]

    postfix = '-'.join(postfix_segments)
    return postfix
