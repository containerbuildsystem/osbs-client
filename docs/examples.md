# Example configurations

## V2 output using Koji

Assumptions:

 * `osbs.example.com` is the OSBS build machine
 * `distribution.example.com` is the Distribution registry used for storing built images, which does not require authentication for push
 * `koji.example.com` is the Koji instance to install repo files for
 * `fedpkg sources` will download any required build artifacts not in git

Here is how `/etc/osbs.conf` might look:

```
[default]
build_host = osbs.example.com
openshift_url = https://osbs.example.com:8443/
authoritative_registry = distribution.example.com
registry_api_versions = v2
vendor = Example, Inc
distribution_scope = public
koji_root = http://koji.example.com/koji
koji_hub = http://koji.example.com/kojihub
sources_command = fedpkg sources
# We 'docker pull' from here:
source_registry_uri = https://distribution.example.com
# and 'docker push' to here:
registry_uri = https://distribution.example.com
```

Note that currently there is no support for pushing to Distribution registry instances that require authentication.

