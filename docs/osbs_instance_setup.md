# Deploying OpenShift Build System

We also have an [ansible playbook](https://github.com/DBuildService/ansible-osbs) that automates these steps. Please note that the playbook may not be in sync with this guide. Feel free to report any issues.


## Base system

Supported platforms:

 * RHEL 7
 * CentOS 7
 * Fedora (latest greatest)

## Packages

### OpenShift

Since [OpenShift 3](https://github.com/openshift/origin) is under heavy development, I encourage you to use latest released version.

As a source for the RPM package of OpenShift, we can use @mmilata's [copr](https://copr.fedoraproject.org/coprs/mmilata/openshift/).

```
$ dnf copr enable mmilata/openshift
$ dnf install openshift
```

### docker

I suggest using Docker engine 1.6+

```
$ dnf install docker
```

#### Setting Up Storage for Docker

I advise you to follow [this guide](http://developerblog.redhat.com/2014/09/30/overview-storage-scalability-docker/).


### docker-registry

Docker Registry where buildsystem pushes built packages and pulls base images.

```
$ dnf install docker-registry
```

#### Storage for Registry (direct-lvm)

```
$ lvcreate --wipesignatures y -n registry direct-lvm -l 50%VG
$ mkfs.xfs /dev/direct-lvm/registry
```

Add this line to `/etc/fstab` and you are all set:

```
/dev/direct-lvm/registry /var/lib/docker-registry xfs defaults 1 1
```


## OpenShift

Since OpenShift 0.4.3, you should have config file for master and node. There is a helper systemd unit file for that. You can run it with

```
$ systemctl start openshift-generate-conf.service
```

It will create whole runtime configuration:

 * SSL certificates
 * policies
 * master and node configs
  * `/car/lib/openshift/master.yaml`
  * `/car/lib/openshift/node.yaml`

Everything will be stored in `/var/lib/openshift`. Inspect the configs and change them accordingly.


### CLI

All the communication with the daemon is performed via executable `osc`. The binary needs to authenticate, otherwise all the requests will be denied. Authentication is handled via configuration file for `osc`. You need to set an environment variable to point `osc` to the config:

*0.4.3-*

```
$ export KUBECONFIG=/var/lib/openshift/openshift.local.certificates/admin/.kubeconfig
$ export CURL_CA_BUNDLE=/var/lib/openshift/openshift.local.certificates/ca/cert.crt
```

*0.4.4+*

```
$ export OPENSHIFTCONFIG=/var/lib/openshift/openshift.local.certificates/admin/.kubeconfig
```

### Authentication and Authorization

You can setup OpenShift with a proxy in front of it. This proxy may have an authentication, e.g. kerberos or basic auth. The proxy should then forward username to openshift via `X-Remote-User` http header.

Communication between proxy and openshift needs to be secure. Proxy needs to use specific SSL certificate signed by CA which is known (and preconfigured) in openshift. We can use self-signed certificate for this because it won't be exposed to the outside world.

Here's how to do it:

```
$ cd /var/lib/openshift
$ openssl req -new -nodes -x509 -days 3650 -extensions v3_ca -keyout proxy_auth.key -out proxy_auth.crt
$ openssl rsa -in proxy_auth.key -out proxy_auth.key
$ cp openshift.local.certificates/ca/cert.crt /etc/pki/tls/certs/openshift_ca.crt
$ cat proxy_auth.{crt,key} > /etc/pki/tls/private/openshift_certkey.crt
```

OpenShift conf snippet (it uses [RequestHeaderIdentityProvider](http://docs.openshift.org/latest/admin_guide/configuring_authentication.html#RequestHeaderIdentityProvider)):

```
  oauthConfig:
    identityProviders:
    - name: my_request_header_provider
       challenge: false
       login: false
       provider:
         apiVersion: v1
         kind: RequestHeaderIdentityProvider
         clientCA: proxy_auth.crt
         headers:
         - X-Remote-User
```

Note that the certificate we generated can be used as a CA here because it is self-signed and is thus its own CA.

httpd conf snippet:

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

Basic auth httpd snippet:

```
AuthType Basic
AuthUserFile "/path/to/htpasswd"
AuthName "OSBS Basic Auth"
AuthBasicProvider file
Require valid-user
RequestHeader set X-Remote-User %{REMOTE_USER}s
```

OpenShift is capable of [processing htpasswd](http://docs.openshift.org/latest/admin_guide/configuring_authentication.html#HTPasswdPasswordIdentityProvider) itself (meaning, you don't need httpd):

```
  oauthConfig:
    identityProviders:
    - name: my_htpasswd_provider
       challenge: true
       login: true
       provider:
         apiVersion: v1
         kind: HTPasswdPasswordIdentityProvider
         clientCA: openshift.local.certificates/proxy/ca.crt
         file: /path/to/htpasswd
```


### Management

Starting OpenShift:

```
$ systemctl start openshift
```

Wiping all runtime configuration:

```
$ systemctl stop openshift && rm -rf /var/lib/openshift/openshift.local.{etcd,volumes} && systemctl start openshift
```


#### Authentication Setup

In case you would like to turn the authentication off (which is not recommended, but should fine for testing):

```
$ openshift ex policy add-role-to-group cluster-admin system:unauthenticated system:authenticated -n master
```

#### Useful Commands

* `osc get builds` — list builds
* `osc get pods` — list pods
* `osc describe --namespace=master policyBinding master` — show authorization setup
* `osc describe build <build>` — get info about build
* `osc build-logs <build>` — get build logs (or `docker logs <container>`), -f to follow


For more information see [openshift's documentation](http://docs.openshift.org/latest/welcome/index.html). Good starting point is also [this guide](https://github.com/openshift/origin/blob/master/examples/sample-app/README.md).


## dock

In order to build images, you need to have a build image. It is the image where OpenShift performs builds. The image has installed component called dock, which performs the build itself.


### Getting build image


#### Dockerfile

```
FROM fedora

RUN yum -y update && \
    yum -y install python-setuptools docker docker-python dock dock-koji dock-metadata osbs && \
    yum clean all

CMD ["dock", "--verbose", "inside-build", "--input", "osv3"]
```

*Required packages*

 * **python-setuptools** — needed for pkg_resources
 * **docker** — we use docker within the container
 * **docker-python** — dock's dependency (alternative name may be `python-docker-py`)
 * **dock** — component to perform the build itself

*Optional packages*

 * **osbs** — if you would like to submit results back to OpenShift (requires `dock-metadata`)
 * **dock-koji** — [dock plugin](https://github.com/DBuildService/dock/blob/master/dock/plugins/pre_koji.py) for getting packages from koji targets
 * **fedpkg** — dock can fetch artifacts from lookaside cache of dist-git


Time to build it:

```
$ docker build --no-cache=true --tag=buildroot ${BUILDROOT_DOCKERFILE_PATH}
```
