# Using a Source Secret

The `prod-with-secret` build type allows you to provide some secret content to the build using [Kubernetes Secret Volumes](https://github.com/GoogleCloudPlatform/kubernetes/blob/master/docs/secrets.md).

This is useful when the build workflow requires keys, certificates, etc, which should not be part of the buildroot image.

The way it works is that a special resource, of type 'Secret', is manually created on the host. This resource is persistent and contains the secret data in the form of keys and base64-encoded values.

Then, when a build container is created, a secret volume is created by OpenShift from that resource and mounted within the container at `$SOURCE_SECRET_PATH`. The secret values can then be accessed as files.


## Creating the resource

First, create the Secret resource.

```
$ cat secret.json
{
  "apiVersion": "v1beta3",
  "kind": "Secret",
  "metadata": {
    "name": "mysecret",
    "namespace": "default"
  },
  "data": {
    "key": "-----BEGIN RSA PRIVATE KEY-----\n[...]",
    "cert": "-----BEGIN CERTIFICATE-----\n[...]"
  }
}
$ osc create -f secret.json
secrets/mysecret
```

When you need to change the data, you can use `update` instead of `create`.

## Configuring OSBS

In your OSBS build instance configuration, use the following values:

```
build_type = prod-with-secret
source_secret = mysecret
```

The `source_secret` name must match the resource name specified in the JSON.

## Fetching the secrets within the build root

In your `dock` plugin which needs the secret values, fetch the location of the Secret Volume mount from the environment variable `SOURCE_SECRET_PATH`. The files in that directory will match the keys from the secret resource's data (`key` and `cert`, from the JSON shown above).
