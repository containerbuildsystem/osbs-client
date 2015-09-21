# Using Kubernetes Secrets

The `prod` build type allows you to provide some secret content to the build using [Kubernetes Secret Volumes](https://github.com/GoogleCloudPlatform/kubernetes/blob/master/docs/secrets.md).

This is useful when the build workflow requires keys, certificates, etc, which should not be part of the buildroot image.

The way it works is that a special resource, of type 'Secret', is manually created on the host. This resource is persistent and contains the secret data in the form of keys and base64-encoded values.

Then, when a build container is created, a secret volume is created by OpenShift from that resource and mounted within the container at a specified path. The secret values can then be accessed as files.


## Creating the resource

### OpenShift >= 0.6.1

First, create the Secret resource:
```
$ oc secrets new mysecret key=/path/to/mykey cert=/path/to/mycert
secrets/mysecret
```

You also have to allow the build pod service account to [access the secret](https://docs.openshift.org/latest/dev_guide/service_accounts.html#managing-allowed-secrets).
```
$ oc secrets add serviceaccount/builder secrets/mysecret --for=mount
```

### OpenShift < 0.6.1

For older versions of OpenShift, create the secret from a JSON description:
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
build_type = prod
pulp_secret = mysecret
pdc_secret = myothersecret
```

`pulp_secret` and `pdc_secret` names must match the resource names specified in the JSON.

## Fetching the secrets within the build root

In your `atomic-reactor` plugin which needs the secret values, provide a configuration parameter to specify the location of the Secret Volume mount. The files in that directory will match the keys from the secret resource's data (`key` and `cert`, from the JSON shown above).
