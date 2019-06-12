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

## V2 output using Pulp and Koji

Assumptions:

 * `osbs.example.com` is the OSBS build machine
 * `distribution.example.com` is the Distribution registry used for staging built images for V2 sync into Pulp
 * `koji.example.com` is the Koji instance to install repo files for
 * `fedpkg sources` will download any required build artifacts not in git
 * `pulp.example.com` is the Pulp instance to use
 * `crane.example.com` is the Crane endpoint for the Pulp content

For Pulp, with the dockpulp client there is an additional configuration file to set up. It should look something like this:

```
[pulps]
prodpulp = https://pulp.example.com/

[registries]
prodpulp = https://crane.example.com/

[filers]
prodpulp = https://pulp.example.com/

[verify]
prodpulp = yes
```

This dockpulp configuration file should be installed as `/etc/dockpulp.conf` inside the buildroot image.

Here is how `/etc/osbs.conf` might look:

```
[default]
build_host = osbs.example.com
openshift_url = https://osbs.example.com:8443/
authoritative_registry = crane.example.com
vendor = Example, Inc
distribution_scope = public
koji_root = http://koji.example.com/koji
koji_hub = http://koji.example.com/kojihub
sources_command = fedpkg sources
# We 'docker pull' from here:
source_registry_uri = https://crane.example.com
# We 'docker push 'to here:
registry_uri = https://distribution.example.com
# This is the Pulp environment that will sync from registry_uri:
pulp_registry_name = prodpulp
pulp_secret = pulpsecret
```

The OSBS machine should be configured with a Kubernetes secret named `pulpsecret` so that it is able to authenticate.

```
$ oc secrets new pulpsecret key=/path/to/mykey cert=/path/to/mycert
secrets/pulpsecret
$ oc secrets add serviceaccount/builder secrets/pulpsecret --for=mount
```
