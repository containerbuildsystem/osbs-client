# OSBS

[![unittests status badge]][unittests status link]
[![coveralls status badge]][coveralls status link]
[![lgtm python badge]][lgtm python link]
[![lgtm alerts badge]][lgtm alerts link]
[![linters status badge]][linters status link]

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

[coveralls status badge]: https://coveralls.io/repos/containerbuildsystem/osbs-client/badge.svg?branch=master
[coveralls status link]: https://coveralls.io/r/containerbuildsystem/osbs-client?branch=master
[lgtm python badge]: https://img.shields.io/lgtm/grade/python/g/containerbuildsystem/osbs-client.svg?logo=lgtm&logoWidth=18
[lgtm python link]: https://lgtm.com/projects/g/containerbuildsystem/osbs-client/context:python
[lgtm alerts badge]: https://img.shields.io/lgtm/alerts/g/containerbuildsystem/osbs-client.svg?logo=lgtm&logoWidth=18
[lgtm alerts link]: https://lgtm.com/projects/g/containerbuildsystem/osbs-client/alerts
[linters status badge]: https://github.com/containerbuildsystem/osbs-client/workflows/Linters/badge.svg?branch=master&event=push
[linters status link]: https://github.com/containerbuildsystem/osbs-client/actions?query=event%3Apush+branch%3Amaster+workflow%3A%22Linters%22
[unittests status badge]: https://github.com/containerbuildsystem/osbs-client/workflows/Unittests/badge.svg?branch=master&event=push
[unittests status link]: https://github.com/containerbuildsystem/osbs-client/actions?query=event%3Apush+branch%3Amaster+workflow%3A%22Unittests%22
[development setup guide]: ./docs/development-setup.md
[osbs instance setup guide]: ./docs/osbs_instance_setup.md
[contributing guide]: ./CONTRIBUTING.md
