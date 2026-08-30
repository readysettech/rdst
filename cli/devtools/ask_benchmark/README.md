# RDST Ask Benchmark

This development-only harness evaluates text-to-SQL accuracy and cost on BIRD Mini-Dev. It does not measure Readyset cache performance and does not replace RDST's production LLM manager.

## Setup

From `rdst/`:

```bash
printf '__version__ = "0.0.0.dev0"\n' > _version_build.py
mkdir -p web_dist
uv sync --group eval
```

Review the BIRD terms and CC BY-SA 4.0 dataset license before preparation. The benchmark stores BIRD artifacts under `~/.cache/rdst/benchmarks/bird-mini/`; dataset files remain outside the repository.

```bash
uv run --group eval python -m devtools.ask_benchmark prepare \
  --dialect mysql \
  --accept-license
```

Preparation downloads the pinned 500-question metadata and the official Mini-Dev archive, verifies the archive's published size and MD5-like ETag value, extracts the MySQL dump and schema descriptions, starts an isolated MySQL 8.4 Compose service, and creates 11 databases containing read-only views over the official combined BIRD schema.

The runtime defaults are:

```text
host: 127.0.0.1
port: 13316
user prefix: rdst_bird
password: rdst-benchmark
database prefix: bird_
```

Override them with `BIRD_MYSQL_*` environment variables or CLI arguments. Preparation derives one MySQL account from the user prefix for each BIRD database and grants that account access to only its matching database.

## CI mode

Every merged RDST main build starts one informational BIRD qualification step. It has
no dependency on release work and uses Buildkite `soft_fail`, so a below-baseline
score, provider failure, or infrastructure failure cannot fail the build or block a
release. The job pins the 50-case development canary, one paired repetition, and
fixed call, cost, and wall-time ceilings. It runs the auto-init, RDST AI-enriched,
and BIRD-curated context pairs concurrently. All six tracks use subscription Sonnet
4.6 and omit BIRD question evidence. The job has no automatic retry, and holdout and
full runs are impossible through this mode.

`prepare` owns exclusive global and cache locks. Benchmark runs hold shared locks on
the same files, allowing the three read-only contexts to overlap while preventing a
concurrent reprovision. This distinction is required: using the preparation lock
exclusively for the full benchmark serialized one context and made the other two fail
after the ten-second lock timeout.

For an isolated CI test, a disposable child CL whose only diff increments
`.buildkite/bird_live_test_trigger` emits exactly the automatic BIRD job. That child
and marker change must be titled `DO NOT MERGE`; neither is a product or permanent
pipeline setting.

Each context passes when canonical Ask has official execution accuracy equal to or
higher than direct Sonnet 4.6 on the same cases. A tie passes. The job completes all
three contexts before returning its overall verdict, so a failed context does not
hide the remaining comparisons. The Buildkite step is informational and is not a
merge or release gate.

After all three contexts complete, the job updates one Buildkite annotation with the
three paired leaderboards. The full reports and receipts are also uploaded as build
artifacts.

After all three contexts finish, the job posts one report to `#builds-rdst` through
Buildkite's configured Slack notifier. The message states whether Ask finished at or
above the direct Sonnet 4.6 baseline, lists both scores and the delta for every schema
context, and links the Buildkite build and its complete artifacts. Ties are labeled
separately and count as passing for the overall verdict. The report
step allows dependency failure, so an accuracy failure still reaches Slack.

The deterministic scripted `BenchmarkRunner -> AskService` integration contract
remains available to the repository's existing Python test workflows; it does not add
a dedicated Buildkite step or a second benchmark mode.

The CI invocation passes the benchmark's explicit BIRD license-acceptance flag.
Preparation installs the frozen Sonnet 4.6 RDST annotation snapshot from the checkout, so CI does
not regenerate it or need a private schema cache. `RDST_BIRD_CACHE_SOURCE` and
`RDST_BIRD_CACHE_S3_URI` remain optional download accelerators. The job uses the
`claude-sonnet-4.6-subscription-medium` configuration and loads the same one-year
`CLAUDE_CODE_OAUTH_TOKEN` secret as Relentless Tester. It does not require or expose
`ANTHROPIC_API_KEY`. Both direct and Ask calls pin `--effort medium` and
`CLAUDE_CODE_EFFORT_LEVEL=medium`; neither track uses `auto` or `max`. Receipts record
the actual thinking-token count. The job uses RDST's shared `ensure_node.sh` helper to
install a checksum-verified Node.js toolchain when the Buildkite image has no `npm`,
then installs the exact pinned Claude Code version into its temporary directory. A
checked-in `uv.lock` makes `uv sync --frozen` valid on a clean checkout.
The runner pins Python 3.10 and creates the ignored development-version and empty
`web_dist` inputs before the editable install. Release builds still generate the real
version and frontend bundle.

The complete job received a local cold run after the CI integration was wired. The
first attempt caught the ignored lockfile and Markdown-fenced validation-repair JSON.
V6 fixed both failures. The second cold run created a fresh virtual environment,
downloaded and provisioned BIRD into a fresh cache and MySQL volume, passed official
scorer conformance, and completed all six live tracks with 16/16 scored attempts each.
Under the old strict-greater-than rule, it rendered a below-baseline report and exited
1 because LLM-enriched and BIRD-curated tied their direct controls; auto-init passed
by one case. The current equal-or-better rule would pass those exact results.
The first Buildkite attempt then exposed a clean-checkout packaging defect:
`_version_build.py` and `web_dist/` are generated and ignored, so Hatch could not
build the editable package. The live runner now creates temporary stubs and removes
them during cleanup.

Each of the six live track/context runs receives pinned `--max-provider-calls`,
`--max-normalized-cost-usd`, and `--max-wall-time-seconds` values. Call limits are cumulative
across artifact resumes and stop before the next provider request. Cost is known only
after a response, so the final completed call may cross the configured cost boundary;
no later call is made. Wall time is enforced at case and provider-call boundaries.
All three limits are immutable manifest fields. A safe stop writes the partial report
and exits with status 4 so Buildkite cannot mistake it for a complete benchmark.

The checked-in `frozen_schemas/sonnet46-llm-enriched-v6` snapshot contains RDST's
Sonnet-generated annotation delta and sanitized provenance for 11 databases, 75
tables, and 798 columns. It contains descriptions only, not raw sampled rows, schema
types, relationships, profiled enum values, questions, evidence, gold SQL, curated
BIRD descriptions, call receipts, or credentials. Sample-derived examples in the
generated descriptions remain part of this cohort.

