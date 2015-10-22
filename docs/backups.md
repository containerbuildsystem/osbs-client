# OSBS Backups

## Creating Backups

Creating OSBS backups is very simple. To backup all relevant data, one needs to:

1. Pause OSBS by `osbs pause-builds`, which will prevent new builds being scheduled and wait for running builds to finish.
2. Backup data using following commands.
  * `oc export --output=json BuildConfig > buildconfigs.json`
  * `oc export --output=json ImageStream > imagestreams.json`
  * `oc export --output=json Build > builds.json` (backing up Builds is not actually necessary, but good for preserving history)
3. Resume builds by `osbs resume-builds`

Note: pausing builds is recommended, since builds can create new ImageStream objects during their execution and the backup data could theoretically get inconsistent - e.g. we could get a BuildConfig without an ImageStream for the image it creates or vice versa, depending on the order of `oc export` commands.

## Restoring from Backups

The assumption is, that we're importing data into a fresh OpenShift instance that is not yet accepting builds. To restore the data, run:

1. `oc create --filename buildconfigs.json`
2. `oc create --filename imagestreams.json`
3. `oc create --filename builds.json`
