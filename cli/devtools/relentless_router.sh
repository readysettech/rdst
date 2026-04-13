#!/usr/bin/env bash
#
# relentless_router.sh — Analyze a CL diff and invoke relentless-tester areas
#
# Takes a diff, uses Claude to determine which relentless-tester areas are
# affected, then launches one Claude agent per area.
#
# Usage:
#   ./devtools/relentless_router.sh                     # uncommitted changes
#   ./devtools/relentless_router.sh HEAD~1              # last commit
#   ./devtools/relentless_router.sh abc123              # specific commit
#   ./devtools/relentless_router.sh HEAD~3..HEAD        # commit range
#   ./devtools/relentless_router.sh --dry-run HEAD~1    # show areas without running
#   ./devtools/relentless_router.sh --areas "cache help-system"  # skip routing, run these areas
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RDST_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$RDST_ROOT/test-results"
PROMPTS_DIR="$SCRIPT_DIR/prompts"
if [[ -n "${RELENTLESS_SKILL:-}" ]]; then
    SKILL_PATH="$RELENTLESS_SKILL"
elif [[ -f "$RDST_ROOT/skills/relentless-tester/SKILL.md" ]]; then
    SKILL_PATH="$RDST_ROOT/skills/relentless-tester/SKILL.md"
else
    SKILL_PATH="$(find "$RDST_ROOT/.." -path "*/skills/relentless-tester/SKILL.md" 2>/dev/null | head -1)"
fi

# Configuration
MODEL="${RELENTLESS_MODEL:-opus}"
BUDGET="${RELENTLESS_BUDGET:-5}"
MAX_TOTAL_BUDGET="${RELENTLESS_MAX_BUDGET:-0}"  # 0 = no limit
DRY_RUN=false
MANUAL_AREAS=""
DIFF_REF=""
MAX_PARALLEL="${RELENTLESS_PARALLEL:-3}"
OUTPUT_FORMAT="text"  # text or json
FULL_SCAN=false

# ── Parse arguments ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --areas)
            MANUAL_AREAS="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --budget)
            BUDGET="$2"
            shift 2
            ;;
        --skill)
            SKILL_PATH="$2"
            shift 2
            ;;
        --parallel)
            MAX_PARALLEL="$2"
            shift 2
            ;;
        --max-total-budget)
            MAX_TOTAL_BUDGET="$2"
            shift 2
            ;;
        --output-format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --full-scan)
            FULL_SCAN=true
            shift
            ;;
        --help|-h)
            cat <<'USAGE'
Usage: relentless_router.sh [OPTIONS] [DIFF_REF]

Analyze a CL diff and run relevant relentless-tester areas.

Arguments:
  DIFF_REF              Git ref or range. Default: uncommitted changes.

Options:
  --dry-run             Show which areas would run, don't execute
  --areas "a b c"       Skip routing, run these specific areas
  --model MODEL         Claude model (default: opus)
  --budget USD          Budget per area (default: 5)
  --skill PATH          Path to relentless-tester SKILL.md
  --parallel N          Max concurrent agents (default: 3)
  --max-total-budget USD  Cap total spend across all areas (default: unlimited)
  --output-format FMT   Output format: text (default) or json
  --full-scan           Ignore diff scoping and test the full selected area behavior
  -h, --help            Show this help
USAGE
            exit 0
            ;;
        *)
            DIFF_REF="$1"
            shift
            ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

timestamp() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(timestamp)] $*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Run claude without ANTHROPIC_API_KEY so it uses OAuth (Claude Max)
run_claude() {
    env -u ANTHROPIC_API_KEY claude "$@"
}

# ── Validate environment ────────────────────────────────────────────────────

command -v claude >/dev/null 2>&1 || die "claude CLI not found"

cd "$RDST_ROOT"

if [[ -z "$SKILL_PATH" || ! -f "$SKILL_PATH" ]]; then
    die "relentless-tester SKILL.md not found. Set RELENTLESS_SKILL env var or use --skill."