Regenerate a new snapshot explicitly after changing the annotation protocol:

```bash
export ANTHROPIC_API_KEY=...
uv run --group eval python -m devtools.ask_benchmark enrich-schema \
  --model claude-sonnet-4.6-anthropic-sdk \
  --run-id sonnet46-schema-enrichment-v2 \
  --sample-rows 5
```

The command writes input/output hashes, call receipts, setup cost, and structural
coverage under the BIRD cache. It does not modify the checked-in snapshot. Review,
assign a new snapshot identity, and vendor the generated semantic layers separately.
Questions, evidence, gold SQL, and BIRD-curated descriptions are not annotation
inputs.

## Models

Edit `model_matrix.toml` to change the scored matrix. Every OpenRouter entry pins an exact model slug and an allowed upstream provider. Router aliases and fallback routing are excluded.

```bash
export OPENROUTER_API_KEY=...

uv run --group eval python -m devtools.ask_benchmark doctor \
  --models kimi-k3-max,gpt-5.6-luna-max,grok-4.6-xhigh
```

`doctor` checks current model availability, provider routing, reasoning support, supported parameters, credentials, and pricing drift. A changed price remains visible so the run manifest captures the checked-in pricing snapshot. Provider data-policy flags are sent only when independently verified; omitted values remain recorded as unknown rather than inferred from route availability. First-party audit entries, when configured, remain on transport-specific leaderboards.

The benchmark-only `claude-subscription` transport invokes the pinned Claude Code CLI
with subscription OAuth. It is isolated with safe mode, no tools, no plugins, no
session persistence, one turn, disabled extended thinking, disabled nonessential
traffic, and zero CLI retries. Every response must report exactly
`claude-sonnet-4-6` through the first-party provider; auxiliary model calls fail the
attempt as an uncontrolled route. Structured response schemas are appended to the
explicit system prompt because Claude Code's schema flag performs an additional model
turn. Receipts record the CLI version, Claude session ID, result UUID, model usage,
response hash, normalized API-equivalent cost, and subscription billing identity.
Temperature, top-p, and stop sequences are recorded as unsupported by this transport.
This provider exists only under `devtools.ask_benchmark`; desktop and web Ask continue
to use production `LLMManager` routing.

## Runs

Verify the local scorer against the pinned official BIRD implementation before a paid run:

```bash
uv run --group eval python -m devtools.ask_benchmark conformance
```

This creates the sentinel-fixture receipt required for canaries. Publication of the
496-case scorable companion result still requires stored-candidate scorer replay.

`oracle-replay` is an optional manual publication and debugging check. It makes no
model calls and does not run in CI. It executes the 496 scorable BIRD gold queries and
records result fingerprints so independent database provisions can be compared. The
four released MySQL queries with invalid aliases, IDs 208, 212, 227, and 327, are
excluded rather than patched.

Replay the scorable gold queries twice and write an immutable comparison receipt:

```bash
uv run --group eval python -m devtools.ask_benchmark oracle-replay \
  --repetitions 2 \
  --output ~/.cache/rdst/benchmarks/bird-mini/gold-replay-before-restart.json
```

After a restart or independent reprovision, run it again with `--compare` pointing at the first receipt. Exact scoring remains unchanged; only declared optimizer-sensitive cases may change fingerprints.

After a complete 496-case scorable run, replay its stored candidates through the pinned official scorer functions:

```bash
uv run --group eval python -m devtools.ask_benchmark conformance \
  --run-dir test-results/ask_benchmark/<full-run-id>
```

A full run is publication-eligible only when this writes a zero-mismatch full-dataset receipt.

Model-only canary:

```bash
uv run --group eval python -m devtools.ask_benchmark run \
  --suite canary \
  --track model-only \
  --context evidence \
  --models kimi-k3-max,gpt-5.6-luna-max,grok-4.6-xhigh
```

Canonical `rdst ask` canary through direct Anthropic SDK Sonnet 4.6:

```bash
uv run --group eval python -m devtools.ask_benchmark run \
  --suite canary \
  --track rdst-ask \
  --context auto-init \
  --interaction-mode auto \
  --models claude-sonnet-4.6-anthropic-sdk
```

Subscription-backed development canary, using the same transport for direct and
canonical Ask tracks:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...

uv run --group eval python -m devtools.ask_benchmark run \
  --suite canary \
  --track rdst-ask \
  --context auto-init \
  --interaction-mode auto \
  --models claude-sonnet-4.6-subscription-medium
