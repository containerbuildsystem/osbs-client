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
 5. osbs-client starts the Openshift build from `BuildConfig` for *IM*.
 6. Openshift spawns a new build container that contains atomic-reactor inside.
 7. atomic-reactor's `CheckAndSetRebuildPlugin` is run. This determines whether the build is an automatically triggered autorebuild by examining `is_autrebuild` label. If this is autorebuild, then TODO. If this is not autorebuild, then the `is_autorebuild` label of the `BuildConfig` is set to `"true"`. This is done in order to ensure that all subsequent builds (except user-executed builds) are marked as autorebuild.
 8. atomic-reactor builds the image.
 9. Assuming the build is successful, atomic-reactor pushes the built image into registry and runs post-build plugins. These can vary depending on type of build, but usually it means pushing image to Pulp or copying it to NFS or so.
 10. atomic-reactor's `ImportImagePlugin` is run, which checks whether ImageStream for *IM* exists. If not, it creates it. Then, either way, it imports newly built *IM* into the `ImageStream`. If there are already some other images that use *IM* as their base image, they get rebuilt.
