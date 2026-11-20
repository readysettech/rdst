# rdst-helm

Runs RDST in server mode: Readyset's always-on query monitoring agent and its
web UI as one service with a persistent volume.

```bash
helm install rdst oci://registry-1.docker.io/readysettech/rdst-helm -n rdst --create-namespace
kubectl -n rdst get secret rdst-auth -o jsonpath='{.data.access-token}' | base64 -d
kubectl -n rdst port-forward svc/rdst 8787:8787
```

Open `http://localhost:8787`, paste the access token, sign in to Readyset and
connect a database in the UI. No values are required.

The chart creates the access token in the Secret `<fullname>-auth` on install
(`rdst-auth` for a release named `rdst`) and keeps it across upgrades, restarts
and uninstalls. `helm install` prints the command to read it.

Optional values you're most likely to set:

- `ingress.*`: serve it on a hostname. Use HTTPS.
- `targets`: databases RDST connects to on startup, each password from a
  Kubernetes Secret, so an agent or GitOps can install it already connected.
- `persistence.storageClass`: a block-storage class (not NFS).
- `auth.token` or `auth.existingSecret`: choose the access token yourself.
  Set one of these with Argo CD, which renders charts with `helm template`.
- `envFrom`: database passwords as `RDST_<TARGET>_PASSWORD` and
  `ANTHROPIC_API_KEY` from a Secret, if you keep credentials out of volumes.
- `networkPolicy.enabled` and `networkPolicy.databases`: limit traffic to the
  ingress controller, your databases and HTTPS.
- `notifications.slack.existingSecret`: send alerts to a Slack webhook.

Sign-in, secrets, notifications, upgrades and connecting to databases are
covered in the server mode guide:
https://github.com/readysettech/rdst/blob/main/deploy/README.md
