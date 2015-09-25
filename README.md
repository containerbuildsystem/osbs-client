# OSBS

[![Code Health](https://landscape.io/github/projectatomic/osbs-client/master/landscape.svg?style=flat)](https://landscape.io/github/projectatomic/osbs-client/master)
[![Build Status](https://travis-ci.org/projectatomic/osbs-client.svg?branch=master)](https://travis-ci.org/projectatomic/osbs-client)
[![Coverage
Status](https://coveralls.io/repos/projectatomic/osbs-client/badge.svg?branch=master&service=github)](https://coveralls.io/github/projectatomic/osbs-client?branch=master)

Python module and command line client for OpenShift Build Service.

It is able to query OpenShift v3 for various stuff related to building images. It can initiate builds, list builds, get info about builds, get build logs... All of this can be done from command line and from python.

## Getting Started

We have [a guide](https://github.com/projectatomic/osbs-client/blob/master/docs/development-setup.md) how to setup OpenShift in a docker container so you can try it out.


## Configuration

You should set up a configuration file for your instance, sample:

```
[general]
build_json_dir = /usr/share/osbs/

[default]
openshift_uri = https://host:8443/
# if you want to get packages from koji (koji plugin in dock)
# you need to setup koji hub and root
# this sample is for fedora
koji_root = http://koji.fedoraproject.org/
koji_hub = http://koji.fedoraproject.org/kojihub
# in case of using artifacts plugin, you should provide a command
# how to fetch artifacts
sources_command = fedpkg sources
# from where should be images pulled and where should be pushed?
registry_uri = your.registry.example.com
verify_ssl = false
build_type = simple
```

## Issuing a build

This is how simple build could look:
```
$ osbs build -g http://path.to.gitrepo.with.dockerfile/ -c image-name -u your-nick
```

## Deploying OpenShift Build System

We have [documentation](https://github.com/projectatomic/osbs-client/blob/master/docs/osbs_instance_setup.md) how you can setup your own instance.
