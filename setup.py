#!/usr/bin/python
"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import re
import sys
import glob

from setuptools import setup, find_packages

data_files = {
    "share/osbs": glob.glob("inputs/*.json"),
}

def _get_requirements(path):
    try:
        with open(path) as f:
            packages = f.read().splitlines()
    except (IOError, OSError) as ex:
        raise RuntimeError("Can't open file with requirements: %s", repr(ex))
    return [p.strip() for p in packages if not re.match(r"^\s*#", p)]

def _install_requirements():
    requirements = _get_requirements('requirements.txt')
    if sys.version_info[0] >= 3:
        requirements += _get_requirements('requirements-py3.txt')
    return requirements

setup(
    name="osbs-client",
    description='Python module and command line client for OpenShift Build Service',
    version="0.36",
    author='Red Hat, Inc.',
    author_email='atomic-devel@projectatomic.io',
    url='https://github.com/projectatomic/osbs-client',
    license="BSD",
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    entry_points={
          'console_scripts': ['osbs=osbs.cli.main:main'],
    },
    install_requires=_install_requirements(),
    data_files=data_files.items(),
    setup_requires=[],
    tests_require=_get_requirements('tests/requirements.txt'),
)
