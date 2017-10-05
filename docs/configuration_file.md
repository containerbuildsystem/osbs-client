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

* `openshift_required_version` (*optional*, `string`) — required version to run against (adjusts build template as appropriate)

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

* `koji_certs_secret` (*optional*, `string`) — name of [kubernetes secret](https://github.com/kubernetes/kubernetes/blob/master/docs/design/secrets.md) to use for koji authentication

* `koji_use_kerberos` (*optional*, `boolean`) — will set [atomic-reactor](https://github.com/projectatomic/atomic-reactor) plugins to use kerberos to authenticate to koji.

* `koji_kerberos_keytab` (*optional*, `string`) - location of the keytab that will be used to initialize kerberos credentials for [atomic-reactor](https://github.com/projectatomic/atomic-reactor) plugins - usually in the form `FILE:<absolute_path>`, see [kerberos documentation](http://web.mit.edu/Kerberos/krb5-latest/doc/basic/keytab_def.html) for other possible values

* `koji_kerberos_principal` (*optional*, `string`) - kerberos principal for the keytab provided in `koji_kerberos_keytab`

* `flatpak_base_image` (*optional*, `string`) - Docker image to use when installing RPMs to create a Flatpak. This does not have to match the version of the RPMs being installed.

* `odcs_url` (*optional*, `string`) - URL for API requests for the On Demand Compose Service. Needed for building Flatpaks

* `odcs_insecure` (*optional*, `boolean`) - If set, valid SSL certificates will not be required for requests to `odcs_url`

* `odcs_openidc_secret` (*optional*, `string`) - name of [kubernetes secret](https://github.com/kubernetes/kubernetes/blob/master/docs/design/secrets.md) to use for authenticating to the ODCS. The secret must contain one key, called 'token'.

* `pdc_url` (*optional*, `string`) - URL for API requests for the Product Definition Center. Needed for building Flatpaks

* `pdc_insecure` (*optional*, `boolean`) - If set, valid SSL certificates will not be required for requests to `pdc_url`

* `sources_command` (*optional*, `string`) — command to use to get dist-git artifacts from lookaside cache (e.g. `fedpkg sources`)

* `username`, `password` (*optional*, `string`) — when OpenShift is hidden behind authentication proxy, you can specify username and password for basic authentication

* `use_kerberos` (*optional*, `boolean`) — when OpenShift is hidden behind authentication proxy, you can use kerberos for authentication

* `client_cert`, `client_key` (*optional*, `string`) - paths to PEM-encoded client certificate and key to be used for authentication

* `kerberos_keytab` (*optional*, `string`) - location of the keytab that will be used to initialize kerberos credentials - usually in the form `FILE:<absolute_path>`, see [kerberos documentation](http://web.mit.edu/Kerberos/krb5-latest/doc/basic/keytab_def.html) for other possible values

* `kerberos_principal` (*optional*, `string`) - kerberos principal for the keytab provided in `kerberos_keytab`

* `kerberos_ccache` (*optional*, `string`) - location of credential cache to use when `kerberos_keytab` is set (please refer to [kerberos documentation](http://web.mit.edu/Kerberos/krb5-latest/doc/basic/ccache_def.html) for list of credential cache types)

* `registry_uri` (*optional*, `string`) — docker registry URI to use for pulling and pushing images. More than one can be specified by separating them with commas, and the registry API version for each can be specified by affixing '/v1' or '/v2' onto the end of the registry URI

* `registry_api_versions` (*optional*, `string`) — comma-separated list of docker registry HTTP API versions to support, defaults to `v1,v2`

* `source_registry_uri` (*optional*, `string`) — URI of docker registry from which image is pulled

* `pulp_registry_name` (*optional*, `string`) — name of pulp registry within dockpulp config

* `verify_ssl` (*optional*, `boolean`) — verify SSL certificates during secure connection?

* `vendor` (*optional*, `string`) — content of `vendor` label to be set

* `build_host` (*optional*, `string`) — content of `com.redhat.build-host` label to be set

* `architecture` (*optional*, `string`) — content of `architecture` label to be set

* `authoritative_registry` (*optional*, `string`) — content of `authoritative-source` label to be set

* `distribution_scope` (*optional*, `string`) - content of `distribution-scope` label to be set - possible values are `private`, `authoritative-source-only`, `restricted`, and `public`

* `use_auth` (*optional*, `boolean`) — by default, osbs-client is trying to authenticate against OpenShift master to get OAuth token; you may disable the process with this option

* `token` (*optional*, `string`) - OAuth token used to authenticate against OpenShift

* `builder_use_auth` (*optional*, `boolean`) — whether atomic-reactor plugins which in turn use osbs-client from within the build pod should try to authenticate against OpenShift master; defaults to `use_auth`

* `builder_openshift_url` (*optional*, `string`) — url of OpenShift where builder will connect

* `pulp_secret` (*optional*, `string`) — name of [kubernetes secret](https://github.com/GoogleCloudPlatform/kubernetes/blob/master/docs/design/secrets.md) to use for pulp plugin

* `smtp_host` (*optional*, `string`) - SMTP server host, e.g. `smtp.mycompany.com`

* `smtp_from` (*optional*, `string`) - Address to send notifications from, e.g. `user@mycompany.com`

* `smtp_additional_addresses` (*optional*, `string`) - A comma-separated list of additional addresses to include in notifications, e.g. `user1@mycompany.com, user2@mycompany.com`

* `smtp_error_addresses` (*optional*, `string`) - if the plugin has encountered an error the notification will be sent to this list of comma-separated addresses, e.g. `osbs-admin@mycompany.com, osbs-contact@mycompany.com`

* `smtp_email_domain` (*optional*, `string`) - construct email for users if it cannot be guessed from Koji's kerberos principals, e.g. `mycompany.com`

* `smtp_to_submitter` (*optional*, `boolean`) - whether Atomic Reactor should send a notification to koji task submitter

* `smtp_to_pkgowner` (*optional*, `boolean`) - whether Atomic Reactor should send a notification to koji package owner

* `cpu_limit` (*optional*, `string`) — CPU limit to apply to build (for more info, see [documentation for resources](https://github.com/projectatomic/osbs-client/blob/master/docs/resource.md)

* `memory_limit` (*optional*, `string`) — memory limit to apply to build (for more info, see [documentation for resources](https://github.com/projectatomic/osbs-client/blob/master/docs/resource.md)

* `storage_limit` (*optional*, `string`) — storage limit to apply to build (for more info, see [documentation for resources](https://github.com/projectatomic/osbs-client/blob/master/docs/resource.md)

* `reactor_config_secret` (*optional*, `string`) — name of Kubernetes secret holding [atomic-reactor configuration file](https://github.com/projectatomic/atomic-reactor/blob/master/docs/config.md)

* `client_config_secret` (*optional*, `string`) — name of Kubernetes secret holding osbs.conf to be used by atomic-reactor in the builder image (this is provided to the orchestrate_build plugin if present)

* `token_secrets` (*optional*, `string`) — whitespace-separated list of secret names with optional mount path in the format secret[:path], which can be used to hold service account tokens referenced by token_file in the osbs_client_secret osbs.conf

* `arrangement_version` (*optional*, `integer`) — default version of inner template to use when creating orchestrator build

* `can_orchestrate` (*optional*, `boolean`) — allows using orchestrator build, default is false

* `info_url_format` (*optional*, `string`) — format for `url` Dockerfile label, used as a Python format string; replacement field keywords are label names, and they will be replaced with the value of the named label

* `artifacts_allowed_domains` (*optional*, `string`) — list of domains allowed to be used when fetching artifacts via URL. When not specified, all domains are allowed.

* `scratch_build_node_selector` (*optional*, `string`) — a node selector to be applied to the scratch builds

* `explicit_build_node_selector` (*optional*, `string`) — a node selector to be applied to explicit builds

* `auto_build_node_selector` (*optional*, `string`) — a node selector to be applied to auto builds

* `isolated_build_node_selector` (*optional*, `string`) — a node selector to be applied to isolated builds

* `node_selector.`*platform* (*optional*, `string`) — a node selector to be used for worker builds for the specified platform, or "none"

* `equal_labels` (*optional*, `string`) — list of equal-preference label groups; if any of each set is missing, aliases will be added to complete the set; label delimiter ':', group delimiter ',' (e.g. `name1:name2:name3, release1:release2, version1:version2`)

* `group_manifests` (*optional*, `boolean`) — whether Atomic Reactor should create manifest lists, default is false

* `prefer_schema1_digest` (*optional*, `boolean`) — used by Atomic Reactor's koji_upload plugin when deciding which digest should be used in the image output files for a Koji build

## Build JSON Templates

In the `build_json_dir` there must be `prod.json` and `prod_inner.json` which
defines the [OpenShift Build](https://docs.openshift.org/latest/dev_guide/builds.html)
specification that will be used to enable the specific
[atomic-reactor](https://github.com/projectatomic/atomic-reactor) plugins that
should be enabled and the config values for each.

There is also a third file that is optional that can exist along side the
previous two in `build_json_dir` which is `prod_customize.json` and it will
provide the ability to set site-specific customizations such as removing,
plugins, adding plugins, or overriding arguments passed to existing plugins.

For orchestrator builds, there are required as well additional json files,
which have same function as mentioned above, only these are specific for
orchestrator or worker builds.

Based on `prod.json` there are `worker.json` and `orchestrator.json`.

Based on `prod_inner.json` there are `orchestrator_inner:1.json` and `worker_inner:1.json`,
where `1` is `arrangement_version` from configuration file, to specify default version.

Based on `prod_customize.json` there are `orchestrator_customize.json` and `worker_customize.json`.

The syntax of `prod_customize.json` is as follows:

```json
{
    "disable_plugins": [
        {
            "plugin_type": "foo_type",
            "plugin_name": "foo_name"
        },
        {
            "plugin_type": "bar_type",
            "plugin_name": "bar_name"
        }
    ],

    "enable_plugins": [
        {
            "plugin_type": "foo_type",
            "plugin_name": "foo_name",
            "plugin_args": {
                "foo_arg1": "foo_value1",
                "foo_arg2": "foo_value2"
            }
        }
    ]
}
```

Such that:

* `disable_plugins` will define a list of dicts that define the plugin type of the plugin that is to be removed (`prebuild_plugins`, `prepublish_plugins`, `postbuild_plugins`, `exit_plugins`) and the name of the plugin.

* `enable_plugins` will define a list of dicts that is used to add plugins or modify already enabled plugins by overriding args passed to the plugin, these must be defined as key-value pairs as illustrated above. It should be noted that plugins added here will be executed at the end of the list of plugins in that particular `plugin_type` (`prebuild_plugins`, `prepublish_plugins`, `postbuild_plugins`, `exit_plugins`), unless the plugin has already been previously added and this setting is only being used to override args. In the case of arg override, the plugin order execution will not change.
