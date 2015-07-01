# OSBS

[![Code Health](https://landscape.io/github/DBuildService/osbs-client/master/landscape.svg?style=flat)](https://landscape.io/github/DBuildService/osbs-client/master)
[![Build Status](https://travis-ci.org/DBuildService/osbs-client.svg?branch=master)](https://travis-ci.org/DBuildService/osbs-client)

Python module and command line client for OpenShift Build Service.

It is able to query OpenShift v3 for various stuff related to building images. It can initiate builds, list builds, get info about builds, get build logs... All of this can be done from command line and from python.

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

We have [documentation](https://github.com/DBuildService/osbs-client/blob/master/docs/osbs_instance_setup.md) how you can setup your own instance.