```

`auto-init` identifies schema provenance; it does not control clarification behavior.
Ask sends every loaded table. For semantic schemas it builds the complete verbose and
compact-v2 serializations, then uses compact only when it is strictly more than 15%
smaller by character count. Exactly 15% uses verbose. The run manifest binds the
adaptive policy, and attempt diagnostics record the selected format and both measured
sizes. Forced `verbose-v1` and `rdst-compact-schema-v2` are restricted to internal
smoke diagnostics. Ask lets the provider make the authoritative context-window
decision. If Anthropic rejects a verbose request for exceeding its context or 32 MB
request-body limit, Ask retries once with the lossless compact form even below the 15%
cost threshold. It does not retry unrelated invalid requests. If compact also fails,
Ask returns the provider request ID and size diagnostics without truncating the schema.

The experimental `auto-init-profiled-values` context keeps the same complete
auto-init schema and adds exact database values whose phrases occur in the question.
Build or verify its local, model-free index before running that cohort:

```bash
uv run --group eval python -m devtools.ask_benchmark profile-values
```

The index is generated from read-only database scans and remains under the BIRD cache;
it is not checked in. Frozen provenance asserts that questions, evidence, gold SQL,
and curated descriptions were not construction inputs. The matcher sends only matched
values and table/column locations, never the full value index or occurrence counts.
Both direct generation and canonical Ask receive the same matched block. This context
is benchmark-only until it passes the 50-case gate; desktop and web do not yet build
or inject this index.

### Exact-value selection algorithm

Profile construction and question-time selection are deterministic. They make no
model calls.

1. The builder scans non-null `text`, `varchar`, and `enum` values between one and
   160 characters. It retains at most 10,000 distinct values per column, ordered by
   row frequency and then value. It records each value's table, column, and occurrence
   count. Counts remain local.
2. The matcher applies Unicode NFKC normalization, case folding, punctuation removal,
   and whitespace collapsing to the question and indexed values.
3. It generates every contiguous question phrase up to eight tokens. A one-token
   phrase is eligible only when quoted or proper-name-like. The proper-name heuristic
   requires an uppercase character and ignores the question's first token, where
   capitalization alone is weak evidence.
4. It performs an exact normalized lookup. A second exact lookup removes a small fixed
   set of connector words such as `and`, `of`, and `the`. This permits punctuation and
   connector differences without inferring synonyms.
5. It rejects numeric-only values and common generic singletons, then deduplicates
   matches that resolve to the same occurrences.
6. It ranks longer phrases first, followed by longer character length, lower stored
   frequency, and normalized lexical order. This favors specific names over common
   fragments and makes ties reproducible.
7. Within a matched value, it ranks locations by overlap between question terms and
   normalized table/column identifiers. It splits camel case and underscores and
   treats simple plurals as the same term. Stored frequency breaks ties only for
   equivalent fields on the same table, such as `type` and `types`.
8. If a matched phrase also names part of a non-value column, it suppresses the value
   hint and records the schema collision. For example, `K-12` in a question about
   `Enrollment (K-12)` is treated as a column reference rather than a row value.
9. When the semantic layer contains declared relationships, it emits at most six
   shortest join paths of at most three edges among candidate tables. It never infers
   an undeclared relationship. Join paths are optional hints, not required joins.
10. It returns at most 12 values and eight table/column locations per value. The prompt
    includes ranked locations and declared join expressions. It excludes occurrence
    counts and the unmatched index.

The eight-token phrase limit, 12-value result limit, eight-location limit, 160-character
value limit, and 10,000-value column limit are conservative resource bounds. The smoke
qualified the policy as a whole; it did not optimize each constant independently.

Fuzzy search is intentionally absent. A close string is not necessarily the user's
database value, and a wrong near-match can produce valid SQL with plausible but wrong
rows. The first exact-match audit already found accidental common-word matches such as
`price`, `market`, and `member`; fuzzy matching would increase that candidate set.
Across hundreds of thousands of values, fuzzy retrieval also needs labeled typo cases,
a calibrated distance threshold, and a top-match margin before its output can be
trusted. The benchmark has none of those yet.

This policy therefore accepts case, punctuation, and connector variation but refuses
spelling correction, aliases, abbreviations, and semantic similarity. It cannot infer,
for example, that `phosphorus` means a stored code of `P`. It can also miss an unquoted
lowercase one-word name or a value outside a truncated 10,000-value column. A future
fuzzy cohort would need reviewed typo fixtures, an explicit approximate-match label,
high-margin abstention, and separate reporting. Do not silently add fuzzy matches to
this exact-value cohort.

### Exact-value 50-case gate

The fresh 50-case paired development gate did not reproduce the smoke's headline
gain. Canonical Ask scored 24/50 official EX and 23/48 stable EX in both conditions.
The stable paired delta was 0.0 points, with a 95% paired bootstrap interval of -8.3
to +8.3 points and an exact McNemar p-value of 1.0. The runs share the same revision,
working-copy diff, protocol hash, questions, model, interaction policy, schema format,
and database provision. Only the context cohort differs.

The matcher supplied 40 values to 23/50 questions. Within those 23 questions, accuracy
moved from 13/23 to 14/23. Profiling fixed q72 and q427, but regressed q412. q412's
`Creature` value appeared in both `cards.type` and `cards.types`; the extra grounding
changed a correct `cards.types = 'Creature'` predicate into an incorrect
`cards.type LIKE '%Creature%'` predicate. q11 moved from correct to incorrect despite
receiving no value context, which demonstrates residual model variance in a single
paired repetition.

Mean task latency moved from 6.20 to 6.07 seconds, P95 from 8.55 to 8.37 seconds, and
normalized cost from $0.588945 to $0.576915. The profiled run needed no validation
repair; the control used one on q72. Matched context averaged 221 characters and
never exceeded 318 characters.

The local index stores raw database values in mode-600 files. It occupies about 50 MB
for 11 BIRD databases, and 47 of 333 indexed columns reached the 10,000-value cap.
Those limits can omit rare values and do not establish acceptable scaling or privacy
for arbitrary desktop and web databases. Exact profiling therefore remains a
benchmark-only cohort. Do not enable it in product runtime based on this gate.

The post-gate matching policy is
`rdst-question-matched-values-v4-ranked-locations`. Offline replay changed the
grounding text for 10/50 development questions. It ranks `cards.types` ahead of
`cards.type` for q412 and suppresses the misleading `K-12` value match for q28. The
policy version is explicit in context provenance and the model configuration
fingerprint. Reports count matched questions, values, multi-location values,
schema-name suppressions, declared join paths, and mean grounding-context size.

The BIRD auto-init snapshots contain no relationships because preparation creates
read-only MySQL views and those views expose no foreign-key constraints. BIRD's
`dev_tables.json` contains relationship metadata, but importing it here would change
the cohort from database-introspected auto-init to BIRD-provided structure. The
matcher therefore emits zero join paths in this cohort. It can use declared
relationships in ordinary schemas, but BIRD has not tested that part of the policy.

The fresh paid v4 pair also finished flat. The control and ranked grounding condition
both scored 25/50 official EX and 24/48 stable EX. The stable paired delta was 0.0
points, its 95% paired bootstrap interval was -6.2 to +6.2 points, and exact McNemar
p was 1.0. The profiled condition matched 23 questions, supplied 39 values, ranked 15
multi-location values, and recorded one schema-name suppression. It emitted no join
paths.

Prompt hashes make the result easier to interpret. Grounding changed 23 prompts;
accuracy on those questions moved from 14/23 to 15/23, with q72 as the only correctness
flip. The other 27 prompts were byte-identical between conditions. q11 alone flipped
within that identical-prompt group, so it is model nondeterminism rather than a
grounding regression. q412 and q427 were correct in both v4 conditions, and q28
remained incorrect in both. Ranking removed the prior q412 regression but did not
produce a headline gain.

Mean latency moved from 6.03 to 5.99 seconds, P95 from 8.98 to 8.53 seconds, and
normalized cost from $0.587772 to $0.579291. The control used one repair and the
profiled condition used none. These operational differences are too small to motivate
product deployment. Keep v4 as benchmark evidence; do not run the remaining variance
repetitions or enable profiling in desktop and web without a new accuracy, storage,
and privacy mechanism.

### Three-candidate execution consistency

The next diagnostic reused the unprofiled v4 control as candidate one and ran two
fresh complete canonical `AskService` canaries under the same protocol, model,
schema, interaction policy, temperature, validation, and execution limits. Each
candidate was a real `rdst-ask` result. The combined selector remained an internal
diagnostic and was never added to `AskService`.

The offline analyzer re-executed all stored SQL and grouped successful non-empty
results by exact typed row multiset. Two matching results formed a majority. Empty
results could not override candidate one. With no majority, the selector kept
candidate one, or the first executable candidate if candidate one failed. Gold
results scored oracle pass@3 and selector EX but never influenced selection.

| Candidate | Official EX | Stable EX | Mean latency | Normalized cost |
|---:|---:|---:|---:|---:|
| 1 | 25/50 (50.0%) | 24/48 (50.0%) | 6.03s | $0.587772 |
| 2 | 24/50 (48.0%) | 23/48 (47.9%) | 6.11s | $0.589140 |
| 3 | 24/50 (48.0%) | 23/48 (47.9%) | 6.24s | $0.590280 |

Oracle pass@3 was still 25/50 official and 24/48 stable. The two additional
candidates recovered no candidate-one failure. Twenty-four questions were correct
in all three runs, 25 were wrong in all three, and q11 was correct only in candidate
one. All 150 candidate queries executed successfully, so validation or execution
success had no winner to identify.

Non-empty result consensus covered 46/50 questions and selected 24/50 correctly. It
lost q11 because candidates two and three agreed on the same wrong `Enrollment
(K-12) > 500` interpretation while candidate one used the gold-aligned sum of both
enrollment columns. Stable selector delta was -2.1 points, with a paired 95% interval
from -6.2 to 0.0 points and exact McNemar p-value 1.0.

Candidates two and three added $1.179420 normalized cost. Three-candidate serial
latency averaged 18.38 seconds. An idealized parallel estimate was 6.47 seconds, but
parallelism cannot rescue zero oracle headroom. Reject repeated temperature-zero
generation with the same prompt. Do not add this selector or generic same-prompt
retries to desktop and web.

The immutable final analysis artifact is
`rdst/test-results/ask_benchmark/sonnet46-rdst-ask-auto-init-three-candidate-consensus-50-v2/`.
The v1 analysis is superseded because its resume check did not exempt the three
predeclared unstable gold cases from fingerprint equality. The rejected selector
implementation was removed from the merge stack; these results are retained as
negative evidence.

### Rejected DIN-SQL-inspired decomposition

An internal no-evidence ablation tested the part of DIN-SQL that current Ask did not
already have. Sonnet first linked exact schema identifiers, then classified and
decomposed the question, then generated SQL with the production structured contract.
The treatment kept the complete adaptive auto-init schema, deterministic validation,
and one bounded validation repair. It did not use BIRD evidence, sample rows, NatSQL,
few-shot demonstrations, gold SQL, or DIN-SQL's generic self-correction. Call it
DIN-SQL-inspired, not a DIN-SQL reproduction.

The 16-case smoke improved from 6/16 to 9/16. It added q72, q173, and q427 without
losing a previously correct case. q72 and q427 used better table and join paths. q173
was only an execution-equivalence win: the treatment filtered a single transaction
amount, while the released gold query grouped orders and filtered their sum. The
database happened to return the same result.

The promoted 50-case development run rejected the approach:

| Condition | Official EX | Stable EX | Mean latency | Normalized cost |
|---|---:|---:|---:|---:|
| Canonical Ask source | 25/50 (50.0%) | 24/48 (50.0%) | 6.03s | $0.587772 |
| Decomposition treatment | 19/50 (38.0%) | 19/48 (39.6%) | 17.63s | $1.685376 |

Official paired delta was -12.0 points. Stable delta was -10.4 points. The treatment
gained q72 and q173 but lost q11, q412, q440, q671, q1375, q1460, q1486, and q1509.
q671 is the declared unstable tie case.

The plans caused the stable regressions. Schema linking omitted one of the two
enrollment columns on q11 and chose `cards.type` instead of `cards.types` on q412.
The plan discarded a useful value mapping on q440, added an unrequested ID column on
q1375, split one exact expense description into three values on q1460, and added an
extra boolean output on q1486. q1509 exposed a treatment-harness problem: its
standalone validator rejected a normalized date literal that the canonical source
run had accepted.

The source and treatment shared the same protocol hash, schema hashes, questions,
model route, and database provision, but came from different RDST revisions and
working-copy diffs. Treat the paired numbers as an internal rejection diagnostic,
not a publication-quality causal estimate. The large stable regression, plan-level
failure evidence, extra calls, and q1509 inconsistency are enough to reject this
candidate. Do not add the three-stage chain to desktop or web, repeat it on the
development set, or open the holdout for it.

The rejected decomposition runner was removed from the merge stack. The immutable
artifacts are:

- `rdst/test-results/ask_benchmark/sonnet46-din-sql-inspired-decomposition-smoke-16-v1/`
- `rdst/test-results/ask_benchmark/sonnet46-din-sql-inspired-decomposition-50-v1/`

`--interaction-mode auto` never converts an LLM-generated option into user intent.
The broad ambiguity model is not invoked in non-interactive mode. Only a deterministic
missing-intent rule, currently an explicit sort request with no stated or implied
direction, returns `clarification_required`. Generation otherwise receives the
unchanged question. Interactive product callers receive the normal resumable
clarification session. Historical positional-first, ranked-auto, and detector-observe
artifacts retain their original identities and must not be compared as the current
policy.
`--interaction-mode interactive-no-answer` records clarification events as scored
no-answer outcomes for a separate cohort.

The 50-case canary is the fixed development partition. The 450-case complement was
subsequently included in the opened 496-case transfer replay, so it is no longer an
unseen holdout and cannot support a release claim. The `--suite holdout` safeguards
remain for reproducibility of the historical partition, not as proof that it is
unopened. A future release qualification must establish a genuinely unseen set.

The latest latency audit found that model calls consumed 99.6% of the matched
16-case product task time. Sonnet clarification alone averaged 17.85 seconds and
accounted for 67.1% of task time; its output averaged 1,081 tokens even though none of
the 23 abstained options changed generation input. The ranked-v3 candidate capped
clarification output at 1,600 tokens, required concise evidence, stopped before
generation on ranked abstention, and bypassed the Haiku filter when the complete
schema had at most eight tables and 20,000 formatted characters. Reports now expose
per-stage call count, mean/P95 latency, mean input/output tokens, and filter-bypass
rate. They were treated as unvalidated candidates until the paid gate below; the
historical estimate was not itself a measured improvement.

The ranked-v3 paid gate is now complete. A fresh response-bound BIRD-gold alignment
passed all 5/5 safety actions and all 3/3 missing-option abstentions, with 7/10 option
coverage and 6/7 top-rank accuracy when covered. It exercised no automatic selection,
so ranking and general clarification-quality gates remain failed. The subsequent
matched smoke scored 5/16 for direct Sonnet and 2/16 for canonical `rdst-ask`, a
-18.8-point paired RDST delta with no RDST-only wins. RDST safely stopped 9/16 cases,
bypassed Haiku filtering on 10/16, and generated SQL for only seven. Mean task latency
fell from 28.53s in the prior product smoke to 12.78s and normalized cost from
$0.872038 to $0.586427, but the candidate is rejected for accuracy promotion. Do not
run the 50-case cohort. The immutable paired result is
`sonnet46-llm-enriched-ranked-v3-safe-smoke-16-paired-v1/`.

The replacement non-interactive policy then ran on the same 16 development IDs with a
fresh matched direct control. Direct scored 5/16 and canonical `AskService` scored
4/16, a -6.2-point paired delta with q72 as the only direct-only win. Ask made zero
automatic injections and zero clarification stops. The detector recommended
clarification on 9/16 cases, but those reports remained telemetry. Letting generation
run recovered q243 and q440 relative to the earlier safe-abstention realization.

This change improved completion but did not pass accuracy promotion. It also exposed
the next latency problem: 16 telemetry calls averaged 10.04 seconds and cost $0.425427,
56% of product cost, without changing one execution decision. Product mean latency
was 17.71 seconds versus 2.53 seconds direct. The next candidate should remove the
broad detector from the online non-interactive path, retain deterministic intent
checks, and keep full clarification for interactive callers. Do not run the 50-case
cohort from the current candidate. The paired artifact is
`sonnet46-llm-enriched-auto-observe-v1-smoke-16-paired-v1/`.

The next candidate removed that unused detector call from non-interactive
`AskService`. A fresh matched 16-case gate again scored 5/16 direct and 4/16 for
canonical `AskService`, with q72 as the only direct-only win. Ask made 22 model calls:
six schema-filter calls and 16 generation calls, with no clarification-model calls.
Mean product latency fell from 17.71 to 8.71 seconds and P95 from 28.63 to 17.77
seconds relative to the detector-observe run. Product normalized cost fell from
$0.764333 to $0.340811. Accuracy still fails promotion, so do not run the 50-case
cohort. The paired artifact is
`sonnet46-llm-enriched-auto-deterministic-v2-smoke-16-paired-v1/`.

The q72 input audit then reconstructed both calls and matched their recorded prompt
hashes. Direct and product generation received the same 14,381-character schema.
Product generation added 1,226 characters of user instructions plus the structured
response tool, but still treated a category phrase as a school name. A production
candidate added the previously tested enum-grounding instruction. The fresh matched
smoke remained 5/16 direct versus 4/16 product. q72 still failed after Ask added both
`School Name = 'State Special School'` and the unsupported inferred predicate `County
Name = 'Alameda'`. The prompt-only change was reverted. Its immutable paired artifact
is `sonnet46-llm-enriched-auto-enum-grounded-v1-smoke-16-paired-v1/`. The next bounded
candidate should test deterministic literal-provenance validation, not more prompt
wording.

That validation candidate passed its bounded matched gate. It parses generated string
filters and blocks only a narrow, reviewable error: a question-sourced value applied
to a known non-enum/free-text column when the same value is listed on an enum column.
Explicit requests for a named, called, titled, or known-as value are exempt. Literals
with no question, context, clarification, or enum source are advisory and do not cause
a retry by themselves. A blocking issue uses the existing single validation-repair
attempt; no general execution-repair loop was added.

The fresh direct control scored 6/16 and canonical `AskService` scored 5/16. The
direct-only q872 result is a fresh-realization difference, not a product regression.
Relative to the preceding accepted Ask realization, all four correct cases remained
correct and q72 was recovered. Its initial SQL incorrectly constrained both
`frpm.School Name` and `frpm.Educational Option Type` to `State Special School`.
Validation identified the enum-shadowed free-text predicate, one repair removed it,
revalidation passed, and execution matched gold. No other case retried. The repair
call took 6.71 seconds and cost $0.017841 normalized; product mean latency was 8.53
seconds and P95 was 14.41 seconds. The paired result remains a one-case RDST deficit,
so this accepts the targeted validator but still does not justify a 50-case run. The
immutable artifact is
`sonnet46-llm-enriched-auto-literal-provenance-v1-smoke-16-paired-v1/`.

The current pipeline subsequently completed the required three independent paired
50-case development repetitions. Direct official EX was 20/50, 19/50, and 20/50;
canonical `AskService` official EX was 20/50, 21/50, and 21/50. Aggregate official EX
was therefore 39.3% direct and 41.3% Ask. Stable EX was 38.9% direct and 41.0% Ask,
for a +2.1-point paired direction. A question-ID-clustered bootstrap that retains all
three repetitions gives a 95% interval of -3.6 to +9.0 points. Treat this as accuracy
parity with an inconclusive positive direction, not a demonstrated uplift.

The case pattern was stable. q412 and q1375 were Ask-only wins in all three
repetitions. q872 was wrong for Ask three times and correct for direct twice. q440 was
correct for direct three times and Ask twice. The other 46 questions had identical
per-track correctness counts. q72 used one successful validation repair in every Ask
repetition. q92 produced a contradictory structured answer in two Ask repetitions and
remains a product contract bug, although it was execution-incorrect in every direct
and Ask realization.

Current Ask averaged 7.57 seconds, 69 model calls, and $1.020819 normalized cost per
50-case repetition. The historical AI-schema Ask realization averaged 32.38 seconds,
made 150 calls, and cost $2.131546. These operational reductions follow directly from
removing the online noninteractive detector, bypassing unnecessary filters, and
shrinking generation output. The historical accuracy difference is not causal because
the protocol and model realization changed. The aggregate artifact is
`sonnet46-llm-enriched-current-pipeline-50-paired-3rep-v1/`. The 450-case holdout
remains unopened.

Two subsequent five-case hypotheses were rejected without a larger run. Moving the
detector behind generation caused all four evidence-dependent ambiguous cases to be
answered incorrectly and never invoked clarification. A global option-coverage prompt
kept safe abstention but left gold-option coverage at 7/10 and reduced top-rank
correctness from 6/7 to 4/7. Both executable changes were reverted. Their immutable
artifacts are `sonnet46-rdst-ask-generation-gated-qualification-5-v1/` and
`sonnet46-rdst-ask-ranked-v4-option-coverage-qualification-5-v1/`. Together they cost
$0.281709 normalized; no 16- or 50-case follow-up was run.

The following BIRD-curated/evidence factorial also stopped at its five-case gate.
Curated/no-evidence direct and canonical `AskService` each scored 1/5. With identical
per-question BIRD evidence, direct scored 2/5 and `AskService` remained at 1/5. Both
product cells required clarification on 4/5 cases, injected no options, and generated
SQL only for their one clear case. Evidence was present in the persisted product
question and detector reports: it cleared q11, partially resolved q28/q46, and caused
new q45 alternatives. Some abstentions were valid because q26 lacked the gold option
and q46's top list-vs-maximum option was wrong, so globally lowering the 0.90 threshold
is rejected. No 16-case continuation was run. Artifacts are
`sonnet46-bird-curated-qualification-5-paired-v1/` and
`sonnet46-bird-evidence-qualification-5-paired-v1/`; all four cells cost $0.489933
normalized across 22 clean Sonnet calls.

An isolated follow-up corrected the semantic channel without changing resolver gates,
schema, filtering, or generation requirements. Both tracks now receive identical
evidence bytes in an `AUTHORITATIVE CALLER-PROVIDED CONTEXT` block; `AskService`
threads that first-class context through clarification, generation, and validation
repair. Per-attempt length and SHA-256 diagnostics prove equality across the pair.

The new five-case evidence pair scored 2/5 direct and 2/5 canonical `AskService`.
q11 remained correct, q45 stopped producing evidence-induced alternatives and became
correct again, and q28 reached generation after its formula ambiguity disappeared.
Clarification stops fell from 4/5 to 2/5, mean clarification output from 820 to 416
tokens, and mean clarification latency from 12.80s to 7.01s. q26 still lacked the gold
meal-measure option, q46 still abstained on enrollment source, and q28 generated the
non-gold `DOCType` projection. This passes a representation-mechanism gate only; it
does not authorize 16 or 50 cases. Artifact:
`sonnet46-bird-evidence-authoritative-context-qualification-5-paired-v1/`.

`bird-curated` and `evidence` now fail closed unless `semantic/provenance.json` binds
the pinned dataset revision and archive, all BIRD metadata source hashes, all selected
YAML output hashes, and explicit false flags for question, evidence, and gold-SQL
content. The current receipt covers 76 source files and 11 output snapshots. An
independent exact-match scan found zero benchmark-question, evidence, or gold-SQL hits
in those YAMLs.

The qualification fixture is bound to BIRD's released gold SQL/evidence and to the
exact detector-response hashes for development IDs 11, 26, 28, 45, and 46. It never
passes BIRD evidence to the product. Bind the recorded output to those evaluator-only
labels with:

```bash
uv run --group eval python -m devtools.ask_benchmark clarification-align \
  test-results/ask_benchmark/<development-run-id> \
  --output /tmp/clarification-gold-aligned.json
