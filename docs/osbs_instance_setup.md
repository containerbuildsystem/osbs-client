# Deploying OpenShift Build System

We also have an [ansible playbook](https://github.com/projectatomic/ansible-osbs) that automates these steps. Please note that the playbook may not be in sync with this guide. Feel free to report any issues.


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
$ dnf install openshift-master openshift-node
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

OpenShift has config file for master and node. You can generate them with the following commands:

```
$ cd /var/lib/openshift
$ openshift start --write-config=/etc/openshift
$ ln -s /etc/openshift/node-* /etc/openshift/node
```

It will create whole runtime configuration:

 * SSL certificates
 * policies
 * master and node configs
  * `/etc/openshift/master/master-config.yaml`
  * `/etc/openshift/node-$hostname/node-config.yaml`

Data will be stored in `/var/lib/openshift`. Inspect the configs and change them accordingly.

### CLI

All the communication with the daemon is performed via executable `oc` (or `osc` if you are using OpenShift 0.x). The binary needs to authenticate, otherwise all the requests will be denied. Authentication is handled via configuration file for `oc`. You need to set an environment variable to point `oc` to the config:

*0.4.4+*

```
$ export OPENSHIFTCONFIG=/var/lib/openshift/openshift.local.certificates/admin/.kubeconfig
```

*0.6.1+*

```
$ export KUBECONFIG=/etc/openshift/master/admin.kubeconfig
```

### Authentication and Authorization

You can setup OpenShift with a proxy in front of it. This proxy may have an authentication, e.g. kerberos or basic auth. The proxy should then forward username to openshift via `X-Remote-User` http header.

Communication between proxy and openshift needs to be secure. Proxy needs to use specific SSL client certificate signed by CA which is known (and preconfigured) in openshift. We can use self-signed certificate for this because it won't be exposed to the outside world.

For more information, see the [upstream guide](https://docs.openshift.org/latest/admin_guide/configuring_authentication.html).

Here's how to do it:

```
$ cd /var/lib/openshift
$ openssl req -new -nodes -x509 -days 3650 -extensions v3_ca -keyout proxy_auth.key -out proxy_auth.crt
$ openssl rsa -in proxy_auth.key -out proxy_auth.key
$ cp /etc/openshift/master/ca.crt /etc/pki/tls/certs/openshift_ca.crt
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
         clientCA: /var/lib/openshift/proxy_auth.crt
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
$ systemctl start openshift-master && systemctl start openshift-node
```

Wiping all runtime data:

```
$ systemctl stop openshift-master && systemctl stop openshift-node
$ rm -rf /var/lib/openshift/*
$ systemctl start openshift-master && systemctl start openshift-node
```


#### Authentication Setup

In case you would like to turn the authentication off (which is not recommended, but should be fine for testing):

```
$ oadm policy add-role-to-group edit system:unauthenticated system:authenticated
```
(`osadm` for OpenShift < 1.0)

#### Useful Commands

* `oc get builds` — list builds
* `oc get pods` — list pods
* `oc describe policyBindings :default` — show authorization setup
* `oc describe build <build>` — get info about build
* `oc build-logs <build>` — get build logs (or `docker logs <container>`), -f to follow


For more information see [openshift's documentation](http://docs.openshift.org/latest/welcome/index.html). Good starting point is also [this guide](https://github.com/openshift/origin/blob/master/examples/sample-app/README.md).


#### Namespaces and projects

OpenShift and kubernetes use namespaces for isolation. OpenShift extends [namespace](https://docs.openshift.org/latest/architecture/core_concepts/projects_and_users.html#namespaces) concept to [project](https://docs.openshift.org/latest/architecture/core_concepts/projects_and_users.html#projects).

In order to build images in OpenShift, you don't need to create new project/namespace. There is namespace called `default` and it's present after installation.

### Certificates based OpenShift setup

In order to be able to submit builds, your user needs to have at least `edit` role on the `default` namespace.

The easiest way to access OpenShift is to create a dedicated user and set just enough rights for the user:

```
$ oadm create-api-client-config \
  --certificate-authority='/etc/openshift/master/ca.crt' \
  --client-dir='/etc/openshift/master/user-builder' \
  --signer-cert='/etc/openshift/master/ca.crt' \
  --signer-key='/etc/openshift/master/ca.key' \
  --signer-serial='/etc/openshift/master/ca.serial.txt' \
  --user='system:builder'
```

This will create `kubeconfig` and certificates at `/etc/openshift/master/user-builder`. All we need now is to set proper permissions for the user:

```
$ oadm policy add-role-to-user -n default edit system:builder
```

### htpasswd based OpenShift setup

This is very well [documented](https://docs.openshift.org/latest/admin_guide/configuring_authentication.html#HTPasswdPasswordIdentityProvider) in OpenShift's documentation.


1. create `htpasswd` file:

 ```
 htpasswd -c /etc/openshift/htpasswd builder
 ```

2. update master config at `/etc/openshift/master/master-config.yaml`:

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
       file: /etc/openshift/htpasswd
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


#### Sharing environment

If your OpenShift instance is being used by other people, make sure that everyone is using their own project/namespace. Do not ever share your namespace with others.


## atomic-reactor

In order to build images, you need to have a build image. It is the image where OpenShift performs builds. The image has installed component called [atomic-reactor](https://github.com/projectatomic/atomic-reactor), which performs the build itself.


### Getting build image


#### Dockerfile

```
FROM fedora

RUN yum -y update && \
    yum -y install atomic-reactor* && \
    yum clean all

CMD ["atomic-reactor", "--verbose", "inside-build", "--input", "osv3"]
```

*Optional packages*

 * **osbs-client** — if you would like to submit results back to OpenShift (requires `atomic-reactor-metadata`)
 * **atomic-reactor-koji** — [atomic-reactor plugin](https://github.com/projectatomic/atomic-reactor/blob/master/atomic_reactor/plugins/pre_koji.py) for getting packages from koji targets
 * **fedpkg** — atomic-reactor can fetch artifacts from lookaside cache of dist-git


Time to build it:

```
$ docker build --no-cache=true --tag=buildroot ${BUILDROOT_DOCKERFILE_PATH}
```


## NFS share for koji container build plugin

Create directory where the images (tarballs) will be stored.

```
$ mkdir -p /mnt/registry/image-export
$ chown apache /mnt/registry/image-export
```

Open NFS port in firewall (I'm not sure if this is needed).

```
$ firewall-cmd --permanent --add-service=nfs
```

### Create/modify `/etc/exports`

* `builder.example.com` is hostname of the builder
* `my.devel.machine.example.com` is just for testing and can be removed later
* `172.17.42.1/16` is docker's internal network

```
$ cat /etc/exports

/mnt/registry/image-export 172.17.42.1/16(rw,all_squash,anonuid=48,anongid=48) builder.example.com(rw,all_squash,anonuid=48,anongid=48) my.devel.machine.example.com(rw,all_squash,anonuid=48,anongid=48)
```

You should see all exports here:

```
$ exportfs -avr
```

Test NFS writability (e.g. from that `my.devel.machine.example.com`)

```
$ mkdir -p /mnt/img-export
$ mount builder.example.com:/mnt/registry/image-export /mnt/img-export
$ touch /mnt/img-export/test_img.tar
```

Modify httpd config

```
$ cat /etc/httpd/conf.d/img.nfs.conf

# atomic-reactor copies tarballs to this directory (via NFS share) and koji
# downloads it from this place. Garbage collection needs to be done on this
# directory.

Alias /image-export /mnt/registry/image-export

<Location /image-export>
    Options +Indexes
    Require all granted
</Location>

$ systemctl reload httpd
```

### Test the setup

You should see that `test_img.tar` there.

```
$ curl https://builder.example.com/image-export/
```

TODO: garbage collection of the old files
