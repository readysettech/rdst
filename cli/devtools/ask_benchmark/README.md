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

When a pre-merge change touches the RDST Python surface, the existing RDST pipeline
shows a **Run BIRD Live Qualification** block beside the Relentless Tester block. It
is placed after Gerrit status and has `blocked_state: passed`, so ignoring it or
getting a diagnostic failure cannot block the CL. Starting it requires exact paid-run
and BIRD-license confirmations and permits only the smoke or development-canary
suites. It runs matched direct and canonical Ask tracks for auto-init, RDST
AI-enriched, and BIRD-curated schema. All three omit BIRD question evidence. The job
has no automatic retry, and holdout and full runs are impossible through this mode.

After each context completes, the job updates a single Buildkite annotation with the
generated paired leaderboard. Completed contexts therefore remain visible inline if
a later context fails; the full immutable reports and receipts are also uploaded as
build artifacts.

The deterministic scripted `BenchmarkRunner -> AskService` integration contract
remains available to the repository's existing Python test workflows; it does not add
a dedicated Buildkite step or a second benchmark mode.

The block records explicit license acceptance in Buildkite metadata. Local scripted
runs may instead set `RDST_BIRD_LICENSE_ACCEPTED=1`. Preparation installs the frozen
Sonnet 4.6 RDST annotation snapshot from the checkout, so CI does not regenerate it
or need a private schema cache. `RDST_BIRD_CACHE_SOURCE` and
`RDST_BIRD_CACHE_S3_URI` remain optional download accelerators. The job also requires
`ANTHROPIC_API_KEY`. Configure credentials only in the Buildkite build or agent
environment.

Each of the six paid track/context runs accepts `--max-provider-calls`,
`--max-normalized-cost-usd`, and `--max-wall-time-seconds`. Call limits are cumulative
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

`auto-init` identifies schema provenance; it does not control clarification behavior.
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

The 50-case canary is the only development partition. The 450-case complement is
available only through `--suite holdout`: it rejects partial slices, requires one
model and one repetition, and requires an explicit run ID, shared campaign ID,
full gold-replay receipt, and `--confirm-open-holdout`. A durable cache ledger binds
that campaign to one resumable model-only run and one resumable `rdst-ask` run. Do
not open it until the pipeline is frozen.

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
direct Anthropic Sonnet 4.6, one model, one repetition, and an explicit run ID.

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

OpenRouter remains available for later model comparison against the frozen pipeline.

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
