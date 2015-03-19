#!/usr/bin/python

import re

from setuptools import setup, find_packages

data_files = {
    "share/osbs": [
        "inputs/prod.json",
        "inputs/prod_inner.json",
    ],
}

def _get_requirements(path):
    try:
        with open(path) as f:
            packages = f.read().splitlines()
    except (IOError, OSError) as ex:
        raise RuntimeError("Can't open file with requirements: %s", repr(ex))
    packages = [p.strip() for p in packages if not re.match("^\s*#", p)]
    return packages

def _install_requirements():
    requirements = _get_requirements('requirements.txt')
    return requirements

setup(
    name="osbs",
    description='Python module and command line client for OpenShift Build Service',
    version="0.1",
    author='Tomas Tomecek',
    author_email='ttomecek@redhat.com',
    url='https://github.com/DBuildService/osbs',
    license="BSD",
    packages=find_packages(exclude=["tests"]),
    entry_points={
          'console_scripts': ['osbs=osbs.cli.main:run'],
    },
    install_requires=_install_requirements(),
    data_files=data_files.items(),
)
