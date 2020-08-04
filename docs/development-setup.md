# Local Development

## Running OpenShift

For more options how to install and run OpenShift, please see the Openshift
[install page][].

We'll use the `oc cluster up` method, since it's very easy to use:

1. If you're on Fedora, you can install package origin-clients

    ```shell
    dnf install -y origin-clients
    ```

    This package provides `oc` command.

1. You can start a single-node cluster like this

    ```shell
    oc cluster up
    ```

    (If you are running into some connection issues, please see notes about
    firewalld configuration in the OpenShift [cluster][] documentation. It may
    be necessary to restart firewalld and docker after setting up the firewall
    rules, or simply reboot. If all else fails, removing all firewall rules with
    `iptables -F` may be useful, though this generally should not be needed.)

## Getting `osbs-client`

For local development, we advise to clone the osbs-client git repo

```shell
git clone git@github.com:containerbuildsystem/osbs-client.git
cd osbs-client
pip2 install -r ./requirements.txt
python2 ./setup.py develop
```

Alternatively, you can get the latest stable version of osbs-client from
Fedora's repositories

```shell
dnf install -y osbs-client
```

## Obtaining build image

In order to build *your* images, you need to have a build image first.
This image is used to create a build container, where atomic-reactor is
running and taking care of building your images.

If you cloned this repository, you can use the Dockerfile which is present in
the root directory.

```shell
docker build --no-cache --tag=buildroot .
```

If you are using osbs-client from git master branch, you should also install
atomic-reactor from master branch:

```shell
docker build --no-cache --tag=buildroot --build-arg REACTOR_SOURCE=git .
```

## Building images, finally

In order to submit a build, you need to have a permission. If you started
OpenShift with `oc cluster up`, there's a user `developer` set up with
namespace `myproject` out of the box. This is where we'll build our images.

You'll also need to enable the custom build type, for example

```shell
oc policy add-role-to-group system:build-strategy-custom system:authenticated
```

(This allows any authenticated user with access to myproject to run a custom
build.)

If you need to login as a different user, you can use command:

```shell
oc login
```

The output of `oc cluster up` contains more information about the authentication
setup.

### Config file

osbs-client accepts configuration from CLI and from an ini-style
configuration file. Here's a really simple one you can use as a start:

```conf
[general]
verbose = true
build_json_dir = inputs/

[local]
openshift_url = https://localhost:8443/
namespace = myproject
use_kerberos = false
verify_ssl = false
use_auth = true
token = <enter-the-token-here>
```

Copy the content and place it in a file named 'osbs.conf'.

You have to ensure all the required json files exist in the directory configured
for `build_json_dir`:

```shell
cp inputs/*.json $build_json_dir
```

You must fill in the `token` value

- OpenShift uses Oauth tokens for authentication, You can easily get a token of
  currently logged-in user:

  ```shell
  oc whoami -t
  hb1WN2Tx8yV4s4slFxhSRm24Hk_Pwma5wZiW0iadP4c
  ```

- Put the token in the config

  ```conf
  ...
  token = hb1WN2Tx8yV4s4slFxhSRm24Hk_Pwma5wZiW0iadP4c
  ```

#### Registry

The `oc cluster up` method deploys a registry by default. This registry requires
authentication and at the same is not using SSL. Hence it's not possible to use
it in the workflow osbs is using at the moment.

You can inspect the registry if you wish, it's running in namespace `default`
and the service is named `docker-registry`

```shell
oc login -u system:admin
oc project default
```

And now you can inspect the registry

```shell
oc describe service docker-registry

Name:                   docker-registry
Namespace:              default
Labels:                 docker-registry=default
Selector:               docker-registry=default
Type:                   ClusterIP
IP:                     172.30.109.245
Port:                   5000-tcp        5000/TCP
Endpoints:              172.17.0.5:5000
Session Affinity:       ClientIP
No events.
```

Don't forget to switch back to developer

```shell
oc login -u developer
```

When you specify correct namespace (in Dockerfile, label `name`) and registry
URI (in `osbs.conf`, `registry_uri` key), OpenShift mounts secret inside build
container with credentials to push to the registry

```shell
[root@dockerfile-fedora-chromium-master-2-build /]# cd /var/run/secrets/openshift.io/push
[root@dockerfile-fedora-chromium-master-2-build push]# cat .dockercfg
{"172.30.72.169:5000":{"username":"serviceaccount","password":"eyJhb...
```

## Sample build

```shell
osbs --config osbs.conf --instance local build -g https://github.com/TomasTomecek/hello-world-container -b master -u ${USER} -c hello-world
```

## Configuring orchestration

To test out orchestrated builds, you'll need another namespace to act as a test
worker, and add the necessary permissions to it:

```shell
oc new-project worker01

oc login -u system:admin
oc policy -n worker01 add-role-to-user edit system:serviceaccount:myproject:builder
oc policy -n worker01 add-role-to-user edit system:serviceaccount:worker01:builder
oc policy -n worker01 add-role-to-group system:build-strategy-custom system:authenticated

oc login -u developer
oc project myproject
```

Then you'll need to create two configuration files. Create a file
'reactor-conf/config.yaml' which contains

```yaml
version: 1
clusters:
  x86_64:
  - name: worker01
    max_concurrent_builds: 4
    enabled: true
```

and a file 'osbs-client-conf/osbs.conf' which contains

```conf
[general]
verbose = true
build_json_dir = <path to inputs in the build image>

[worker01]
openshift_url = https://<not-localhost-ip-address>:8443/
namespace = worker01
use_kerberos = false
verify_ssl = false
use_auth = true
registry_uri = <registry URL>
```

And create the corresponding secrets

```shell
oc secrets -n myproject new osbs-client-conf osbs-client-conf/osbs.conf
oc secrets -n myproject new reactor-conf reactor-conf/config.yaml
```

Note that while the directory and secret names are arbitrary, the filenames
('osbs.conf', 'config.yaml') must be exactly as listed above, since filename
determines the key under which the content is stored within the secret.

Finally, edit the main 'osbs.conf' and add to the [local] section

```conf
can_orchestrate = true
```

You are now ready to perform an orchestrated build

```shell
osbs --config osbs.conf --instance local build --orchestrate --platforms x86_64 -g https://github.com/TomasTomecek/hello-world-container -b master -u ${USER}
```

[install page]: https://install.openshift.com
[cluster]: https://github.com/openshift/origin/blob/master/docs/cluster_up_down.md