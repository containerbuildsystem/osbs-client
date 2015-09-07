"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import copy
import os
import subprocess
import sys
import tempfile
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


def checkout_git_repo(git_uri, git_ref, git_branch):
    tmpdir = tempfile.mkdtemp()
    try:
        subprocess.check_call(['git', 'clone', git_uri,
                               '-b', git_branch, tmpdir],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as ex:
        raise OsbsException("Unable to clone git repo '%s' "
                            "branch '%s'" % (git_uri, git_branch),
                            cause=ex, traceback=sys.exc_info()[2])

    # Find the specific ref we want
    try:
        subprocess.check_call(['git', 'reset', '--hard', git_ref],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              cwd=tmpdir)
    except subprocess.CalledProcessError as ex:
        raise OsbsException("Unable to reset branch to '%s'" % git_ref,
                            cause=ex, traceback=sys.exc_info()[2])

    return tmpdir


def get_df_parser(git_uri, git_ref, git_branch):
    code_dir = checkout_git_repo(git_uri, git_ref, git_branch)
    return DockerfileParser(os.path.join(code_dir, 'Dockerfile'))


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

def get_time_from_rfc3399(rfc3399):
    """
    return time tuple from an RFC 3399-formatted time string

    :param rfc3399: str, time in RFC 3399 format
    :return: float, seconds since the Epoch
    """

    try:
        # Decode the RFC 3399 date with no fractional seconds
        # (the format Origin provides)
        time_tuple = strptime(rfc3399, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise RuntimeError("Time format not understood: %s" % rfc3399)

    return timegm(time_tuple)