```

Qualification reports gold-option coverage, top-rank accuracy when the gold option is
present, applied-selection correctness, missing-option abstention, and case-level
action accuracy:

```bash
uv run --group eval python -m devtools.ask_benchmark clarification-qualify \
  /tmp/clarification-gold-aligned.json \
  --output /tmp/clarification-qualification.json
```

Use `--replay-current-policy` to apply the current deterministic policy to an older
stored detector response without making another model call. The fixture fails closed
if BIRD changes, a case leaves the development partition, or detector output differs
from the response that was aligned. A changed detector realization therefore requires
a new development-only option alignment against released gold. Human review is still
required for claims about whether ordinary users perceive an ambiguity; those claims
belong to reviewed product fixtures or BIRD-Interact, not this BIRD-intent gate.

AI-enrichment preparation now orders samples by primary key, or by a fixed-width
SHA-256 digest of every structural column when no primary key exists. Provenance
records effective row counts, ordering columns, ordering strategy, and per-table
sample hashes; future snapshots are therefore reproducible and auditable without
storing sampled values in the repository.

The historical ranked-v2 smoke is a failed accuracy-promotion gate, not a headline
benchmark: direct scored 5/16 and `rdst-ask` scored 4/16. Alternatives were reported
on 16/16 cases, clarification was required on 11/16, and atomic resolution applied no
options. The fresh five-case BIRD-gold gate passed all five safety decisions, but
gold-option coverage remained 7/10 and top-rank accuracy was 5/7 when covered. Do not
spend on the 50-case cohort from that policy.

The frozen internal generation ablation separates schema scope from generation
contract without claiming any cell is `rdst-ask`. It replays the exact filtered-table
lists from a completed canonical smoke, uses the original questions with no evidence
or clarification injection, and crosses full versus filtered LLM-enriched schema with
SQL-only versus RDST structured generation. The v1 diagnostic scored 6/16 for
full/SQL-only, 5/16 for filtered/SQL-only, 3/16 for full/structured, and 5/16 for
filtered/structured. This small development diagnostic identifies a schema-by-contract
interaction, not a stable effect estimate: filtering cost one SQL-only case but gained
two net structured cases, while the structured contract cost three cases under full
schema. It supports fixing and requalifying generation before another 50-case run.

```bash
uv run --group eval python -m devtools.ask_benchmark generation-ablation \
  test-results/ask_benchmark/sonnet46-rdst-ask-llm-enriched-smoke-16-v3 \
  --run-id sonnet46-generation-ablation-smoke-16-v1
