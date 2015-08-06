# Configuration file

By default, osbs-client expects configuration file at `/etc/osbs.conf`. You can change this behaviour with command line option `--config`.

Configuration file uses ini syntax:

```
[section]
key=value
# comment
```

## Options

There is section `[general]` which contains global configuration for all instances. You can put more sections to your config which represents multiple instances of OSBS (and then you can refer to them with command line option `--instance`).

### Types

Options may be either string or boolean. For boolean, these values are considered false: `false`, `FALSE`, `False`, `0`; and these are true: `true`, `TRUE`, `True`, `1`.

Some options are also mandatory.


### `[general]` options

* `build_json_dir` (**mandatory**, `string`) — path to directory with build json templates

* `verbose` (*optional*, `boolean`) — enable verbose logging

### instance options

* `openshift_uri` (**mandatory**, `string`) — root URL where openshift master API server is listening (e.g. `localhost:8443`)

* `git_url` (*optional*, `string`) — URL of git reposiotry where dockerfile lives (it is used to perform `git clone`)

* `git_ref` (*optional*, `string`) — name of git ref (branch/commit) to check out

* `user` (*optional*, `string`) — namespace in final docker image name (`user/component:tag`)

* `component` (*optional*, `string`) — image name in final docker image name (`user/component:tag`)

* `yum_repourls` (*optional*, `string`) — URL to content of yum repo file (which is downloaded and inserted into build process)

* `namespace` (*optional*, `string`) — name of [kubernetes namespace](https://github.com/GoogleCloudPlatform/kubernetes/blob/master/docs/namespaces.md) to use

* `koji_root` (*optional*, `string`) — URL of koji root (for Fedora it is `http://koji.fedoraproject.org/`)

* `koji_hub` (*optional*, `string`) — URL of koji hub — XMLRPC (for Fedora it is `http://koji.fedoraproject.org/kojihub`)

* `koji_target` (*optional*, `string`) — name of koji target from which packages should be fetched

* `sources_command` (*optional*, `string`) — command to use to get dist-git artifacts from lookaside cache (e.g. `fedpkg sources`)

* `username`, `password` (*optional*, `string`) — when OpenShift is hidden behind authentication proxy, you can specify username and password for basic authentication

* `use_kerberos` (*optional*, `boolean`) — when OpenShift is hidden behind authentication proxy, you can use kerberos for authentication

* `registry_uri` (*optional*, `string`) — docker registry URI to use for pulling and pushing images

* `pulp_registry_name` (*optional*, `string`) — name of pulp registry within dockpulp config

* `verify_ssl` (*optional*, `boolean`) — verify SSL certificates during secure connection?

* `build_type` (**mandatory**, `string`) — name of build type to use for building the image

* `vendor` (*optional*, `string`) — content of Vendor label to be set

* `build_host` (*optional*, `string`) — content of Build\_Host label to be set

* `architecture` (*optional*, `string`) — content of Architecture label to be set

* `authoritative_registry` (*optional*, `string`) — content of Authoritative\_Registry label to be set

* `use_auth` (*optional*, `boolean`) — by default, osbs-client is trying to authenticate against OpenShift master to get OAuth token; you may disable the process with this option

* `metadata_plugin_use_auth` (*optional*, `boolean`) — during build, atomic-container is sending metadata to OpenShift master; this is the same option as `use_auth`, except it consumed during build

* `source_secret` (*optional*, `string`) — name of [kubernetes secret](https://github.com/GoogleCloudPlatform/kubernetes/blob/master/docs/design/secrets.md) to use for pulp plugin

* `nfs_server_path` (*optional*, `string`) — NFS server and path to use for storing built image (it is passed to `mount` command)

* `nfs_dest_dir` (*optional*, `string`) — directory to create on provided NFS server where image will be stored

* `cpu_limit` (*optional*, `string`) — CPU limit to apply to build (for more info, see [documentation for resources](https://github.com/projectatomic/osbs-client/blob/master/docs/resource.md)

* `memory_limit` (*optional*, `string`) — memory limit to apply to build (for more info, see [documentation for resources](https://github.com/projectatomic/osbs-client/blob/master/docs/resource.md)

* `storage_limit` (*optional*, `string`) — storage limit to apply to build (for more info, see [documentation for resources](https://github.com/projectatomic/osbs-client/blob/master/docs/resource.md)