fi

log "Using skill: $SKILL_PATH"

# ── Get the diff ─────────────────────────────────────────────────────────────

log "Collecting diff..."

if [[ -z "$DIFF_REF" ]]; then
    DIFF=$(git diff HEAD 2>/dev/null || git diff)
    DIFF_DESC="uncommitted changes"
else
    if [[ "$DIFF_REF" == *..* ]]; then
        DIFF=$(git diff "$DIFF_REF")
        DIFF_DESC="range $DIFF_REF"
    else
        DIFF=$(git diff "${DIFF_REF}~1".."${DIFF_REF}")
        DIFF_DESC="commit $DIFF_REF"
    fi
fi

if [[ -z "$DIFF" && "$FULL_SCAN" != true ]]; then
    log "No changes found for $DIFF_DESC. Nothing to test."
    exit 0
fi

if [[ -n "$DIFF" ]]; then
    CHANGED_FILES=$(echo "$DIFF" | grep -E '^\+\+\+ b/' | sed 's|^+++ b/||' || true)
    log "Changed files ($DIFF_DESC):"
    echo "$CHANGED_FILES" | sed 's/^/  /'
else
    CHANGED_FILES=""
    log "Full scan mode — no diff required."
fi

# ── Get commit message (for routing context) ───────────────────────────────

COMMIT_MSG=""
if [[ -n "$DIFF_REF" && "$DIFF_REF" != *..* ]]; then
    COMMIT_MSG=$(git log --format="%B" -1 "$DIFF_REF" 2>/dev/null || true)
elif [[ -n "$DIFF_REF" && "$DIFF_REF" == *..* ]]; then
    # For ranges, get all commit messages
    COMMIT_MSG=$(git log --format="%s" "$DIFF_REF" 2>/dev/null || true)
fi

# ── Determine areas to test ─────────────────────────────────────────────────

if [[ -n "$MANUAL_AREAS" ]]; then
    # User specified areas directly
    read -ra AREAS <<< "$MANUAL_AREAS"
    log "Using manually specified areas: ${AREAS[*]}"
else
    log "Routing diff to relentless-tester areas..."

    ROUTER_PROMPT=$(cat "$PROMPTS_DIR/router_system.md")

    ROUTE_INPUT="## Commit Message
${COMMIT_MSG:-"(no commit message available)"}

## Changed Files
$CHANGED_FILES