```

The command is frozen to direct Anthropic `claude-sonnet-4-6`, the 16 development
cases, 800 output tokens, original questions, and one balanced four-cell repetition.
Its manifest binds the source attempts, schema-enrichment provenance, case inputs,
database provision, route, and protocol. The artifact is an attribution diagnostic;
it is deliberately excluded from product leaderboards and publication claims.

The follow-up structured-system ablation changes only the system message while
holding the filtered schemas, production structured prompt and response schema,
questions, model, cap, and scorer fixed. Calls alternate which cell runs first. The
legacy generic system scored 4/16 and the explicit expert SQL system scored 5/16.
Question 427 was the sole expert-only win because the expert cell omitted an invented
`mcmId IS NOT NULL` predicate; there were no generic-only wins. Both cells still made
the same incorrect literal-school-name choice on question 72 and the same projection
mistake on question 1460. The +1 case is encouraging but below the variance gate and
does not qualify another 50-case run by itself.

```bash
uv run --group eval python -m devtools.ask_benchmark structured-system-ablation \
  test-results/ask_benchmark/sonnet46-rdst-ask-llm-enriched-smoke-16-v3 \
  --run-id sonnet46-structured-system-ablation-smoke-16-v1
```

This diagnostic writes a completion receipt binding its manifest, attempts, calls,
summary, and implementation hashes. The production candidate now uses the expert SQL
system message; it remains unfrozen pending the next canonical smoke gate.

The response-contract ablation then held the expert system message, filtered schema,
SQL requirements, questions, model, cap, and scorer fixed while comparing plain SQL
against the production seven-field JSON/tool response. Both cells scored the same
5/16 cases: 45, 243, 427, 440, and 1141. There were no correctness flips. Plain SQL
reduced mean input from 3,994 to 3,079 tokens, mean output from 350 to 77 tokens,
latency from 9.33s to 6.19s, and cost from $0.01723 to $0.01039 per case. It did not
repair questions 72 or 1460. Therefore the structured response is an efficiency cost
and can create format-consistency failures, but it is not the observed EX regression's
primary cause. The shared detailed product prompt is the next isolated variable.

```bash
uv run --group eval python -m devtools.ask_benchmark structured-response-ablation \
  test-results/ask_benchmark/sonnet46-rdst-ask-llm-enriched-smoke-16-v3 \
  --run-id sonnet46-structured-response-ablation-smoke-16-v1
