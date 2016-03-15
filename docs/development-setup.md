# Local Development

It's actually very simple to setup OpenShift locally. We'll use a docker image from Docker Hub. For more OpenShift-focused information, see [this guide](https://docs.openshift.org/latest/getting_started/administrators.html).

First thing to do is to clone this git repo:

```
$ git clone git@github.com:projectatomic/osbs-client.git
$ cd osbs-client
```


## setup with docker-compose

Install `docker-compose`:

```
$ dnf install -y docker-compose
```

Then you can start OpenShift with one simple command:

```
$ docker-compose up -d
```


## setup without docker-compose

You don't need docker-compose to launch the container (it's just convenience); you can run the OpenShift container like this:

```
$ docker run -d --name "origin" \
  --privileged --pid=host --net=host \
  -v /:/rootfs:ro -v /var/run:/var/run:rw -v /sys:/sys -v /var/lib/docker:/var/lib/docker:rw \
  -v /var/lib/openshift/openshift.local.volumes:/var/lib/openshift/openshift.local.volumes \
  openshift/origin:v1.0.6 start
```


## building images

In order to successfully build an image you need to have 3 things:

1. permission to submit a build — for development it's fine to do this (never do that on a public system!):
    ```
    $ docker exec -ti $openshift_container bash
    $ oadm policy add-role-to-group edit system:unauthenticated system:authenticated
    ```


2. buildroot image — see [this section](https://github.com/projectatomic/osbs-client/blob/master/docs/osbs_instance_setup.md#atomic-reactor) section in [deplyoment guide](https://github.com/projectatomic/osbs-client/blob/master/docs/osbs_instance_setup.md)

3. make OpenShift API accessible so build can submit metadata once build is done:
    ```
    $ firewall-cmd --permanent --add-port 8443/tcp
    $ firewall-cmd --add-port 8443/tcp
    ```

4. config for osbs-client; something like this should do
    ```
    [local]
    openshift_uri = https://172.17.42.1:8443/
    sources_command = fedpkg sources
    registry_uri = <registry:5000>
    vendor = <this_company>
    authoritative_registry = <registry.example.com>
    build_host = <builder.example.com>
    architecture = x86_64
    build_type = prod
    verify_ssl = false
    builder_use_auth = false
    use_auth=false
    ```