## Diff
\`\`\`diff
$DIFF
\`\`\`"

    ROUTE_RESULT=$(run_claude -p \
        --model "$MODEL" \
        --max-budget-usd 1 \
        --system-prompt "$ROUTER_PROMPT" \
        --output-format text \
        --no-session-persistence \
        "$ROUTE_INPUT" 2>/dev/null) || die "Failed to route diff"

    # Extract JSON array from response (strip any non-JSON wrapper)
    AREAS_JSON=$(echo "$ROUTE_RESULT" | grep -o '\[.*\]' | head -1)

    if [[ -z "$AREAS_JSON" ]]; then
        die "Router returned no areas. Raw output: $ROUTE_RESULT"
    fi

    # Parse JSON array into bash array (compatible with macOS which lacks mapfile)
    AREAS=()
    while IFS= read -r line; do
        AREAS+=("$line")
    done < <(echo "$AREAS_JSON" | python3 -c "import sys, json; [print(a) for a in json.load(sys.stdin)]")

    log "Router selected ${#AREAS[@]} areas: ${AREAS[*]}"

    # ── Baseline tier-1 floor ──────────────────────────────────────────────
    # If any CLI code changed, always include these 5 areas regardless of
    # what the router picked. Catches cross-cutting regressions.
    BASELINE_AREAS=("help-system" "command-help-pages" "error-handling-bad-args" "interactive-menu" "consistency-audit")
    CLI_CHANGED=false
    while IFS= read -r file; do
        case "$file" in
            features/*|shared/cli/*|rdst.py)
                CLI_CHANGED=true
                break
                ;;
        esac
    done <<< "$CHANGED_FILES"

    if [[ "$CLI_CHANGED" == true ]]; then
        for baseline in "${BASELINE_AREAS[@]}"; do
            # Add only if not already present
            if ! printf '%s\n' "${AREAS[@]}" | grep -qx "$baseline"; then
                AREAS+=("$baseline")
            fi
        done
        log "CLI code changed — ensured baseline areas: ${BASELINE_AREAS[*]}"
    fi

    # ── Unmapped files signal ──────────────────────────────────────────────
    # Detect files that don't match any known file-to-area mapping hints
    UNMAPPED_FILES=()
    while IFS= read -r file; do
        [[ -z "$file" ]] && continue
        case "$file" in
            features/*|shared/cli/*|shared/ui/*|shared/config/*|shared/db_*|shared/password_*|\
            shared/query_registry/*|shared/llm*|shared/api/*|shared/env_*|shared/secret_*|\
            rdst.py|mcp_server.py|tests/*|web_dist/*)
                ;; # known mapping
            *)
                UNMAPPED_FILES+=("$file")
                ;;
        esac
    done <<< "$CHANGED_FILES"

    if [[ ${#UNMAPPED_FILES[@]} -gt 0 ]]; then
        log "WARNING: ${#UNMAPPED_FILES[@]} file(s) not covered by file-to-area mapping:"
        for uf in "${UNMAPPED_FILES[@]}"; do
            log "  UNMAPPED: $uf"
        done
        log "  These files get best-effort routing from the LLM. Consider adding mapping hints."
    fi
fi

if [[ ${#AREAS[@]} -eq 0 ]]; then
    log "No test areas to run."
    exit 0
fi

# ── Dry run? ─────────────────────────────────────────────────────────────────

if [[ "$DRY_RUN" == true ]]; then
    log "DRY RUN — would run these areas:"
    for area in "${AREAS[@]}"; do
        echo "  - $area"
    done
    exit 0
fi

# ── Prepare output directory ─────────────────────────────────────────────────

RUN_ID=$(date "+%Y%m%d_%H%M%S")
RUN_DIR="$RESULTS_DIR/relentless_$RUN_ID"
mkdir -p "$RUN_DIR"

echo "$DIFF" > "$RUN_DIR/diff.patch"
printf '%s\n' "${AREAS[@]}" > "$RUN_DIR/areas.txt"

log "Output: $RUN_DIR"
log "Model: $MODEL | Budget: \$$BUDGET/area | Parallel: $MAX_PARALLEL"
if [[ "$FULL_SCAN" == true ]]; then
    log "Mode: full scan (diff scoping disabled)"
fi

# ── Total budget guard ──────────────────────────────────────────────────

PROJECTED_COST=$((${#AREAS[@]} * BUDGET))
if [[ "$MAX_TOTAL_BUDGET" -gt 0 ]]; then
    if [[ "$PROJECTED_COST" -gt "$MAX_TOTAL_BUDGET" ]]; then
        die "Projected cost \$$PROJECTED_COST (${#AREAS[@]} areas x \$$BUDGET) exceeds --max-total-budget \$$MAX_TOTAL_BUDGET. Reduce areas, budget, or raise the cap."
    fi
    log "Budget check: \$$PROJECTED_COST projected, \$$MAX_TOTAL_BUDGET cap"
fi

# ── Launch agents ────────────────────────────────────────────────────────────

PIDS=()
AREA_MAP=()
RUNNING=0

launch_area() {
    local area="$1"
    local area_raw="$RUN_DIR/${area}.json"
    local focus_instructions

    if [[ "$FULL_SCAN" == true ]]; then
        focus_instructions="Run the full test area comprehensively. Do NOT limit testing to the changed files. Treat this as an area-wide bug hunt across the current codebase for this area."
    else
        focus_instructions="Focus your testing on functionality affected by these changes. Skip test cases for unrelated commands/features."
    fi

    log "Launching: $area"

    # Filter diff to only include hunks for files relevant to this area
    # (pass full diff so agent has complete context of what changed)
    run_claude -p \
        --model "$MODEL" \
        --max-budget-usd "$BUDGET" \
        --dangerously-skip-permissions \
        --system-prompt-file "$SKILL_PATH" \
        --output-format json \
        --no-session-persistence \
        "1 $area

SCOPE: This CL only changed these files:
$CHANGED_FILES

## Full Diff
\`\`\`diff
$DIFF
\`\`\`

$focus_instructions

IMPORTANT: When you are completely done, print a summary block in EXACTLY this format (no extra text inside the block):
\`\`\`RELENTLESS_RESULT
{\"area\": \"$area\", \"status\": \"pass|fail\", \"bugs_filed\": 0, \"tests_run\": 0, \"tests_failed\": 0, \"summary\": \"one-line description\"}
\`\`\`" \
        > "$area_raw" 2>&1 &

    local pid=$!
    PIDS+=("$pid")
    AREA_MAP+=("$area")
}

# Extract readable log and cost from the raw JSON output of a claude -p --output-format json run
process_area_output() {
    local area="$1"
    local area_raw="$RUN_DIR/${area}.json"
    local area_log="$RUN_DIR/${area}.log"
    local area_cost_file="$RUN_DIR/${area}.cost"

    if [[ ! -f "$area_raw" ]]; then
        echo "0" > "$area_cost_file"
        return
    fi

    python3 - "$area_raw" "$area_log" "$area_cost_file" <<'PYEOF'
import json, sys

raw_path, log_path, cost_path = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    with open(raw_path) as f:
        data = json.load(f)
    result_text = data.get("result", "(no result in output)")
    cost = data.get("total_cost_usd") or data.get("cost_usd") or 0
except Exception as e:
    result_text = open(raw_path).read()   # fallback: write raw content
    cost = 0

with open(log_path, "w") as f:
    f.write(result_text)

with open(cost_path, "w") as f:
    f.write(str(cost))
PYEOF
}

# Track start times using parallel arrays (bash3 compatible)
AREA_START_NAMES=()
AREA_START_TIMES=()

get_start_time() {
    local name="$1"
    for _k in "${!AREA_START_NAMES[@]}"; do
        if [[ "${AREA_START_NAMES[$_k]}" == "$name" ]]; then
            echo "${AREA_START_TIMES[$_k]}"
            return
        fi
    done
    echo "$(date +%s)"
}

for area in "${AREAS[@]}"; do
    # Throttle parallel launches
    HEARTBEAT=0
    while [[ $RUNNING -ge $MAX_PARALLEL ]]; do
        # Wait for any child to finish
        for i in "${!PIDS[@]}"; do
            if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
                wait "${PIDS[$i]}" || true
                ELAPSED=$(( $(date +%s) - $(get_start_time "${AREA_MAP[$i]}") ))
                log "Completed: ${AREA_MAP[$i]} (${ELAPSED}s)"
                unset 'PIDS[i]'
                unset 'AREA_MAP[i]'
                RUNNING=$((RUNNING - 1))
            fi
        done
        # Re-index arrays
        PIDS=("${PIDS[@]}")
        AREA_MAP=("${AREA_MAP[@]}")
        sleep 5
        HEARTBEAT=$((HEARTBEAT + 5))
        if [[ $HEARTBEAT -ge 60 ]]; then
            HEARTBEAT=0
            TMUX_STATUS=$(tmux list-sessions 2>/dev/null | grep -o 'test' | head -1 || true)
            for _a in "${AREA_MAP[@]}"; do
                _pid_for_area=""
                for _j in "${!AREA_MAP[@]}"; do
                    if [[ "${AREA_MAP[$_j]}" == "$_a" ]]; then
                        _pid_for_area="${PIDS[$_j]:-}"
                        break
                    fi
                done
                _cpu="?"
                if [[ -n "$_pid_for_area" ]]; then
                    _cpu=$(ps -p "$_pid_for_area" -o %cpu= 2>/dev/null | tr -d ' ' || true)
                    _cpu="${_cpu:-?}"
                fi
                _start=$(get_start_time "$_a")
                _elapsed=$(( $(date +%s) - _start ))
                _mins=$((_elapsed / 60))
                _secs=$((_elapsed % 60))
                log "  ⏳ $_a — ${_mins}m${_secs}s elapsed, CPU: ${_cpu}%${TMUX_STATUS:+ [tmux active]}"
            done
        fi
    done

    launch_area "$area"
    AREA_START_NAMES+=("$area")
    AREA_START_TIMES+=($(date +%s))
    RUNNING=$((RUNNING + 1))
done

# Wait for remaining
HEARTBEAT=0
while [[ ${#PIDS[@]} -gt 0 ]]; do
    for i in "${!PIDS[@]}"; do
        if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
            wait "${PIDS[$i]}" || true
            ELAPSED=$(( $(date +%s) - $(get_start_time "${AREA_MAP[$i]}") ))
            log "Completed: ${AREA_MAP[$i]} (${ELAPSED}s)"
            unset 'PIDS[i]'
            unset 'AREA_MAP[i]'
        fi
    done
    PIDS=("${PIDS[@]}")
    AREA_MAP=("${AREA_MAP[@]}")
    if [[ ${#PIDS[@]} -gt 0 ]]; then
        sleep 5
        HEARTBEAT=$((HEARTBEAT + 5))
        if [[ $HEARTBEAT -ge 60 ]]; then
            HEARTBEAT=0
            TMUX_STATUS=$(tmux list-sessions 2>/dev/null | grep -o 'test' | head -1 || true)
            for _a in "${AREA_MAP[@]}"; do
                _pid_for_area=""
                for _j in "${!AREA_MAP[@]}"; do
                    if [[ "${AREA_MAP[$_j]}" == "$_a" ]]; then
                        _pid_for_area="${PIDS[$_j]:-}"
                        break
                    fi
                done
                _cpu="?"
                if [[ -n "$_pid_for_area" ]]; then
                    _cpu=$(ps -p "$_pid_for_area" -o %cpu= 2>/dev/null | tr -d ' ' || true)
                    _cpu="${_cpu:-?}"
                fi
                _start=$(get_start_time "$_a")
                _elapsed=$(( $(date +%s) - _start ))
                _mins=$((_elapsed / 60))
                _secs=$((_elapsed % 60))
                log "  ⏳ $_a — ${_mins}m${_secs}s elapsed, CPU: ${_cpu}%${TMUX_STATUS:+ [tmux active]}"
            done
        fi
    fi
done

# ── Process raw JSON output → readable logs + cost files ─────────────────

for area in "${AREAS[@]}"; do
    process_area_output "$area"
done

# ── Extract results ──────────────────────────────────────────────────────

extract_result() {
    local area="$1"
    local area_log="$RUN_DIR/${area}.log"
    if [[ ! -f "$area_log" ]]; then
        echo "{\"area\": \"$area\", \"status\": \"error\", \"bugs_filed\": 0, \"tests_run\": 0, \"tests_failed\": 0, \"summary\": \"no output produced\"}"
        return
    fi
    local result
    result=$(sed -n '/^```RELENTLESS_RESULT$/,/^```$/p' "$area_log" | grep -v '^```' | head -1)
    if [[ -n "$result" ]]; then
        echo "$result"
    else
        local lines
        lines=$(wc -l < "$area_log" | tr -d ' ')
        echo "{\"area\": \"$area\", \"status\": \"unknown\", \"bugs_filed\": 0, \"tests_run\": 0, \"tests_failed\": 0, \"summary\": \"no structured result ($lines lines of output)\"}"
    fi
}

