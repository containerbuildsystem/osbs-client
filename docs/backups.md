# OSBS Backups

## Using osbs-client

OSBS client has `backup-builder` and `restore-builder` subcommands that automate
most of what needs to be done.

### `backup-builder`

To backup OSBS builder, simply run (you may have to add authentication and
instance/namespace selection options if needed)

```shell
osbs backup-builder
```

The command creates backup file named
`osbs-backup-<instance>-<namespace>-<timestamp>.tar.bz2`. You can use the
`--filename` argument to override the file name or write the backup to standard
output.

Please note that you need to be able to create/delete `resourcequotas` on the
builder in order to prevent new builds from being created while backup is in
progress, and read permission on `builds`, `buildconfigs` and `imagestreams`.

### `restore-builder`

Restoring builder data from a backup is as simple as

```shell
osbs restore-builder <osbs-backup-file>`
```

The backup is read from standard input if you use `-` as a file name. You need
the permission to create/delete `resourcequotas`, `builds`, `buildconfigs` and
`imagestreams`.

It is recommended to perform restore on freshly installed OpenShift with no
data, otherwise you'll end up with mix of original and restored data, or an
error in case some resource that you want to restore has the same name as one
that is already present. You can use the `--continue-on-error` flag if you want
to ignore such name clashes (and other errors) and import only the resources
that do not raise an error.

## Manually

Manual backup routines are performed with the OpenShift Client (through the `oc`
command), available in Fedora in the `origin-clients` package.

### Creating Backups

Creating OSBS backups is very simple. To backup all relevant data, one needs to
backup data using

- `oc export --output=json BuildConfig > buildconfigs.json`
- `oc export --output=json ImageStream > imagestreams.json`
- `oc export --output=json Build > builds.json` (backing up Builds is not
  actually necessary, but good for preserving history)

**NOTE**: pausing builds is recommended, since builds can create new ImageStream
objects during their execution and the backup data could theoretically get
inconsistent ― e.g. we could get a BuildConfig without an ImageStream for the
image it creates or vice versa, depending on the order of `oc export` commands.

### Restoring from Backups

The assumption is, that we're importing data into a fresh OpenShift instance
that is not yet accepting builds. To restore the data, run:

1. `oc create --filename buildconfigs.json`
1. `oc create --filename imagestreams.json`
1. `oc create --filename builds.json`