```

The user-prompt ablation then held the expert system message, plain-SQL response,
filtered schema, question, model, cap, and scorer fixed. The lean direct-style prompt
scored 4/16; the detailed product prompt scored 5/16 and independently reproduced the
same five correct IDs as the prior plain-response cell. Lean fixed question 72 but
lost questions 243 and 1141 by adding unrequested columns and invented latest-row
logic. It was also slower and more verbose. The broad detailed-prompt regression
hypothesis is therefore rejected on this smoke; the remaining targeted failures point
to database-value grounding that is absent from the no-evidence semantic context.

```bash
uv run --group eval python -m devtools.ask_benchmark prompt-ablation \
  test-results/ask_benchmark/sonnet46-rdst-ask-llm-enriched-smoke-16-v3 \
  --run-id sonnet46-prompt-ablation-smoke-16-v1
```

This internal artifact cost $0.357375 across 32 controlled calls and has a completion
receipt binding its manifest, attempts, calls, summary, and implementation hashes.
Neither cell is labeled `rdst-ask`.

The next audit found that MySQL view row estimates are null/zero, so RDST had silently
skipped bounded enum discovery for every isolated BIRD view. The formatter also kept
only the first three enum values. The corrected path discovers 122 enum columns and
800 values, treats self-describing and empty-string values correctly, and serializes
the complete set. The provenance-valid v6 AI snapshot completed 81 calls for
$1.348878 setup cost. A preceding v5 attempt stopped after 77 successful calls on the
empty-string completeness bug and is retained only as an incomplete diagnostic.

The matched enum-complete canonical smoke scored 6/16 direct and 5/16 for ranked-auto
`AskService`, a -6.2-point RDST delta with a -25.0 to +12.5 point interval. Direct q72
used the newly visible school category and passed; Ask still chose literal-name
predicates. Both q1460 generations used the exact combined expense value, leaving a
separate-name versus concatenated-name scoring convention. The detector injected no
options, abstained 23 times, and consumed $0.512055 of the $0.872038 product cost. Do
not lower its uncalibrated threshold without reviewed labels. The next isolated
diagnostic changes only enum-grounding instructions in generation.

That paired instruction-only diagnostic was net-neutral: both the detailed baseline
and enum-grounded treatment scored 6/16. The treatment fixed q72 but lost q1460 on an
unrelated separate-versus-concatenated name projection. All 32 calls completed cleanly
for $0.390651. The rule is not promoted from this realization. The artifact JSON has
the correct treatment identity; its receipt-bound Markdown uses a stale `Lean-only`
label that is fixed in the renderer for future artifacts but intentionally preserved
in the immutable completed run.

The enum-complete canonical smoke now has an immutable BIRD-gold-aligned packet at
`clarification-gold-aligned-v2.json`. Its recorded ranked-v1 policy fails the safety
gate at 4/5 actions because q11 silently proceeds although the correct BIRD enrollment
formula is absent from its options. Replaying ranked-v2 over the same stored output
passes the safety gate at 5/5 with no selections or partial injection. Gold-option
coverage is 7/10 and top-rank accuracy when covered is 3/7, so neither artifact
supports calibrated-ranking or general clarification-quality claims.

The preregistered 16-case development smoke uses `--suite smoke`. It contains
qualification cases 11, 26, 28, 45, and 46;
generation regressions 72, 243, 427, 750, and 1460; validation failures 173 and 872;
clarification/context diagnostics 239, 349, 440, and 1141. The suite is frozen to
exact Sonnet 4.6 through either pinned direct SDK or pinned subscription transport,
one model, one repetition, and an explicit run ID.

The current subscription smoke uses explicit medium effort. Historical OpenRouter
artifacts labeled `claude-sonnet-4.6-max` remain valid only as max-effort model-only
experiments and are not the direct baseline for this CI qualification.

```bash
uv run --group eval python -m devtools.ask_benchmark run \
  --suite smoke --track model-only --context llm-enriched \
  --models claude-sonnet-4.6-anthropic-sdk \
  --run-id sonnet46-direct-llm-enriched-smoke-16-v1

