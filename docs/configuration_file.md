# Configuration file

By default, osbs-client expects configuration file at '/etc/osbs.conf'. You can
change this behaviour with command line option `--config`.

The configuration file uses ini syntax

```ini
[section]
key=value
# comment
```

## Options

There is a section `[general]` which contains global configuration for all
instances. You can put more sections into your config which represent multiple
instances of OSBS (and then you can refer to them with the command line option
`--instance`).

It is also possible to describe platforms in platform sections, which have the
form `[platform:ARCH]` where `ARCH` is the platform name such as `x86_64` or
`aarch64`. Platform sections are not interpreted as OSBS instances, but
descriptions to be applied to OSBS instances running on the given platform.

### Types

Options may be either string or boolean. For boolean, these values are
considered false: `false`, `FALSE`, `False`, `0`; and these are true: `true`,
`TRUE`, `True`, `1`.

Some options are also mandatory.

### `[general]` options

- `build_json_dir` (mandatory, str): path to directory with build json templates
- `verbose` (optional, boolean): enable verbose logging
- `openshift_required_version` (optional, str): required version to run against
  (adjusts build template as appropriate)

### instance options

- `openshift_uri` (mandatory, str): root URL where openshift master API server
  is listening (e.g. 'localhost:8443')
- `git_url` (optional, str): URL of git repository where dockerfile lives (it is
  used to perform `git clone`)
- `git_ref` (optional, str): name of git ref (branch/commit) to check out
- `user` (optional, str): namespace in final docker image name
  ('user/component:tag')
- `component` (optional, str): image name in final docker image name
  ('user/component:tag')
- `yum_repourls` (optional, str): URL to content of yum repo file (which is
  downloaded and inserted into build process)
- `namespace` (optional, str): the [kubernetes namespace][] to use
- `koji_target` (optional, str): name of koji target from which packages should
  be fetched
- `flatpak_base_image` (optional, str): Docker image to use when installing RPMs
  to create a Flatpak. This does not have to match the version of the RPMs being
  installed.
- `username`, `password` (optional, str): when OpenShift is hidden behind
  authentication proxy, you can specify username and password for basic
  authentication
- `use_kerberos` (optional, boolean): when OpenShift is hidden behind
  authentication proxy, you can use kerberos for authentication
- `client_cert`, `client_key` (optional, str): paths to PEM-encoded client
  certificate and key to be used for authentication
- `kerberos_keytab` (optional, str): location of the keytab that will be used to
  initialize kerberos credentials: usually in the form `FILE:<absolute_path>`,
  see the kerberos [keytab][] documentation for other possible values
- `kerberos_principal` (optional, str): kerberos principal for the keytab
  provided in `kerberos_keytab`
- `kerberos_ccache` (optional, str): location of credential cache to use when
  `kerberos_keytab` is set (please refer to the kerberos [ccache][]
  documentation for list of credential cache types)
- `use_auth` (optional, boolean): by default, osbs-client is trying to
  authenticate against OpenShift master to get OAuth token; you may disable the
  process with this option
- `token` (optional, str): OAuth token used to authenticate against OpenShift
- `builder_use_auth` (optional, boolean): whether atomic-reactor plugins which
  in turn use osbs-client from within the build pod should try to authenticate
  against OpenShift master; defaults to `use_auth`
- `cpu_limit` (optional, str): CPU limit to apply to build (for more info, see
  the OSBS [resource][] documentation
- `memory_limit` (optional, str): memory limit to apply to build (for more info,
  see the OSBS [resource][] documentation
- `storage_limit` (optional, str): storage limit to apply to build (for more
  info, see the OSBS [resource][] documentation
- `reactor_config_map` (optional, str): name of the Kubernetes ConfigMap holding
  the atomic-reactor [configuration file][]
- `token_secrets` (optional, str): whitespace-separated list of secret names
  with optional mount path in the format `secret[:path]`, which can be used to
  hold service account tokens referenced by token_file in the osbs_client_secret
  osbs.conf
- `can_orchestrate` (optional, boolean): allows using orchestrator build,
  default is false
- `scratch_build_node_selector` (optional, str): a node selector to be applied
  to the scratch builds
- `explicit_build_node_selector` (optional, str): a node selector to be applied
  to explicit builds
- `auto_build_node_selector` (optional, str): a node selector to be applied to
  auto builds
- `isolated_build_node_selector` (optional, str): a node selector to be applied
  to isolated builds
- `node_selector.{platform}` (optional, str): a node selector to be used for
  worker builds for the specified platform, or "none"
- `build_from` (optional, str): build source to use, consists of 2 parts
  separated with delimiter ':', first part can be : image or imagestream, and
  second part is corresponding image or imagestream
- `worker_max_run_hours` (optional, int): the build will be cancelled if a
  worker process takes more than this amount of hours to build. Default is 3
  hours. If the value is 0 or less, the build will not be cancelled no matter
  how long it takes to build
- `orchestrator_max_run_hours` (optional, int): the build will be cancelled if
  it is not completed across all workers within this time. Default is 4 hours.
  If the value is 0 or less, the build will not be cancelled no matter how long
  it takes to build

### `[platform:ARCH]` options

- `architecture` (optional, str): platform's GOARCH (Go language platform name).
  If not declared, this option assumes the name of the platform being defined

## Build JSON Templates

In the `build_json_dir` there must be `prod.json`, which defines the OpenShift
[Build][] specification.

There is also a third file that is optional that can exist along side the
previous two in `build_json_dir` which is `prod_customize.json` and it will
provide the ability to set site-specific customizations such as removing,
plugins, adding plugins, or overriding arguments passed to existing plugins.

For orchestrator builds, there are required as well additional json files, which
have same function as mentioned above, only these are specific for orchestrator
or worker builds.

Based on `prod.json` there are `worker.json` and `orchestrator.json`.

Based on `prod_customize.json` there are `orchestrator_customize.json` and
`worker_customize.json`.

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

- `disable_plugins` will define a list of dicts that define the plugin type of
  the plugin that is to be removed (`prebuild_plugins`, `prepublish_plugins`,
  `postbuild_plugins`, `exit_plugins`) and the name of the plugin.
- `enable_plugins` will define a list of dicts that is used to add plugins or
  modify already enabled plugins by overriding args passed to the plugin, these
  must be defined as key-value pairs as illustrated above. It should be noted
  that plugins added here will be executed at the end of the list of plugins in
  that particular `plugin_type` (`prebuild_plugins`, `prepublish_plugins`,
  `postbuild_plugins`, `exit_plugins`), unless the plugin has already been
  previously added and this setting is only being used to override args. In the
  case of arg override, the plugin order execution will not change.

[kubernetes namespace]: https://github.com/GoogleCloudPlatform/kubernetes/blob/master/docs/namespaces.md
[keytab]: http://web.mit.edu/Kerberos/krb5-latest/doc/basic/keytab_def.html
[ccache]: http://web.mit.edu/Kerberos/krb5-latest/doc/basic/ccache_def.html
[resource]: .//resource.md
[configuration file]: https://github.com/containerbuildsystem/atomic-reactor/blob/master/docs/config.md
[Build]: https://docs.openshift.org/latest/dev_guide/builds.html
[atomic-reactor]: https://github.com/containerbuildsystem/atomic-reactor
