# Description of the Build Process

This document describes the process used to build images using OSBS. It sums up
how different components of the system communicate to achieve the result.

## Components

This document mentions several components that communicate with each other:

- [osbs-client][]
- [Openshift v3][]
- [atomic-reactor][]

## The Process

1. User runs `osbs build` or a Python process calls
   `osbs.api.OSBS().create_build()`
1. osbs-client determines the base layer for the image it's building; let's call
   base layer *BL* and the image that's being built *IM*
1. osbs-client determines name of Openshift's [BuildConfig][]
   to use and makes sure no other build is running for it (only one build for a
   given `BuildConfig` is allowed to be running at a time)
1. osbs-client updates Openshift's `BuildConfig` values for current build of
   *IM*. Most importantly, it makes sure that
   1. `ImageStream` for *BL* is in triggers. This ensures that when *BL* is
      rebuilt, *IM* is rebuilt as well
   1. `is_autorebuild` label is set to `"false"`, so that atomic-reactor knows
      that this is a build executed by user
   1. The `BuildConfig`'s source git ref names the git branch the build is from
      ― The `bump_release` configuration names the branch's commit the initial
      build is from
1. osbs-client starts the Openshift build from `BuildConfig` for *IM*
1. Openshift spawns a new build container that contains atomic-reactor inside
1. atomic-reactor's `CheckAndSetRebuildPlugin` is run. This determines whether
   the build is an automatically triggered autorebuild by examining
   `is_autorebuild` label. If this is autorebuild, then TODO. If this is not
   autorebuild, then the `is_autorebuild` label of the `BuildConfig` is set to
   `"true"`. This is done in order to ensure that all subsequent builds (except
   user-executed builds) are marked as autorebuild
1. atomic-reactor builds the image
1. Assuming the build is successful, atomic-reactor pushes the built image into
   the registry and runs its post-build plugins. These can vary depending on
   configuration, but usually it means pushing image to Pulp or the like
1. atomic-reactor's `ImportImagePlugin` is run, which checks whether ImageStream
   for *IM* exists. If not, it creates it. Then, either way, it imports newly
   built *IM* into the `ImageStream`. If there are already some other images
   that use *IM* as their base image, they get rebuilt

### docker registry v2 API (current workflow)

Since in v2 there is no file-like representation of an image, you can transport
image only via registry protocol.

In order to create a v2 form, you need an instance of a
docker [distribution registry][] registry.

### About v1 builds

OSBS no longer supports v1 builds.

## OSBS Repo Configuration File

OSBS repo config file is a file that resides in the top level of built Git repo.
It is currently not mandatory. The default name is `.osbs-repo-config`.

Currently, only `[autorebuild]` section is supported and `enabled` argument
inside that. The `enabled` argument is a boolean, recognized values are 0,
false, 1 and true (case insensitive). For example

```ini
[autorebuild]
enabled=1
```

`enabled` is true by default (e.g. if the file is not present or the value is
not set in the config file).

[osbs-client]: https://github.com/containerbuildsystem/osbs-client
[openshift v3]: https://github.com/openshift/origin
[atomic-reactor]: https://github.com/containerbuildsystem/atomic-reactor
[BuildConfig]: https://docs.openshift.org/latest/dev_guide/builds.html#defining-a-buildconfig
[OSBS Repo Configuration File]: #osbs-repo-configuration-file
[distribution registry]: https://github.com/docker/distribution
