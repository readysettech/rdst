# RDST server mode (Docker and Kubernetes)

In server mode, RDST runs as one long-lived service: the always-on monitoring
agent and the web UI in a single process. You open the UI in a browser, and
nothing else needs to run on your computer. The `readysettech/rdst` image
starts in server mode, whether you run it with Docker or the Helm chart.

- Image: `docker.io/readysettech/rdst` (linux/amd64 and linux/arm64)
- Helm chart: `oci://registry-1.docker.io/readysettech/rdst-helm`

It needs no configuration. Start it, open it, enter the access token, and
connect a database in the UI the same way you would in the desktop app.
Everything after the quick starts is optional.

## What server mode is

- **It's RDST's always-on agent and web UI as one service.** It runs in a
  container next to your databases, and people use it from a browser anywhere.
  The image turns it on (`RDST_SERVER=1`); the desktop app and the CLI never
  run in server mode.
- **Monitoring, Actions, the Inbox, analysis, Ask and the AI work the same as
  on the desktop.** Your Readyset account and its AI sign in from the browser
  at `localhost`, and so do Supabase and DigitalOcean. AWS uses the pod's IAM
  role, environment credentials or a device-code sign-in.
- **Every request needs the server's access token**, because anyone who can
  reach the port can reach the UI (see [Signing in](#signing-in)).
- **Secrets live on the data volume** in a file only RDST can read, since a
  container has no OS keychain (see [Secrets](#secrets)).
- **Alerts go to Slack and the Inbox.** There's no desktop to show system
  notifications, so Slack carries them while nobody has the UI open, and a
  browser tab on the UI can show browser notifications (see
  [Notifications](#notifications)).
- **Readyset comparisons, the Readyset Caching demo and local cache deploys
  stay in the desktop app.** They start their own Docker containers, which a
  server can't do. Requests for them return a "runs in the desktop app"
  message. Load tests and benchmarks measure your database only, health checks
  skip the Readyset benchmark, and Analyze's Readyset cacheability check still
  works. Deploying a Readyset cache to a remote host or a Kubernetes cluster
  still works too.
- **RDST finds databases in its cluster instead of on your computer.** "Search
  this cluster" lists the PostgreSQL and MySQL Services the pod can reach. RDST
  doesn't run a coding agent, open a terminal or an app, or edit an agent's
  settings, since those would run inside the container, out of your reach.
- **Claude Code or Codex on your own computer can still use RDST.** They connect
  to the server's `/mcp` endpoint over HTTPS with an agent key. "Connect Claude
  or Codex" on a database's page makes the key and gives the exact command (see
  [Coding agents](#coding-agents)).
- **Files RDST reads, such as SSH keys and TLS CA files, come from the server's
  filesystem.** Mount them into the container first (for example with the
  chart's `extraVolumes` at `/data/.ssh`).

## Quick start: Docker

Run RDST as a standalone agent on any host or VM that can reach your database:

```bash
docker run -d --name rdst --hostname rdst --restart unless-stopped \
  -p 8787:8787 -v rdst-data:/data readysettech/rdst
docker exec rdst cat /data/.rdst/access-token   # prints the access token
```

Open `http://localhost:8787` and paste the token. Then sign in to Readyset and
connect a database.

- Keep `-v rdst-data:/data`. Targets, monitoring history, the Readyset sign-in
  and saved passwords live there, so the volume is what survives a restart or
  an upgrade.
- Keep `--hostname` fixed, so a recreated container picks up monitoring at once.
- `--restart unless-stopped` brings it back after a crash or a host reboot.
- To reach a database on the Docker host, use `host.docker.internal` (add
  `--add-host host.docker.internal:host-gateway` on Linux).
- On a remote VM, reach the UI over SSH with
  `ssh -L 8787:localhost:8787 your-vm` and open `http://localhost:8787`. To
  serve it on a hostname instead, put a TLS proxy (Caddy, nginx) in front and
  set `-e RDST_PUBLIC_URL=https://rdst.example.com`. Readyset sign-in needs
  HTTPS on anything that isn't `localhost`.

## Quick start: Kubernetes (Helm)

```bash
helm install rdst oci://registry-1.docker.io/readysettech/rdst-helm \
  -n rdst --create-namespace
kubectl -n rdst rollout status statefulset/rdst
kubectl -n rdst get secret rdst-auth -o jsonpath='{.data.access-token}' | base64 -d
kubectl -n rdst port-forward svc/rdst 8787:8787
```

Open `http://localhost:8787`, paste the token, sign in to Readyset and connect
a database. The database host is whatever the pod can reach, for example
`mydb-rw.database.svc.cluster.local` (see
[Connecting to your databases](#connecting-to-your-databases)).

To serve it on a hostname instead of a port-forward, add an ingress with HTTPS:

```bash
helm upgrade rdst oci://registry-1.docker.io/readysettech/rdst-helm -n rdst --reuse-values \
  --set ingress.enabled=true --set ingress.className=nginx \
  --set ingress.hosts[0].host=rdst.example.com \
  --set ingress.hosts[0].paths[0].path=/ --set ingress.hosts[0].paths[0].pathType=Prefix \
  --set ingress.tls[0].hosts[0]=rdst.example.com --set ingress.tls[0].secretName=rdst-tls
```

- Use HTTPS on the ingress. Readyset sign-in uses browser crypto that only runs
  on secure pages (HTTPS, or `localhost`). With cert-manager, add the
  `cert-manager.io/cluster-issuer` annotation under `ingress.annotations`.
- On ingress-nginx, keep proxy buffering off for live updates:
  `nginx.ingress.kubernetes.io/proxy-buffering: "off"`.
- Use a block-storage class (EBS, Longhorn, local-path, Persistent Disk). The
  local stores are SQLite and need a real local filesystem, not NFS. If the
  cluster has no default StorageClass (a new EKS cluster doesn't), set
  `persistence.storageClass`.

Every option is in [`helm/rdst/values.yaml`](helm/rdst/values.yaml).

## Persistence and replicas

RDST keeps its state in SQLite databases under `/data`: monitoring history,
query snapshots, targets, the Readyset sign-in and saved secrets. SQLite allows
one writer, so one server owns one volume. That's why the chart runs exactly
one replica as a StatefulSet with its own volume and a stable pod name
(`rdst-0`), which lets a restarted pod take over monitoring immediately. A
second replica would be a second, separate RDST, so to monitor more databases,
add them to the same server.

When the pod restarts or moves to another node, the volume follows it and
nothing is lost. `helm uninstall` keeps the volume and the access-token Secret,
so reinstalling with the same release name picks up where it left off. Delete
the PVC (`data-rdst-0`) and the `rdst-auth` Secret to start over.

To back up RDST, back up the volume: a volume snapshot, or a copy of `/data`
taken while RDST is stopped.

## Upgrades

**Docker.** Pull the new image and recreate the container on the same volume:

```bash
docker pull readysettech/rdst
docker rm -f rdst
docker run -d --name rdst --hostname rdst --restart unless-stopped \
  -p 8787:8787 -v rdst-data:/data readysettech/rdst
```

**Helm.**

```bash
helm upgrade rdst oci://registry-1.docker.io/readysettech/rdst-helm -n rdst --reuse-values
```

Add `--version X.Y.Z` to pin a release. Data, the access token and everyone's
sign-in carry over. Database migrations run when the new version starts; the
startup probe allows them up to 10 minutes. Snapshot the volume first if you
want a way back.

## Signing in

On the desktop, the UI is only reachable from your own computer, so it needs
no password. A server is reachable by anyone who can reach its port or
hostname, and the UI can read your databases, run queries and spend your
Readyset AI credits. So every request has to prove it's allowed, and the
access token is that proof.

**Where the token is:**

| Install | Token |
| --- | --- |
| Docker | Generated on first start and saved at `/data/.rdst/access-token`: `docker exec rdst cat /data/.rdst/access-token` |
| Helm | In the Secret `<release>-auth`: `kubectl -n rdst get secret rdst-auth -o jsonpath='{.data.access-token}' \| base64 -d` |

- A browser signs in once and keeps a session cookie for 90 days. It stays
  signed in across browser and server restarts.
- Scripts send `Authorization: Bearer <token>`. `/health` is open for probes.
- **Choosing or changing the token.** With Docker, set `-e RDST_ACCESS_TOKEN=...`.
  With Helm, set `auth.token`, or point `auth.existingSecret` at a Secret you
  manage (SOPS, sealed-secrets, external-secrets). Changing `auth.token`
  restarts the pod and signs every browser out. After changing the token in
  your own Secret, restart RDST with
  `kubectl rollout restart statefulset/<fullname>`; until then the old token
  keeps working. Argo CD renders charts with
  `helm template`, which can't read back a generated token, so set one of these
  with Argo CD.
- **Turning it off.** If something in front already checks who people are
  (oauth2-proxy, an ingress auth middleware, a network only your team can
  reach), set `RDST_AUTH=none` (in Helm, `auth.mode: none`). The server then
  answers only host names it knows, so list the proxy's with
  `RDST_WEB_ALLOWED_HOSTS=rdst.example.com` (in Helm, under `env`). Browsers
  work as before; scripts and other API clients need a same-origin `Origin`
  header, so keep token auth if you automate against the API.

## Readyset and cloud provider sign-ins

**Your Readyset account** turns on AI monitoring, deep analysis and Ask,
exactly as in the desktop app. Readyset's sign-in returns only to `localhost`
for now, so open RDST through `kubectl port-forward`, a published Docker port
or an SSH tunnel, then click **Sign in** in the UI. The session is saved on the
data volume, so you can go back to the ingress hostname afterwards.

**Supabase and DigitalOcean** connect the same way, from RDST opened at
`localhost`: the provider's consent page returns there, and RDST then lists
your databases.

**AWS** uses a device code: RDST shows a code and a link, and you approve it in
your browser. On EKS it's simpler to give the pod an IAM role and skip the
sign-in, since RDST uses the AWS SDK's default credentials:

- IRSA: set `serviceAccount.annotations` to
  `{eks.amazonaws.com/role-arn: arn:aws:iam::<account>:role/<role>}`.
- EKS Pod Identity: associate a role with the `rdst` service account.
- Or provide `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION`
  through `envFrom`.

The role needs read access to RDS (`rds:Describe*`) for discovery.

## Secrets

Database passwords you type in the UI, an Anthropic API key if you add one, a
Slack webhook and the Readyset account session are saved on the data volume
in `/data/.rdst/secrets.json`. The file is readable only by the user the server
runs as (uid 10001, a non-root account the image creates so nothing runs as
root). Protect it the way you protect the volume: encrypted storage, and
backups with access control.

For anything installed by an agent or GitOps, pass database passwords as
environment variables from a Kubernetes Secret instead, so they never touch
RDST's disk. The chart's [`targets`](#with-helm-targets-at-install-time) list
does this for you. An environment variable always wins over the file:

| Secret | Environment variable |
| --- | --- |
| Password for a target named `shop` | `RDST_SHOP_PASSWORD` (or the name in the target's `password_env`) |
| Anthropic API key | `ANTHROPIC_API_KEY` |
| Slack webhook | `RDST_SLACK_WEBHOOK_URL` |
| This server's access token | `RDST_ACCESS_TOKEN` |

In Kubernetes, put them in a Secret and reference it from the chart:

```yaml
envFrom:
  - secretRef:
      name: rdst-db-passwords      # keys like RDST_SHOP_PASSWORD
```

## Coding agents

Claude Code, Codex or any MCP client on your own computer can ask RDST what's
slow and get the fix, the same way it does with RDST Desktop. It reaches the
server's `/mcp` endpoint over HTTPS with an agent key.

- **Getting a key.** On a database's page, click **Create connect command** in
  the "Connect Claude or Codex" card. It makes the key and shows commands with
  it filled in. For Claude Code:

  ```bash
  claude mcp add --transport http rdst https://rdst.example.com/mcp \
    --header "Authorization: Bearer rdst_ak_..."
  ```

  Codex reads the key from an environment variable, so its command sets
  `RDST_AGENT_KEY` first. The key is shown once.
- **One key at a time.** Creating a new command replaces the key, and agents
  using the old one lose access at once. **Revoke** removes it with no
  replacement. The card shows when the key was made and last used.
- **What it opens.** Only the agent tools at `/mcp`. The key can't open the UI
  or call any other API, and it can't create or revoke keys. The server stores
  only a hash of it (`/data/.rdst/agent-key.json`, mode 0600).
- **Who can reach it.** An agent connects from wherever the server is
  reachable, the same as a browser. Your ingress (a private network, a VPN, or
  an authenticating proxy) is the outer wall; the key is the lock behind it.
  Revoke the key if a laptop that had it is lost.

## Notifications

Monitoring runs whether or not anyone has the UI open. What it finds waits in
the Inbox, and the alerts the desktop app would show as system notifications
(a lost connection, a stuck query, a fix ready, the daily report) can go to
Slack instead:

1. In Slack, create an app from scratch at https://api.slack.com/apps, turn on
   **Incoming Webhooks**, click **Add New Webhook to Workspace** and pick the
   channel (or your own DM) to post to. Copy the webhook URL.
2. In RDST, open the Inbox, click the bell, paste the URL under Slack, click
   **Test**, then **Save**. Or set `RDST_SLACK_WEBHOOK_URL` on the container
   (in Helm, `notifications.slack.existingSecret`, a Secret with the URL under
   the key `webhook-url`).

The same per-database switches, quiet hours and daily cap decide what's sent.
Messages link back to the item when RDST knows its own address:
`RDST_PUBLIC_URL` (in Helm, `publicUrl`, which defaults to the ingress host).
The webhook URL is a secret: it's kept with the other secrets and never shown
again or written to logs. RDST only posts to `hooks.slack.com` and
`hooks.slack-gov.com`.

A browser tab left open on the UI (over HTTPS) can also show browser
notifications.

## Connecting to your databases

RDST connects to whatever the container or pod can reach. Networking between
RDST and the database is yours to arrange, the same as for any app you deploy.
There are three ways to connect one.

### With Helm targets, at install time

The best fit for an agent or GitOps doing the install: name the database in the
chart's values and RDST starts up already connected. The password comes from a
Kubernetes Secret as an environment variable and never touches RDST's disk.

```yaml
targets:
  - name: shop
    engine: postgresql            # or mysql
    host: pg-rw.database.svc.cluster.local
    port: 5432
    database: shop
    user: shop
    passwordSecret: {name: pg-app, key: password}   # CloudNativePG's app Secret
    monitoring: true              # turn monitoring on once it's added
```

On every start RDST adds or updates these databases. A name that's already in
RDST from the UI is left alone, and a database you remove from the list stays in
RDST until you delete it there. A bad entry is logged with the reason and
skipped; the rest still land. Each password arrives as `RDST_<NAME>_PASSWORD`
(`shop-db` becomes `RDST_SHOP_DB_PASSWORD`).

### With your coding agent

In the connect dialog, **Rather have your coding agent do it?** then **Show the
prompt** gives you a prompt for Claude Code, Codex or any agent that has
`kubectl` access to the cluster. Copying it creates an agent key and puts it in
the prompt (see [Coding agents](#coding-agents)). The agent finds the database
your app uses from the project's manifests, Helm values or `kubectl`, uses its
in-cluster hostname, reads the password from the app's Secret, adds it to RDST
through RDST's tools, runs the setup checks and helps turn monitoring on. It
asks before changing anything in the cluster or the database.

### By hand in the UI

**Search this cluster** lists the databases RDST can reach and fills in the
form; you paste the password. A password typed in the UI is saved in the
secrets file on the volume (see [Secrets](#secrets)).

### Databases in the same cluster

**Search this cluster** in the connect dialog lists the PostgreSQL and MySQL
Services and the CloudNativePG and Zalando clusters RDST can see. The chart
gives RDST read-only access to Services, Namespaces and those clusters, never
Secrets (`discovery.kubernetes.enabled: false` turns it off, and
`discovery.kubernetes.namespaces` limits it). You can also type the host
yourself. Use the database's Service DNS name, `<service>.<namespace>.svc.cluster.local`
(or `<service>.<namespace>` for short):

| Operator or chart | Host to use |
| --- | --- |
| CloudNativePG | `<cluster>-rw.<namespace>.svc.cluster.local` (the primary) |
| Zalando postgres-operator | `<cluster>.<namespace>.svc.cluster.local` |
| Bitnami PostgreSQL | `<release>-postgresql.<namespace>.svc.cluster.local` |
| Bitnami MySQL | `<release>-mysql.<namespace>.svc.cluster.local` |

Monitoring reads from the primary, so point RDST at the read-write Service.

### NetworkPolicy

With `networkPolicy.enabled: true`, the chart allows traffic in from
`networkPolicy.ingressFrom` (usually your ingress controller) and out to DNS,
HTTPS (for the Readyset account service), the Kubernetes API (for Search this
cluster) and the databases you list:

```yaml
networkPolicy:
  enabled: true
  ingressFrom:
    - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: ingress-nginx}}
  databases:
    - to: [{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: database}}}]
      ports: [{port: 5432, protocol: TCP}]
```

A database missing from `databases` looks like "Connection refused" or a
timeout. If your databases' namespace has its own NetworkPolicy, it also has
to allow traffic from the `rdst` namespace. NetworkPolicies only apply where the
network plugin enforces them: on EKS, turn on network policy in the VPC CNI
add-on (`enableNetworkPolicy: "true"`); Calico and Cilium enforce them out of
the box.

### Managed databases (RDS, Aurora, Cloud SQL, Azure)

- **RDS and Aurora from EKS:** the database's security group has to allow
  inbound traffic on its port from the cluster's node security group (or the
  pod's security group, if you use security groups for pods). Use the
  instance's endpoint as the host.
- **Cloud SQL from GKE:** connect to the instance's private IP from a
  VPC-native cluster. If you use the Cloud SQL Auth Proxy, run it as its own
  Deployment and Service and point RDST at that Service.
- **Azure Database:** allow the cluster's virtual network in the server's
  networking rules, or use a private endpoint.

### Databases outside the network

- **SSH tunnel:** RDST's SSH tunnel targets work in server mode. Mount the key
  with `extraVolumes` and `extraVolumeMounts` (for example a Secret at
  `/data/.ssh`, mode 0400) and choose that path in the connection form.
- **VPN or peering:** anything that makes the database's address routable from
  the pod works.
- **Public databases:** allow the cluster's egress IPs (or the Docker host's)
  in the database's firewall.

### TLS

The `sslmode` choices work as on desktop. To verify the server certificate
against a private CA, mount the CA file with `extraVolumes` and point the
connection's root certificate setting at it.

### What RDST needs on the database

RDST checks these when you connect and shows the exact fix for anything
missing, with the SQL to run.

**PostgreSQL:**

- `pg_stat_statements` in `shared_preload_libraries`, then
  `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;` in the database you
  connect to. Without it there are no query statistics to read.
- The `pg_read_all_stats` role (or `pg_monitor`, which includes it) for the
  user RDST connects as, so it can see every user's queries:
  `GRANT pg_monitor TO rdst;`
- Recommended: `track_activity_query_size = 4096` so long queries aren't cut
  off, and `pg_stat_statements.track = top`.

Where those settings live:

- **CloudNativePG:** set any `pg_stat_statements.*` parameter under
  `spec.postgresql.parameters` (for example `pg_stat_statements.track: top`)
  and the operator loads the library. Create the extension with
  `bootstrap.initdb.postInitApplicationSQL` or once by hand.
- **RDS and Aurora:** `pg_stat_statements` is preloaded by default (RDS 11+,
  Aurora 10+). Other settings go in a custom DB parameter group; a new instance
  uses a `default.*` group, which can't be edited. If `rds.allowed_extensions`
  is set, it has to include `pg_stat_statements`.
- **Zalando and Bitnami:** add `pg_stat_statements` to
  `shared_preload_libraries` in the cluster's PostgreSQL parameters (Bitnami:
  `primary.extendedConfiguration`).

**MySQL 8:**

- `performance_schema` on. It's a startup setting, so changing it needs a
  restart (on RDS, in the parameter group, then reboot).
- Read access for the RDST user: `GRANT SELECT ON performance_schema.* TO 'rdst'@'%';`
- The `global_instrumentation` and `statements_digest` consumers on (they're
  on by default). Deep analysis also uses `thread_instrumentation`,
  `events_statements_current` and `events_statements_history` to pick realistic
  parameter values.

### Troubleshooting

| What you see | What to do |
| --- | --- |
| `Connection refused: Cannot reach <host>:<port>.` | Check the database is running and the host and port are right. In Kubernetes, check `networkPolicy.databases`, the database's own NetworkPolicy, and security groups or firewalls. |
| `Connection timed out to <host>:<port>.` | Usually a firewall, security group or NetworkPolicy dropping traffic. |
| `could not translate host name` | The host name doesn't resolve from the pod. Use the full Service name, `<service>.<namespace>.svc.cluster.local`. |
| `Authentication failed for user '<user>'.` | Fix the password in the connection form, or the `RDST_<TARGET>_PASSWORD` variable if you set one. |
| `pg_stat_statements isn't set up, so there are no query statistics to read.` | Follow the setup fix RDST shows (see [What RDST needs](#what-rdst-needs-on-the-database)). |
| Readyset sign-in doesn't open or never finishes | Open RDST over HTTPS or on `localhost`. Plain HTTP on another hostname can't run the sign-in. |
| The pod stays `Pending` with an unbound PersistentVolumeClaim | The cluster has no default StorageClass. Set `persistence.storageClass`. |
| `401` from the API, or the sign-in page keeps coming back | The token doesn't match. Read it again from the Secret (Helm) or the data volume (Docker). |
| `421 Misdirected Request` with `auth.mode: none` | Add the hostname to `RDST_WEB_ALLOWED_HOSTS`. |
| `Remote requests require a same-origin Origin or Referer header` | You're calling the API without the token while `auth.mode: none` is set. Send the browser's `Origin`, or use token auth. |
| Ask or deep analysis says to sign in | Sign in to Readyset in the UI, or set `ANTHROPIC_API_KEY` in the server's environment (Helm `env` or `envFrom`). |

## Installing with an agent or GitOps

Everything an automated install needs:

1. Install the chart (or `docker run` the image) with no values. It starts on
   port 8787. Wait until `GET /health` returns 200.
2. Read the access token from the `<fullname>-auth` Secret (Helm prints the
   command after install) or
   `/data/.rdst/access-token` (Docker), or set it up front with `auth.token`,
   `auth.existingSecret` or `RDST_ACCESS_TOKEN`.
3. Connect the databases with the chart's `targets` list (passwords from
   Secrets), or give the person the URL and the token and connect them in the
   browser. Signing in to Readyset happens in the browser.
4. Persist `/data`. Keep one replica. Set `persistence.storageClass` if the
   cluster has no default StorageClass.
5. Use HTTPS for anything that isn't `localhost`, and set `publicUrl` if the
   address isn't the ingress host.
6. Allow egress to the databases and `keyservice.readyset.io:443`. If
   `networkPolicy.enabled`, list the databases under `networkPolicy.databases`.
7. Make sure each database has the settings in
   [What RDST needs](#what-rdst-needs-on-the-database).

With Fleet, Flux or Argo CD, point a Helm release at
`oci://registry-1.docker.io/readysettech/rdst-helm` and add only the values you
need: `ingress`, `auth.token` or `auth.existingSecret` (required with Argo CD),
`envFrom`, `persistence.storageClass`, `networkPolicy`.

## Network access

From the RDST pod or container:

- your databases;
- `keyservice.readyset.io:443` for the Readyset account, AI monitoring and
  analysis (required for monitoring);
- `hooks.slack.com:443`, if Slack notifications are on;
- `us.i.posthog.com:443` for anonymous usage telemetry, unless
  `RDST_TELEMETRY=false` (`telemetry.enabled: false`).

The browser additionally reaches Readyset's sign-in (Supabase, and Google or
GitHub if you use them) while you sign in to Readyset.

## Building the image

From the repository root:

```bash
pnpm --dir web-apps install --frozen-lockfile
pnpm --dir web-apps --filter rdst-web run build
cp -r web-apps/apps/rdst/dist/. rdst/web_dist/
docker build -t rdst rdst/
```
