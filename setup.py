#!/usr/bin/python
"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import re

from setuptools import setup, find_packages

data_files = {
    "share/osbs": [
        "inputs/prod.json",
        "inputs/prod_inner.json",
        "inputs/prod-without-koji.json",
        "inputs/prod-without-koji_inner.json",
        "inputs/prod-with-secret.json",
        "inputs/prod-with-secret_inner.json",
        "inputs/simple.json",
        "inputs/simple_inner.json",
    ],
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
    return requirements

setup(
    name="osbs",
    description='Python module and command line client for OpenShift Build Service',
    version="0.13",
    author='Tomas Tomecek',
    author_email='ttomecek@redhat.com',
    url='https://github.com/DBuildService/osbs',
    license="BSD",
    packages=find_packages(exclude=["tests"]),
    entry_points={
          'console_scripts': ['osbs=osbs.cli.main:main'],
    },
    install_requires=_install_requirements(),
    data_files=data_files.items(),
)
