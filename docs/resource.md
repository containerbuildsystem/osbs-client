# Resource limiting

To set resource quota, use `oc create -f quota.yaml` on a file

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: myquota
spec:
  hard:
    # Number of parallel builds
    pods: 10
    # Compute units (KCU) for all builds
    cpu: "2"
    # Memory for all builds
    memory: 2Gi
    # Storage for all builds
    storage: 20Gi
```

Now `oc describe quota myquota` will show the used and total amount of each
resource type.

You can set default per-build limits either by setting them in the OSBS build
configuration file (`cpu_limit`, `memory_limit`, `storage_limit`), or by setting
defaults that OpenShift will apply to builds that do not specify their limits

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: limits
spec:
  limits:
  - default:
      # 100m == 0.1 KCU
      cpu: 100m
      memory: 512Mi
      storage: 2Gi
    type: Container
```

You can set per-build limits using the `--cpu-limit`, `--memory-limit`, and
`--storage-limit` OSBS command line options.

You can set quota for `pods`, `cpu`, `memory`, and `storage`, but you do not
have to set quota for all of them. If you set quota for `cpu`, `memory`, or
`storage`, you must also set limits for that resource: builds not specifying
limits are unbounded and will not be admitted.

You do not have to set limits for `pods`. Each build takes a fixed quantity (1
pod).
