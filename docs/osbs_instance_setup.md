# Deploying OpenShift Build System

OSBS consists of several components, namely:

 * operating system - RHEL 7, Centos 7 or Fedora (latest greatest) should work out of the box
 * [Docker](https://www.docker.com/)
 * [OpenShift 3](https://www.openshift.org/) - Enterprise and Origin both work
 * [atomic-reactor](https://github.com/projectatomic/atomic-reactor)
 * (optional) authenticating proxy based on Apache httpd
 * (optional) docker registry where built images are going to be pushed

The recommended way to install OSBS is to use our
[ansible playbook](https://github.com/projectatomic/ansible-osbs).
Please note that the playbook may not be in sync with this guide - feel free to
report any issues.

## Docker

Use the docker shipped with your operating system. Consult OpenShift
documentation - there might be restrictions on what versions of Docker you can
use.

```
$ dnf install docker
```

### Docker storage

Before using docker it is necessary to set up its storage. Refer to
[this guide](http://developerblog.redhat.com/2014/09/30/overview-storage-scalability-docker/)
and the manual page of docker-storage-setup for more information. On RPM-based
distros it's most likely that you want to use the direct-lvm method.

## OpenShift

Both OpenShift Origin or OpenShift Enterprise can be used to host OSBS.

### Installing OpenShift Origin

See the [upstream documentation](https://docs.openshift.org/latest/welcome/index.html)
for installation instructions.

AFAIK Origin is not packaged for Fedora, you can use @mmilata's
[copr](https://copr.fedoraproject.org/coprs/mmilata/openshift/) though the
packages there will likely be outdated.

```
$ dnf copr enable mmilata/openshift
$ dnf install origin-master origin-node
```

Other option is to install OpenShift [from its source](https://github.com/openshift/origin).

### Installing OpenShift Enterprise

See
[OpenShift Enterprise documentation](https://docs.openshift.com/enterprise/latest/welcome/index.html)
for installation instructions.

```
# correct repositories have to be enabled
$ yum install atomic-openshift-master atomic-openshift-node atomic-openshift-sdn-ovs
```

### Configuration

OpenShift uses various files for master and node that are generated when RPMs
are installed:

 * SSL certificates
 * policies
 * master and node configs
  * `/etc/origin/master/master-config.yaml`
  * `/etc/origin/node-$hostname/node-config.yaml`
 * init scripts configuration (e.g. log level)
  * `/etc/sysconfig/origin-master`
  * `/etc/sysconfig/origin-node`

Data will be stored in `/var/lib/origin`. Inspect the configs and change them
accordingly. In case of more drastic changes you may want to re-generate the
configuration using different parameters with `openshift start master
--write-config` and `oadm create-node-config` commands.

### Management

Starting OpenShift:

```
$ systemctl start origin-master && systemctl start origin-node
```

Wiping all runtime data:

```
$ systemctl stop origin-master origin-node
$ rm -rf /var/lib/origin/*
$ systemctl start origin-master origin-node
```

### Talking to OpenShift via command line

All communication with OpenShift is performed via executable `oc`. The
binary needs to authenticate, otherwise all the requests will be denied.
Authentication is handled via configuration file for `oc`. You need to set an
environment variable to point `oc` to the admin config:

```
$ export KUBECONFIG=/etc/origin/master/admin.kubeconfig
```

#### Useful Commands

* `oc get builds` — list builds
* `oc get pods` — list pods
* `oc describe policyBindings :default` — show authorization setup
* `oc describe build <build>` — get info about build
* `oc build-logs <build>` — get build logs (or `docker logs <container>`), -f to follow

For more information see
[OpenShift's documentation](http://docs.openshift.org/latest/welcome/index.html).
Good starting point is also
[this guide](https://github.com/openshift/origin/blob/master/examples/sample-app/README.md).

### Namespaces and projects

OpenShift and Kubernetes use namespaces for isolation. OpenShift extends
[namespace](https://docs.openshift.org/latest/architecture/core_concepts/projects_and_users.html#namespaces)
concept to
[project](https://docs.openshift.org/latest/architecture/core_concepts/projects_and_users.html#projects).

In order to build images in OpenShift, you don't need to create new
project/namespace. There is namespace called `default` and it's present after
installation. If your OpenShift instance is being used by other people, make
sure that everyone is using their own project/namespace. Do not ever share your
namespace with others.

### Authentication and authorization

In case you would like to turn the authentication off (which is not
recommended, but should be fine for testing), run following in your namespace:

```
$ oadm policy add-role-to-group edit system:unauthenticated system:authenticated
```

In production, you most likely want to set up some form of
[authentication](https://docs.openshift.org/latest/install_config/configuring_authentication.html)
and then configure suitable [authorization policy](https://docs.openshift.org/latest/admin_guide/manage_authorization_policy.html).

#### Certificates based authentication setup

In order to be able to submit builds, your user needs to have at least `edit` role on the chosen namespace.

The easiest way to access OpenShift is to create a dedicated user and set just enough rights for the user:

```
$ oadm create-api-client-config \
  --certificate-authority='/etc/origin/master/ca.crt' \
  --client-dir='/etc/origin/master/user-builder' \
  --signer-cert='/etc/origin/master/ca.crt' \
  --signer-key='/etc/origin/master/ca.key' \
  --signer-serial='/etc/origin/master/ca.serial.txt' \
  --user='system:builder'
```

This will create `kubeconfig` and certificates at `/etc/origin/master/user-builder`. All we need now is to set proper permissions for the user:

```
$ oadm policy add-role-to-user -n default edit system:builder
```

#### htpasswd based authentication setup

This is very well [documented](https://docs.openshift.org/latest/admin_guide/configuring_authentication.html#HTPasswdPasswordIdentityProvider) in OpenShift's documentation.


1. create `htpasswd` file:

 ```
 htpasswd -c /etc/origin/htpasswd builder
 ```

2. update master config at `/etc/origin/master/master-config.yaml`:

 ```
 oauthConfig:
   ...
   identityProviders:
   - name: htpasswd_provider
     challenge: true
     login: true
     provider:
       apiVersion: v1
       kind: HTPasswdPasswordIdentityProvider
       file: /etc/origin/htpasswd
 ```

3. set correct permissions for the builder user:

 ```
 $ oadm policy add-role-to-user -n default edit htpasswd_provider:builder
 ```

4. put username and password to config of osbs-client:

 ```
 [the-instance]
 openshift_uri = https://localhost:8443/
 username = builder
 password = ...
 ```

#### kerberos based authentication setup

OpenShift itself cannot use Kerberos authentication, however it can delegate
the authentication to reverse proxy. See the corresponding section below for
details.

## Apache-based authenticating proxy

You can setup OpenShift with a proxy in front of it. This proxy may have an
authentication, e.g. kerberos or basic auth. The proxy should then forward
username to OpenShift via `X-Remote-User` http header.

Communication between proxy and OpenShift needs to be secure. Proxy needs to
use specific SSL client certificate signed by CA which is known (and
preconfigured) in OpenShift. We can use self-signed certificate for this
because it won't be exposed to the outside world.

For more information, see the
[upstream guide](https://docs.openshift.org/latest/admin_guide/configuring_authentication.html).

Here's how to do it:

```
$ cd /var/lib/origin
$ openssl req -new -nodes -x509 -days 3650 -extensions v3_ca -keyout proxy_auth.key -out proxy_auth.crt
$ openssl rsa -in proxy_auth.key -out proxy_auth.key
$ cp /etc/origin/master/ca.crt /etc/pki/tls/certs/openshift_ca.crt
$ cat proxy_auth.{crt,key} > /etc/pki/tls/private/openshift_certkey.crt
```

OpenShift master configuration snippet (it uses
[RequestHeaderIdentityProvider](http://docs.openshift.org/latest/admin_guide/configuring_authentication.html#RequestHeaderIdentityProvider)):

```
  oauthConfig:
    identityProviders:
    - name: my_request_header_provider
       challenge: false
       login: false
       provider:
         apiVersion: v1
         kind: RequestHeaderIdentityProvider
         clientCA: /var/lib/origin/proxy_auth.crt
         headers:
         - X-Remote-User
```

Note that the certificate we generated can be used as a CA here because it is
self-signed and is thus its own CA.

### Kerberos authentication example

```
<VirtualHost *:9443>
    SSLProxyEngine On
    SSLProxyCACertificateFile /etc/pki/tls/certs/openshift_ca.crt
    SSLProxyMachineCertificateFile /etc/pki/tls/private/openshift_certkey.crt

    <Location "/">
        ProxyPass https://127.0.0.1:8443/ connectiontimeout=30 timeout=300
        ProxyPassReverse https://127.0.0.1:8443/
    </Location>

    # don't auth /oauth/token/request and /oauth/token/display
    <ProxyMatch /oauth/token/.*>
        Require all granted
    </ProxyMatch>

    # /oauth/authorize and /oauth/approve should be protected by Apache.
    <ProxyMatch /oauth/a.*>
        AuthType Kerberos
        AuthName "OSBS Kerberos Authentication"
        KrbMethodNegotiate on
        KrbMethodK5Passwd off
        KrbServiceName Any
        KrbAuthRealms REALM.COM
        Krb5Keytab /path/to/keytab
        KrbSaveCredentials off
        Require valid-user
        RequestHeader set X-Remote-User %{REMOTE_USER}s
        RequestHeader unset Authorization
        RequestHeader unset WWW-Authenticate
    </ProxyMatch>

    # All other requests should use Bearer tokens. These can only be verified by
    # OpenShift so we need to let these requests pass through.
    <ProxyMatch ^/oauth/>
        SetEnvIfNoCase Authorization Bearer passthrough
        Require env passthrough
    </ProxyMatch>
</VirtualHost>
```

With this configuration run the following to allow all authenticated users to start builds:

```
$ oadm policy add-role-to-group edit system:authenticated
```

## atomic-reactor

In order to build images, you need to have a build image. It is the image used
by OpenShift to perform builds. The image has installed component called
[atomic-reactor](https://github.com/projectatomic/atomic-reactor), which
performs the build itself.

Please see the project's documentation for more complete information. There's
also [ansible role](https://github.com/projectatomic/ansible-role-atomic-reactor)
that can be used to pull or build the image.

Unless configured otherwise in osbs-client, build image is expected to be
tagged as `buildroot:latest` on the build host. If you are using multinode
setup you should set up [integrated docker
registry](https://docs.openshift.org/latest/install_config/install/docker_registry.html)
and refer to the build image by it's ImageStream tag.

### Pulling build image

The latest development version of the build image is available at docker hub:
```
$ docker pull slavek/atomic-reactor
```

### Building build image

You can also build the image yourself using Dockerfile:


```
FROM fedora

RUN yum -y update && \
    yum -y install atomic-reactor* && \
    yum clean all

CMD ["atomic-reactor", "--verbose", "inside-build"]
```

*Optional packages*

 * **osbs-client** — if you would like to submit results back to OpenShift (requires `atomic-reactor-metadata`)
 * **atomic-reactor-koji** — [atomic-reactor plugin](https://github.com/projectatomic/atomic-reactor/blob/master/atomic_reactor/plugins/pre_koji.py) for getting packages from koji targets
 * **fedpkg** — atomic-reactor can fetch artifacts from lookaside cache of dist-git


Time to build it:

```
$ docker build --no-cache=true --tag=buildroot ${BUILDROOT_DOCKERFILE_PATH}
```

## Docker registry

The built images need to be pushed somewhere. You can use legacy docker registry:

```
$ dnf install docker-registry
$ systemctl enable docker-registry
$ systemctl start docker-registry
```

Or you can use current v2 docker registry:

```
$ dnf install docker-distribution
$ systemctl enable docker-distribution
$ systemctl start docker-distribution
```
