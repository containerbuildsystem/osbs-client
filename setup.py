#!/usr/bin/python
"""
Copyright (c) 2015, 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import re

from setuptools import setup, find_packages


def _get_requirements(path):
    try:
        with open(path) as f:
            packages = f.read().splitlines()
    except (IOError, OSError) as ex:
        raise RuntimeError("Can't open file with requirements: %s", repr(ex))
    return [p.strip() for p in packages if not re.match(r"^\s*#", p)]


setup(
    name="osbs-client",
    description='Python module and command line client for OpenShift Build Service',
    version="2.1.0",
    author='Red Hat, Inc.',
    author_email='atomic-devel@projectatomic.io',
    url='https://github.com/containerbuildsystem/osbs-client',
    license="BSD",
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    entry_points={
          'console_scripts': ['osbs=osbs.cli.main:main'],
    },
    install_requires=_get_requirements('requirements.txt'),
    package_data={'osbs': ['schemas/*.json']},
    setup_requires=[],
    tests_require=_get_requirements('tests/requirements.txt'),
    python_requires='>=3.6',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
)
