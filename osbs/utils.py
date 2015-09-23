"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import contextlib
import copy
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

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


def graceful_chain_get(d, *args):
    if not d:
        return None
    t = copy.deepcopy(d)
    for arg in args:
        try:
            t = t[arg]
        except (AttributeError, KeyError):
            return None
    return t


def deep_update(orig, new):
    if isinstance(orig, dict) and isinstance(new, dict):
        for k, v in new.items():
            if isinstance(orig.get(k, None), dict) and isinstance(v, dict):
                deep_update(orig[k], v)
            else:
                orig[k] = v


@contextlib.contextmanager
def checkout_git_repo(git_uri, git_ref, git_branch):
    tmpdir = tempfile.mkdtemp()
    repo_path = os.path.join(tmpdir, "repo")
    try:
        try:
            # when you clone into an empty directory and cloning process fails
            # git will remove the empty directory (why?!)
            run_command(['git', 'clone', git_uri, '-b', git_branch, repo_path])
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


def get_df_parser(git_uri, git_ref, git_branch):
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


def get_imagestreamtag_from_image(image):
    """
    return ImageStreamTag, give a FROM value

    :param image: str, the FROM value from the Dockerfile
    :return: str, ImageStreamTag
    """

    # this duplicates some logic with atomic_reactor.util.ImageName,
    # but I don't think it's worth it to depend on AR just for this

    ret = image

    # Remove the registry part
    parts = image.split('/', 2)
    if len(parts) == 2:
        if '.' in parts[0] or ':' in parts[0]:
            ret = parts[1]
    elif len(parts) == 3:
        ret = '%s/%s' % (parts[1], parts[2])

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