uv run --group eval python -m devtools.ask_benchmark run \
  --suite smoke --track rdst-ask --context llm-enriched \
  --interaction-mode auto \
  --models claude-sonnet-4.6-anthropic-sdk \
  --run-id sonnet46-rdst-ask-llm-enriched-smoke-16-v1
```

OpenRouter model selection uses the v3 frozen pipeline receipt. The disabled-thinking
subscription configuration is preserved in v4 as historical provenance. The current
medium-effort subscription configuration starts with v5. V6 adds tolerant fenced-JSON
parsing for the bounded validation repair without weakening transport failures. Generation and the
single bounded validation repair allow up to 4,000 completion tokens because provider
reasoning shares that budget on reasoning-capable routes; the visible response remains
constrained by the concise seven-field SQL contract. The PydanticAI adapter forwards
the exact supplied JSON schema through a strict `StructuredDict` output tool. A generic
dictionary tool is not sufficient: it previously allowed missing fields, invalid enum
values, and out-of-range confidence values even though the route was labeled
structured.

Freeze receipts are immutable. v1 records the first post-experiment product pipeline;
v2 supersedes its 800-token completion ceiling; v3 supersedes the generic-dictionary
OpenRouter adapter; v4 starts the subscription-backed Sonnet development baseline.
Do not compare artifacts across these protocol hashes as though they were the same
run. Direct Anthropic Sonnet 4.6 does not execute the PydanticAI
adapter, so its v2 three-repetition baseline remains product-behavior evidence; the v3
confirmation cohort provides a protocol-compatible model-selection control.

## BIRD-Interact c-Interact qualification

The first c-Interact adapter is deliberately smaller than the official evaluator. It
uses the official pinned Mini-Interact public metadata and the repository's ten
published ADK example trajectories. Exactly two successful examples are read-only
queries and therefore compatible with `AskService`; their five published
clarification exchanges form the primary answer bank. Public non-critical ambiguity
labels provide deterministic answers for supported sort and limit questions that the
published trajectories did not ask. Three other successful examples perform
`UPDATE`, `CREATE FUNCTION`, or `CREATE VIEW`; Ask excludes them because it is
read-only.

Prepare the pinned public inputs and official PostgreSQL image:

```bash
uv run --group eval python -m devtools.ask_benchmark bird-interact-prepare \
  --accept-license
