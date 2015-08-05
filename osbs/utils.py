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
import tempfile

from dockerfile_parse import DockerfileParser


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


def checkout_git_repo(uri, commit):
    tmpdir = tempfile.mkdtemp()
    subprocess.check_call(['git', 'clone', uri, '-b', commit, tmpdir], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    return tmpdir


def get_df_parser(git_uri, git_ref):
    code_dir = checkout_git_repo(git_uri, git_ref)
    return DockerfileParser(os.path.join(code_dir, 'Dockerfile'))


def get_imagestream_name_from_image(image):
    # this duplicates some logic with atomic_reactor.util.ImageName,
    # but I don't think it's worth it to depend on AR just for this
    ret = image
    parts = image.split('/', 2)
    if len(parts) == 2:
        if '.' in parts[0] or ':' in parts[0]:
            ret = parts[1]
    elif len(parts) == 3:
        ret = '%s/%s' % (parts[1], parts[2])
    if ':' in ret:
        ret = ret[:ret.index(':')]

    return ret