RESULTS=()
AREA_COSTS=()
TOTAL_BUGS=0
TOTAL_FAILURES=0
TOTAL_AGENT_COST=0
HAS_FAIL=false

for area in "${AREAS[@]}"; do
    result_json=$(extract_result "$area")
    RESULTS+=("$result_json")

    bugs=$(echo "$result_json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('bugs_filed', 0))" 2>/dev/null || echo 0)
    failures=$(echo "$result_json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('tests_failed', 0))" 2>/dev/null || echo 0)
    status=$(echo "$result_json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))" 2>/dev/null || echo unknown)

    # Read agent session cost from .cost file written by process_area_output
    area_cost=0
    cost_file="$RUN_DIR/${area}.cost"
    if [[ -f "$cost_file" ]]; then
        area_cost=$(cat "$cost_file")
    fi
    AREA_COSTS+=("$area_cost")
    TOTAL_AGENT_COST=$(python3 -c "print(round($TOTAL_AGENT_COST + $area_cost, 6))")

    TOTAL_BUGS=$((TOTAL_BUGS + bugs))
    TOTAL_FAILURES=$((TOTAL_FAILURES + failures))
    if [[ "$status" == "fail" ]]; then
        HAS_FAIL=true
    fi
done

printf '%s\n' "${RESULTS[@]}" > "$RUN_DIR/results.jsonl"