```

Run the canonical interactive path. The adapter calls `AskService.ask()`, answers only
questions that match a public BIRD label, applies BIRD's out-of-scope response
otherwise, and then calls `AskService.resume()`:

```bash
uv run --group eval python -m devtools.ask_benchmark bird-interact-qualify \
  --run-id sonnet46-bird-interact-public-label-v4
```

The historical paid v2 output reached generation on both tasks. It asked six
questions and matched five to labels. Both result sets matched as unordered sets. A
later audit found that the public `alien_3` metadata masks sort direction and gives
`ORDER BY avg_lif DESC` as the intended SQL. RDST had emitted ascending order. The
zero-call v3 proxy rescore therefore reports 1/2 instead of 2/2:

```bash
uv run --group eval python -m devtools.ask_benchmark bird-interact-rescore \
  test-results/ask_benchmark/sonnet46-bird-interact-frozen-transcript-qualification-v2 \
  --run-id sonnet46-bird-interact-frozen-transcript-qualification-v3-rescore
```

That failure was not loss of an explicit user instruction. The ambiguous question
said only "sorted by average interference." The v2 simulator omitted the public
non-critical sort label, so Ask had no way to obtain the intended direction.

The v4 product change adds a deterministic ambiguity when a question requests
sorting without a stated or implied direction. Its options tie, so non-interactive
mode abstains instead of guessing. A one-case `alien_3` diagnostic then asked for the
direction through canonical `AskService.ask()`, replayed the public `DESC` label
through `AskService.resume()`, and generated ordered-correct SQL. The authoritative
v5 artifact scored 1/1, matched all three generated questions to labels, took 22.49
seconds, and cost $0.033695 across three calls. It covered three of four labeled terms;
the detector still asked one column-oriented question that a normal product user may
not be able to answer.

The current interactive detector rejects implementation-facing questions before they
reach CLI or web. It also rejects duplicate options and options that claim the same
SQL effect. The web interaction remains one batched event with a sequential
question wizard, so this check does not add model round trips. Answer keys now use the
detector's stable ambiguity IDs. `resume()` rejects unknown or empty supplied answers
without consuming the session and records every answered or skipped question before
generation. The v5 result predates these checks and must not be used as evidence for
their quality.

This is qualification-only evidence, not an official c-Interact reward. The public
release omits ground-truth test cases, the sample is only two read-only tasks from one
database, and Ask batches its questions instead of conducting repeated detector
rounds. Semantic execution scoring uses the final generated SQL before the product's
display `LIMIT`; the actual limited SQL is stored separately.

The next official qualification set was preregistered before obtaining hidden ground
truth. The fixture
`bird_interact_official_qualification_v1.json` pins public revision
`f7881a9c2b9630cc4fc13b0c39279740b0a2fd87`, file hash
`d155fa0855bc1885f77df2fcc357d3056e10426cd6093c0042aa99d79067af08`,
and a deterministic 10-case Query-category sample spanning 10 databases. It excludes
the inspected `alien_3` and `alien_8` cases. Selection used only case IDs, category,
and database IDs; task text, SQL, and hidden tests were not inspected. Once the
official ground-truth file arrives, bind it by hash and run these 10 cases once.

Pass the same `--run-id` and settings to resume an interrupted run. Attempt keys prevent duplicate scoring.

Results are written under `test-results/ask_benchmark/<run-id>/`:

```text
manifest.json
attempts.jsonl
calls.jsonl
summary.json
summary.md
summary.html
```

`calls.jsonl` is written as model calls complete. `summary.html` is produced once every selected model has complete scored coverage.

Rebuild a report with:

```bash
uv run --group eval python -m devtools.ask_benchmark report \
  test-results/ask_benchmark/<run-id>
```

Legacy schema-v1 runs require `--output <directory>` so historical artifacts remain immutable.

Compare compatible runs and create a self-contained dashboard:

```bash
uv run --group eval python -m devtools.ask_benchmark compare \
  test-results/ask_benchmark/<run-a> \
  test-results/ask_benchmark/<run-b> \
  --output test-results/ask_benchmark/comparison
```

The comparison directory contains `leaderboard.json`, `leaderboard.csv`,
`leaderboard.md`, and `leaderboard.html`.

## Headline rule

For each track, context, and transport leaderboard:

1. Find the highest full-set execution accuracy.
2. Keep complete-coverage models within two percentage points of that result.
3. Select the lowest standardized cold cost per execution-correct answer.

Standardized cold cost uses each model's observed task-level token consumption and the pinned route prices. Provider-reported billed cost is shown separately and is never substituted into the headline leaderboard.

Scored failures and refusals count toward the denominator and headline cost. Unscored transport repairs remain in operational spend, repair-rate, and repair-spend diagnostics. The report also includes the accuracy-cost Pareto frontier, soft F1, strict multiset correctness, ordering diagnostics, confidence intervals, and pipeline failures.
