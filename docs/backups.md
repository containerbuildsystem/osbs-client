# OSBS Backups

## Creating Backups

Creating OSBS backups is very simple. To backup all relevant data, one needs to:

1. Pause OSBS by `osbs pause-builds`
2. Wait for all running builds to finish
3. Backup data using following commands.
  * `oc export -o=json BuildConfig > buildconfigs.json`
  * `oc export -o=json ImageStream > imagestreams.json`
  * `oc export -o=json Build > builds.json` (backing up Builds is not actually necessary, but good for preserving history)
4. Resume builds by `osbs resume-builds`

Note: pausing builds is recommended, since builds can create new ImageStream objects during their execution and the backup data could theoretically get inconsistent - e.g. we could get a BuildConfig without an ImageStream for the image it creates or vice versa, depending on the order of `oc export` commands.

## Restoring from Backups

The assumption is, that we're importing data into a fresh OpenShift instance that is not yet accepting builds. To restore the data, run:

1. `oc create -f buildconfigs.json`
2. `oc create -f imagestreams.json`
3. `oc create -f builds.json`
