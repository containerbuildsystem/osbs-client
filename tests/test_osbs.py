"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals

import os
import re
import inspect


def test_if_all_versions_match():
    def read_version(fp, regex):
        with open(fp, "r") as fd:
            content = fd.read()
            found = re.findall(regex, content)
            if len(found) == 1:
                return found[0]
            else:
                raise Exception("Version not found!")
    import osbs
    from osbs import __version__
    if __version__.endswith('.dev'):
        version_without_dev = __version__[:-4]
    else:
        version_without_dev = __version__

    fp = inspect.getfile(osbs)
    project_dir = os.path.dirname(os.path.dirname(fp))
    specfile = os.path.join(project_dir, "osbs-client.spec")
    setup_py = os.path.join(project_dir, "setup.py")
    spec_version = read_version(specfile, r"\nVersion:\s*(.+?)\s*\n")
    setup_py_version = read_version(setup_py, r"version=['\"](.+)['\"]")
    assert spec_version == version_without_dev
    assert setup_py_version == __version__