# ── Report ───────────────────────────────────────────────────────────────

if [[ "$OUTPUT_FORMAT" == "json" ]]; then
    python3 -c "
import json, sys

results = []
for line in sys.stdin:
    line = line.strip()
    if line:
        results.append(json.loads(line))

report = {
    'diff': '$DIFF_DESC',
    'areas_count': ${#AREAS[@]},
    'total_bugs': $TOTAL_BUGS,
    'total_failures': $TOTAL_FAILURES,
    'total_agent_cost_usd': $TOTAL_AGENT_COST,
    'overall_status': 'fail' if any(r['status'] == 'fail' for r in results) else ('pass' if all(r['status'] == 'pass' for r in results) else 'mixed'),
    'output_dir': '$RUN_DIR',
    'results': results
}
print(json.dumps(report, indent=2))
" < "$RUN_DIR/results.jsonl"
else
    log ""
    log "========================================"
    log "  RELENTLESS ROUTER REPORT"
    log "========================================"
    log "  Diff:      $DIFF_DESC"
    log "  Areas:     ${#AREAS[@]}"
    log "  Bugs:      $TOTAL_BUGS"
    log "  Failures:  $TOTAL_FAILURES"
    log "  Output:    $RUN_DIR"
    log ""
    log "  Area Results:"
    log ""

    idx=0
    for result_json in "${RESULTS[@]}"; do
        area=$(echo "$result_json" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['area'])")
        status=$(echo "$result_json" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['status'])")
        summary=$(echo "$result_json" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['summary'])")
        bugs=$(echo "$result_json" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['bugs_filed'])")
        area_cost="${AREA_COSTS[$idx]:-0}"
        cost_display=$(python3 -c "print(f'\${float(\"$area_cost\"):.4f}')")

        case "$status" in
            pass)    icon="PASS" ;;
            fail)    icon="FAIL" ;;
            error)   icon="ERR " ;;
            *)       icon="????" ;;
        esac

        if [[ "$bugs" -gt 0 ]]; then
            log "  [$icon] $area  cost=$cost_display  bugs=$bugs  $summary"
        else
            log "  [$icon] $area  cost=$cost_display  $summary"
        fi
        idx=$((idx + 1))
    done

    total_cost_display=$(python3 -c "print(f'\${float(\"$TOTAL_AGENT_COST\"):.4f}')")
    log ""
    log "  Total agent cost: $total_cost_display"
    log "========================================"
    log "Review logs: ls $RUN_DIR/"
    log "Structured results: cat $RUN_DIR/results.jsonl"
fi

# Exit non-zero if any area failed (useful for CI)
if [[ "$HAS_FAIL" == true ]]; then
    exit 1
fi
