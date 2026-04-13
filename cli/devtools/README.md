# relentless_router.sh

`relentless_router.sh` is a helper around Relentless Tester that studies your local diff, asks Claude which Relentless areas matter, and then launches those areas in parallel. It lives next to this README in `rdst/devtools/`.

## Prerequisites
- `claude` CLI installed and authenticated (OAuth is used because the script unsets `ANTHROPIC_API_KEY`).
- A checkout of the `skills/relentless-tester` repo, or a `RELENTLESS_SKILL` env var pointing at its `SKILL.md`.
- ReadySet repo with git metadata available (the script shells out to `git diff`).

## Basic Usage
```bash
# Analyze unstaged changes
./devtools/relentless_router.sh

# Route the previous commit
./devtools/relentless_router.sh HEAD~1

# Compare a range
./devtools/relentless_router.sh HEAD~3..HEAD
```

The script collects the diff, prints the files under test, asks the router prompt (`devtools/prompts/router_system.md`) which Relentless areas apply, and then kicks off one Claude agent per area. Logs land under `rdst/test-results/relentless_<timestamp>/`.

## Useful Flags
- `--dry-run` – only show which areas would run.
- `--areas "cache help-system"` – skip routing and run these areas directly.
- `--model opus` / `--budget 5` – change Claude model or USD budget per area.
- `--skill path/to/SKILL.md` – override the SKILL file path (or set `RELENTLESS_SKILL`).
- `--parallel 4` – control how many agents run at once (default 3).
- `--max-total-budget 30` – cap total projected spend across all areas; aborts if exceeded.
- `--output-format json` – emit a machine-readable JSON report to stdout (for CI/Gerrit integration).

## Environment Variables
Any of the tunables can be exported instead of passed as flags:

| Variable | Meaning | Default |
| --- | --- | --- |
| `RELENTLESS_MODEL` | Claude model for routing + areas | `opus` |
| `RELENTLESS_BUDGET` | Max budget per area (USD) | `5` |
| `RELENTLESS_PARALLEL` | Parallel agent cap | `3` |
| `RELENTLESS_MAX_BUDGET` | Total spend cap across all areas (USD), 0 = unlimited | `0` |
| `RELENTLESS_SKILL` | Path to Relentless SKILL | `rdst/skills/relentless-tester/SKILL.md` if present, otherwise auto-discovered |

## Outputs
Each run produces a timestamped directory under `rdst/test-results/` containing:
- `areas.txt` – list of areas that ran.
- `diff.patch` – exact diff passed to the router.
- `*.log` – full Claude transcripts per area.
- `results.jsonl` – one JSON object per area with status, bug count, and summary.

Tail or open those logs to review findings and copy/paste bug reports. Reruns create new timestamped folders, so the history of Relentless runs stays intact.

### JSON output for CI

```bash
# Get machine-readable report (e.g., for Gerrit comments or Slack notifications)
./devtools/relentless_router.sh --output-format json HEAD~1

# Exit code is non-zero if any area failed
./devtools/relentless_router.sh HEAD~1 && echo "All clear" || echo "Issues found"
```

The JSON report includes `overall_status` (`pass`, `fail`, or `mixed`), per-area results, and aggregate bug/failure counts.

## Troubleshooting
- **"claude CLI not found"** – install `claude` and re-auth.
- **"relentless-tester SKILL.md not found"** – supply `RELENTLESS_SKILL` or `--skill`.
- **Empty diff** – the script exits quietly when there are no changes between your working tree and the selected ref.
- **Router picked zero areas** – use `--areas` to force-run the suites you care about.

Happy routing!
