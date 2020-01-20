# Using Kubernetes Secrets

You can provide some secret content to the build using [Kubernetes Secret Volumes](http://kubernetes.io/v1.1/docs/user-guide/secrets.html).

This is useful when the build workflow requires keys, certificates, etc, which should not be part of the buildroot image.

The way it works is that a special resource, of type 'Secret', is manually created on the host. This resource is persistent and contains the secret data in the form of keys and base64-encoded values.

Then, when a build container is created, a secret volume is created by OpenShift from that resource and mounted within the container at a specified path. The secret values can then be accessed as files.


## Creating the resource

### OpenShift

First, create the Secret resource:
```
$ oc secrets new mysecret key=/path/to/mykey cert=/path/to/mycert
secrets/mysecret
```

You also have to allow the build pod service account to [access the secret](https://docs.openshift.org/latest/dev_guide/service_accounts.html#managing-allowed-secrets).
```
$ oc secrets add serviceaccount/builder secrets/mysecret --for=mount
```

## Fetching the secrets within the build root

In your `atomic-reactor` plugin which needs the secret values, provide a configuration parameter to specify the location of the Secret Volume mount. The files in that directory will match the keys from the secret resource's data (`key` and `cert`, from the JSON shown above).
