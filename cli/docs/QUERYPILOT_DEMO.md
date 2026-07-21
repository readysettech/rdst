# Readyset Platform Demo

See the Readyset Platform compare PostgreSQL and Readyset side by side, with QueryPilot automatically caching the queries that matter most. The demo runs entirely on your computer and does not connect to your databases.

## Requirements

- `curl` or `wget`
- Docker running locally
- About 1.1 GB for a one-time container image download (the sample database ships pre-built)
- About 2 GB of free disk space while the demo is running

## Start the Demo

Install RDST and start the web app:

```bash
curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
rdst web
```

In RDST Web:

1. Open the **Demo** tab.
2. Select **Start demo environment**.
3. Wait for the local database and cache environment to become ready. The first run takes longer because RDST downloads the required images; the sample database arrives pre-built, so there is no data-loading wait.

If RDST asks for your email, it stores the address in your local RDST configuration and uses it for Readyset product updates and to associate this demo with product-usage analytics.

## What You'll See

The guided tour walks through the complete caching story:

1. Start a sample application workload.
2. Compare the same traffic going directly to PostgreSQL and through Readyset.
3. Inspect the query table and cache two suggested queries yourself.
4. Turn on QueryPilot and watch it select and cache queries automatically.
5. Change the caching policy and compare the result.

QueryPilot offers two policies in the demo:

- **Most frequent** caches the queries that run most often.
- **Most expensive** caches the queries that consume the most total execution time.

The throughput chart shows the effect of each change. The query table provides the receipts: select a status to see why a query was cached, passed through, or skipped, along with its rank, activity, and PostgreSQL and Readyset latency.

## Tear Down the Demo

Select **Tear down** when you are finished. RDST stops the workload and removes the demo containers and their data, so no demo environment is left running.

RDST also tears down the environment automatically after one hour, even if the browser is left open.

## Troubleshooting

### Docker isn't running

Start Docker Desktop or the Docker daemon, verify that `docker info` succeeds, and then select **Start demo environment** again.

### A port is already in use

Select **Tear down**, stop the program using the reported port, and retry. If setup stopped before the Tear down button appeared, restart `rdst web` and start the demo again.

### The first setup is slow

The first run downloads about 1.1 GB of images (the sample database ships pre-built inside one of them). On a slower connection this can take a few minutes. Later runs reuse the downloaded images and start in well under a minute.
