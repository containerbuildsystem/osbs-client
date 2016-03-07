"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

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
from collections import namedtuple
from datetime import datetime
from io import BytesIO

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
from osbs.exceptions import OsbsException

logger = logging.getLogger(__name__)

class RegistryURI(object):
    # Group 0: URI without path -- allowing empty value -- including:
    # - Group 1: optional 'http://' / 'https://'
    # - Group 2: hostname and port
    # Group 3: path, including:
    # - Group 4: optional API version, 'v' followed by a number
    versionre = re.compile(r'((https?://)?([^/]*))(/(v\d+))?$')

    def __init__(self, uri):
        groups = self.versionre.match(uri).groups()
        self.docker_uri = groups[2]
        self.version = groups[4] or 'v1'
        self.scheme = groups[1] or ''

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
        return self.next()

    def next(self):
        ti = self.tarfile.next()

        if ti is None:
            self.close()
            raise StopIteration()

        return self.TarFile(ti.name, self.tarfile.extractfile(ti))

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


def buildconfig_update(orig, new, remove_nonexistent_keys=False):
    """Performs update of given `orig` BuildConfig with values from `new` BuildConfig.
    Both BuildConfigs have to be represented as `dict`s.

    This function:
    - adds all key/value pairs to `orig` from `new` that are missing
    - replaces values in `orig` for keys that are in both
    - removes key/value pairs from `orig` for keys that are not in `new`,
      but only in dicts nested inside `strategy` key
      (see https://github.com/projectatomic/osbs-client/pull/273#issuecomment-148038314)
    """
    if isinstance(orig, dict) and isinstance(new, dict):
        if remove_nonexistent_keys:
            missing = set(orig.keys()) - set(new.keys())
            for k in missing:
                orig.pop(k)
        for k, v in new.items():
            if k == 'strategy':
                remove_nonexistent_keys = True
            if isinstance(orig.get(k, None), dict) and isinstance(v, dict):
                buildconfig_update(orig[k], v, remove_nonexistent_keys)
            else:
                orig[k] = v


@contextlib.contextmanager
def checkout_git_repo(git_uri, git_ref, git_branch=None):
    tmpdir = tempfile.mkdtemp()
    repo_path = os.path.join(tmpdir, "repo")
    try:
        try:
            # when you clone into an empty directory and cloning process fails
            # git will remove the empty directory (why?!)
            args = ['git', 'clone', git_uri]
            if git_branch:
                args += ['-b', git_branch]

            args.append(repo_path)
            run_command(args)
        except OsbsException as ex:
            raise OsbsException("Unable to clone git repo '%s' "
                                "branch '%s'" % (git_uri, git_branch),
                                cause=ex, traceback=sys.exc_info()[2])

        # Find the specific ref we want
        try:
            run_command(['git', 'reset', '--hard', git_ref], cwd=repo_path)
        except OsbsException as ex:
            raise OsbsException("Unable to reset branch to '%s'" % git_ref,
                                cause=ex, traceback=sys.exc_info()[2])

        yield repo_path

    finally:
        shutil.rmtree(tmpdir)


@contextlib.contextmanager
def paused_builds(osbs, quota_name=None):
    try:
        logger.info("pausing builds")
        osbs.pause_builds(quota_name=quota_name)
        yield osbs
    finally:
        logger.info("resuming builds")
        osbs.resume_builds(quota_name=quota_name)


def looks_like_git_hash(git_ref):
    return all(ch in string.hexdigits for ch in git_ref) and len(git_ref) == 40


def get_df_parser(git_uri, git_ref, git_branch=None):
    with checkout_git_repo(git_uri, git_ref, git_branch) as code_dir:
        dfp = DockerfileParser(os.path.join(code_dir), cache_content=True)
    return dfp


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


def run_command(*popenargs, **kwargs):
    """
    Run command with arguments and return its output as a byte string.

    This is originally taken from subprocess.py of python 2.7
    """
    process = subprocess.Popen(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               *popenargs, **kwargs)
    output, _ = process.communicate()
    retcode = process.poll()
    if retcode:
        cmd = kwargs.get("args")
        if cmd is None:
            cmd = popenargs[0]
        raise OsbsException(
            message="Command %s returned %s\n\n%s" % (cmd, retcode, output)
        )
    return output
