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


def get_base_image(repo_dir):
    df_path = os.path.join(repo_dir, 'Dockerfile')
    df = DockerfileParser(df_path)
    return df.baseimage
