# Frozen RDST AI schema snapshot

`annotations.json` is the immutable output delta from RDST's annotation pipeline using
direct Anthropic `claude-sonnet-4-6`. It contains table descriptions, business context,
and column descriptions for 11 databases, 75 tables, and 798 columns. The model saw
auto-init schema profiles and up to five deterministically sampled rows per table.

The checkout does not contain raw sampled rows, schema types, relationships, profiled
enum values, BIRD questions, per-question evidence, gold SQL, BIRD-curated
descriptions, model call receipts, database credentials, or API keys. Sample-derived
examples inside generated descriptions remain part of the frozen model output.

`prepare` verifies the source auto-init content, applies the annotation delta, and
checks the reconstructed semantic content before installing it in the benchmark cache.
Regenerating it requires an explicit `enrich-schema` run and a new snapshot identity.
