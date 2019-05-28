# Description of the Build Process

This document describes the process used to build images using OSBS. It sums up how different components of the system communicate to achieve the result.

## Components

This document mentions several components that communicate with each other:

 * [osbs-client](https://github.com/projectatomic/osbs-client)
 * [Openshift v3](https://github.com/openshift/origin/)
 * [atomic-reactor](https://github.com/projectatomic/atomic-reactor)

## The Process

 1. user runs `osbs build` or a Python process calls `osbs.api.OSBS().create_build()`.
 2. osbs-client determines the base layer for the image it's building; let's call base layer *BL* and the image that's being built *IM*.
 3. osbs-client determines name of [Openshift's BuildConfig](https://docs.openshift.org/latest/dev_guide/builds.html#defining-a-buildconfig) to use and makes sure no other build is running for it (only one build for a given `BuildConfig` is allowed to be running at a time).
 4. osbs-client updates Openshift's `BuildConfig` values for current build of *IM*. Most importantly, it makes sure that:
   * `ImageStream` for *BL* is in triggers. This ensures that when *BL* is rebuilt, *IM* is rebuilt as well.
   * `is_autorebuild` label is set to `"false"`, so that atomic-reactor knows that this is a build executed by user.
   * the `BuildConfig`'s source git ref names the git branch the build is from
   * the `bump_release` configuration names the branch's commit the initial build is from
   * the `stop_autorebuild_if_disabled` configuration names the config file where autorebuilds are enabled/disabled, see [OSBS repo configuration file](#osbs-repo-configuration-file)
 5. osbs-client starts the Openshift build from `BuildConfig` for *IM*.
 6. Openshift spawns a new build container that contains atomic-reactor inside.
 7. atomic-reactor's `CheckAndSetRebuildPlugin` is run. This determines whether the build is an automatically triggered autorebuild by examining `is_autorebuild` label. If this is autorebuild, then TODO. If this is not autorebuild, then the `is_autorebuild` label of the `BuildConfig` is set to `"true"`. This is done in order to ensure that all subsequent builds (except user-executed builds) are marked as autorebuild.
 8. atomic-reactor builds the image.
 9. Assuming the build is successful, atomic-reactor pushes the built image into registry and runs post-build plugins. These can vary depending on configuration, but usually it means pushing image to Pulp or so.
 10. atomic-reactor's `ImportImagePlugin` is run, which checks whether ImageStream for *IM* exists. If not, it creates it. Then, either way, it imports newly built *IM* into the `ImageStream`. If there are already some other images that use *IM* as their base image, they get rebuilt.


### docker registry v2 API (current workflow)

Since in v2 there is no file-like representation of an image, you can transport image only via registry protocol.

In order to create a v2 form, you need an instance of [distribution](https://github.com/docker/distribution) registry. Once you push the built image there, it's up to you, if you want to move the v2 image into pulp registry. That process is called sync.

 * upstream docker registry — can be configured with `registry_uri`:

```ini
registry_uri = registry.example.com
```

   if registry requires authentication, a dockercfg should be stored in the secret:
   ```ini
   registry_secret = dockercfg_secret
   ```

 * for backwards compatibility reasons, it is possible to suffix `registry_uri` with `/v2`.

```ini
registry_uri = registry.example.com/v2
```

Configuration of pulp registry where images should be synced can be done via:

```ini
pulp_sync_registry_name = stage-pulp
```

If this is not specified, value of `pulp_registry_name` is used.

pulp sync command (the way to get image from distribution to pulp) is requested when `v2` is in `registry_api_versions`.


### About v1 builds

OSBS no longer supports parallel v1 and v2 builds.

## OSBS Repo Configuration File

OSBS repo config file is a file that resides in the top level of built Git repo. It is currently not mandatory. The default name is `.osbs-repo-config`.

Currently, only `[autorebuild]` section is supported and `enabled` argument inside that. The `enabled` argument is a boolean, recognized values are 0, false, 1 and true (case insensitive). For example:

```ini
[autorebuild]
enabled=1
```

`enabled` is true by default (e.g. if the file is not present or the value is not set in the config file).
