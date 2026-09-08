# Ask and Analyze schema prefix caching

Clarification and SQL generation put the same versioned, content-hashed schema
context at the start of their system messages. Questions, preferences, generation
instructions, and hosted JSON response instructions follow that prefix. The user
prompt references the context instead of repeating the schema.

Analyze instead keeps the existing schema collector for SQL-referenced tables,
including its column definitions, indexes, constraints, and available statistics.
Tables are collected in sorted order for a stable prefix. SQL, EXPLAIN plans,
and execution metrics follow that prefix. Analyze does not load the full semantic
layer merely to share Ask's cache. Offline YAML analysis uses the supplied schema;
no database is contacted to expand that input.

Both Ask steps use the complete schema selected by the existing adaptive formatter.
Verbose and compact formats sort tables, columns, and enum entries. No fields are
dropped. A schema or engine change changes the context hash. Context-size fallback
replaces the entire prefix with the lossless compact schema, causing a cold prefix.
No warm-up request, server-side schema upload, or local response cache is added.

This uses automatic provider prefix caching, not explicit cache-control blocks.
The full input still travels with each request. Support, minimum prefix length,
TTL, structured-output handling, and provider selection determine actual hits.
Ask and Analyze send a versioned SHA256 identity covering target, engine, and
the exact selected schema prefix. Keyservice derives an account-scoped HMAC
session ID from this identity. Different queries and workflows reuse it;
changed schema, target, engine, or prefix version produces a new identity.
Analyze uses its subset's identity, so matching table sets can reuse a session
without sharing Ask's full-schema key. Updated collected statistics can change
that identity too. Small subsets may fall below a provider's caching threshold;
sending less context is preferable to padding them for cache hits.
Workflow IDs remain separate for tracing. Clients without a valid schema key
retain the earlier Ask workflow-scoped routing or non-Ask priority.
Sessions omit explicit provider order because that overrides sticky routing.
The allowlist, price caps,
and permission to fall back remain unchanged. Fallback may lose cache locality. Non-hosted
providers may prepend structured-output contracts and invalidate cross-step reuse.
Do not assume that moving the schema guarantees a discount on every route.

Keyservice returns the upstream provider and allowlisted cached_tokens and
cache_write_tokens counts when reported. Hosted clients normalize these to
cache_read_input_tokens and cache_creation_input_tokens. Deploy the updated
Keyservice to expose these counts; older workers omit them.
Missing cache counts mean unknown, not an observed miss.
Quota charging continues to use provider-reported cost.

Before estimating savings, compare cold and repeated warm Ask runs against the
same schema and question. Record actual provider, prompt/completion tokens,
reported cached tokens, total billed cost, and latency. Repeat with a changed
schema and provider fallback. Compare SQL execution results against the baseline.
Unit tests establish request layout and fallback correctness, not model accuracy.

## Live validation, September 8, 2026

Five sequential staging-backed TPC-DS Ask runs returned the same ten rows and
columns as the baseline, after normalizing decimal serialization. Quota charges
were $0.003798, $0.004359, $0.002466, $0.002719, and $0.002877.

OpenRouter generation receipts for run four reported Cloudflare cache reads of
16,384/24,393 clarification input tokens and 23,232/23,276 generation input tokens.
In run five clarification hit Cloudflare's cache, but generation switched to
DeepInfra and reported zero cached tokens. This confirms actual repeat-request
cache hits and the provider-locality limitation. It does not isolate cross-step
reuse from same-task reuse, nor establish savings against a repeated warm baseline.
No quota projection should assume guaranteed clarification-to-generation hits.

The live test also exposed the older worker dropping cache details from its
response. The passthrough fix in this change is unit-tested but not deployed.

## Isolated cross-step validation

Two further runs used a different random prefix for each run, placed before all
schema text. Each run called clarification once, then generation once, so no
previous generation could warm that prefix. These tests used the real Ask
service and hosted message adapter, calling OpenRouter directly with Cloudflare
as the only provider. They preserved the model, token bounds, and price caps;
test-only provider pinning prevented provider changes from confounding results.
They did not exercise Keyservice authentication or quota accounting.

In both runs, clarification reported zero cached tokens and the first
generation reported 16,384 cached tokens. This demonstrates cross-step reuse,
not repeated-generation reuse. Both completed runs returned the same ten rows
as the baseline. A preceding attempt was rate-limited after clarification and
is retained separately; bounded retries were used for the subsequent tests.

Provider pinning was only used in the test, not added to the product. Sticky
routing improves locality but cannot guarantee a hit if a provider is unavailable
or its internal cache is cold. The routing change requires Keyservice deployment.

A third isolated run used the updated payload builder without provider pinning.
Both main calls used DeepInfra. Clarification read zero cached tokens; the first
generation read 14,592 of 23,302 input tokens from cache. Generation cost
$0.00091513 versus $0.00179065 calculated without the reported cache discount
at that response's input and output rates, about 49% less. Total four-call Ask
cost was $0.002953705. Its ten result rows also matched the baseline.
This test exercised the new routing payload against OpenRouter directly, not a
deployed worker. Raw receipts are under test-results/schema-cache-isolated-20260908
and test-results/schema-cache-sticky-20260908 in the local workspace.

## Full-schema Analyze experiment, superseded

With schema-scoped sessions and the shared complete serializer, two different
TPC-DS Ask questions returned the expected ten and five rows. Two subsequent
Analyze calls used real EXPLAIN ANALYZE plans for different item lookups and
completed successfully. Their workflow IDs differed, but all main calls used
the same schema key and upstream session ID.

The second Ask generation and the first Analyze each read 19,456 cached input
tokens from DeepInfra. The first Analyze was the first request of that task
type under a fresh test prefix, demonstrating Ask-to-Analyze reuse. The second
Analyze reported zero cached tokens despite the same provider and schema key.
Provider-internal cache locality still prevents guaranteed hits.

This test called OpenRouter directly through the updated payload builder, using
the normal provider allowlist and no provider pinning. Keyservice was not
deployed. Receipts and outputs are in
test-results/schema-cache-cross-query-20260908 in the local workspace.

This experiment is historical, not the current Analyze configuration. The full
schema cost more than earlier relevant-table analysis even on a cache hit, so
Analyze now uses the smaller SQL-referenced subset while retaining schema-scoped
routing. Ask continues to use the full lossless schema.

## Relevant-table Analyze validation

Retesting the same two item lookups with real EXPLAIN ANALYZE and the restored
subset completed successfully. DeepInfra reported 5,891 and 5,899 input tokens
and costs of $0.000491075 and $0.000515425. Both requests reported zero cache
reads, but were still cheaper than the full-schema experiment above. They shared
a schema routing key while retaining distinct workflow IDs. Results and receipts
are in test-results/schema-cache-analyze-subset-20260908.

Provider reference:
https://openrouter.ai/docs/guides/best-practices/prompt-caching
