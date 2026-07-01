# Seed Data

This directory contains static seed files for first-deployment initialisation of the Model Registry.

## `models.json`

A pre-populated JSON array of one POC `ModelRecord` object conforming to the Model Registry schema:

| Name | Tasks | VRAM (GB) | Context Length | Fallback |
|---|---|---|---|---|
| `llama3.2-3b` | chat, summarization, reasoning, code | 3 | 8192 | — |

The record uses backend `ollama` and endpoint `http://inference-ollama:11434`.

`llama3.2:3b` was chosen as the sole POC model because it runs on CPU (~3 GB RAM), pulls quickly, and covers all POC task types adequately.

### How to use

Copy the file to the PersistentVolume before the Model Registry pod starts (or while it is scaled to zero):

```bash
# Replace <namespace> and <pvc-pod> with your actual values.
# Spin up a temporary pod that mounts the PVC, then copy the seed file into it:

kubectl run seed-loader \
  --image=busybox \
  --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"model-registry-data"}}],"containers":[{"name":"seed-loader","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n <namespace>

kubectl cp seed/models.json <namespace>/seed-loader:/data/models.json

kubectl delete pod seed-loader -n <namespace>
```

Or, if the registry pod is already running and you want to overwrite an empty store:

```bash
kubectl cp seed/models.json <namespace>/<model-registry-pod>:/data/models.json
```

### Important

> **This file is NOT auto-loaded by the application.**
>
> The Model Registry reads `/data/models.json` from the mounted PersistentVolume at startup.
> If the file is absent, the application creates an empty `[]` file automatically.
> To pre-populate the registry with these POC models, you must manually copy this file
> to the PVC before (or immediately after) first deployment, then restart the pod.
