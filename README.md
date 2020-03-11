# OSBS

[![Build Status](https://travis-ci.org/containerbuildsystem/osbs-client.svg?branch=master)](https://travis-ci.org/containerbuildsystem/osbs-client)
[![Coverage
Status](https://coveralls.io/repos/containerbuildsystem/osbs-client/badge.svg?branch=master&service=github)](https://coveralls.io/github/containerbuildsystem/osbs-client?branch=master)

Python module and command line client for OpenShift Build Service.

It is able to query OpenShift v3 for various stuff related to building images. It can initiate builds, list builds, get info about builds, get build logs and so on. All of this can be done by the OSBS site administrator from command line and from python. Regular users submitting builds can interact with this system using Koji, as described in the guide linked below.

## Getting Started

We have [a guide](https://github.com/containerbuildsystem/osbs-client/blob/master/docs/development-setup.md) how to setup whole build system for local development.


## Deploying OpenShift Build System

We have [documentation](https://github.com/containerbuildsystem/osbs-client/blob/master/docs/osbs_instance_setup.md) how you can setup your own instance.

## Contributing

If you would like to help out, that's great! Please read the [contribution guide](https://github.com/containerbuildsystem/osbs-client/blob/master/CONTRIBUTING.md).
