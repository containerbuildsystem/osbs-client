# OSBS

[![code health]][code health link]
[![build status]][build status link]
[![coverage status]][coverage status link]

Python module and command line client for OpenShift Build Service.

It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs and
so on. All of this can be done by the OSBS site administrator from command line
and from python. Regular users submitting builds can interact with this system
using Koji, as described in the guide linked below.

## Getting Started

We have a [development setup guide][] on how to set up a whole build system for
local development.

## Deploying OpenShift Build System

We have an [osbs instance setup guide][] detailing how you can set up your own
instance.

## Contributing

If you would like to help out, that's great! Please read the [contributing
guide][] .

[code health]: https://landscape.io/github/containerbuildsystem/osbs-client/master/landscape.svg?style=flat
[code health link]: https://landscape.io/github/containerbuildsystem/osbs-client/master
[build status]: https://travis-ci.org/containerbuildsystem/osbs-client.svg?branch=master
[build status link]: https://travis-ci.org/containerbuildsystem/osbs-client
[coverage status]: https://coveralls.io/repos/containerbuildsystem/osbs-client/badge.svg?branch=master&service=github
[coverage status link]: https://coveralls.io/github/containerbuildsystem/osbs-client?branch=master
[development setup guide]: ./docs/development-setup.md
[osbs instance setup guide]: ./docs/osbs_instance_setup.md
[contributing guide]: ./CONTRIBUTING.md
