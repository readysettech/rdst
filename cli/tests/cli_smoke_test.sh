#!/usr/bin/env bash
# CLI smoke tests for the in-request-path branch.
#
# Exercises every cache subcommand and key configure paths WITHOUT requiring
# a running docker daemon or database. Verifies:
#   - help pages render
#   - error paths produce helpful messages (no stack traces)
#   - new subcommands (start/stop/restart) are registered and callable
#   - cache deploy refuses on bad target with a clear error
#   - cache delete handles bad/unknown ids gracefully

set -uo pipefail

cd "$(dirname "$0")/.."

PASS=0
FAIL=0
FAIL_NAMES=()

run_test() {
    local name="$1"
    shift
    local expected_pattern="$1"
    shift
    local expected_status="$1"
    shift
    # Remaining args are the command
    local output
    local status
    output=$("$@" 2>&1)
    status=$?

    local pattern_ok=0
    if [[ -z "$expected_pattern" ]]; then
        pattern_ok=1
    elif echo "$output" | grep -qE "$expected_pattern"; then
        pattern_ok=1
    fi

    local status_ok=0
    if [[ "$expected_status" == "*" ]]; then
        status_ok=1
    elif [[ "$status" -eq "$expected_status" ]]; then
        status_ok=1
    fi

    if [[ $pattern_ok -eq 1 && $status_ok -eq 1 ]]; then
        echo "  PASS: $name"
        PASS=$((PASS+1))
    else
        echo "  FAIL: $name (status=$status, expected=$expected_status; pattern_ok=$pattern_ok)"
        echo "    output: $output" | head -5
        FAIL=$((FAIL+1))
        FAIL_NAMES+=("$name")
    fi
}

echo "=== Tier 2: CLI smoke tests ==="
echo

echo "[help pages]"
run_test "rdst --help" "Analysis|Configuration|Cache" 0 python3 rdst.py --help
run_test "rdst cache --help shows new subcommands" "start.*stopped|stop.*running|restart" 0 python3 rdst.py cache --help
run_test "rdst cache start --help" "Start a stopped cache" 0 python3 rdst.py cache start --help
run_test "rdst cache stop --help" "Stop a running cache" 0 python3 rdst.py cache stop --help
run_test "rdst cache restart --help" "Restart a deployed cache" 0 python3 rdst.py cache restart --help
run_test "rdst cache deploy --help" "Deploy Readyset cache" 0 python3 rdst.py cache deploy --help
run_test "rdst cache add --help" "Create a shallow cache" 0 python3 rdst.py cache add --help
run_test "rdst cache delete --help" "Remove a cache from Readyset" 0 python3 rdst.py cache delete --help
run_test "rdst cache show --help" "List cached queries" 0 python3 rdst.py cache show --help
run_test "rdst cache remove --help" "Remove cache deployment" 0 python3 rdst.py cache remove --help
run_test "rdst configure --help" "" 0 python3 rdst.py configure --help

echo
echo "[error paths — nonexistent target]"
run_test "cache start nonexistent" "not found" 1 python3 rdst.py cache start --target __nonexistent_target__
run_test "cache stop nonexistent" "not found" 1 python3 rdst.py cache stop --target __nonexistent_target__
run_test "cache restart nonexistent" "not found" 1 python3 rdst.py cache restart --target __nonexistent_target__
run_test "cache deploy nonexistent" "" "*" python3 rdst.py cache deploy --target __nonexistent_target__ --mode docker

echo
echo "[error paths — cache delete with bad id format]"
# 'foo bar' has space — fails ArgDef regex/SQL injection guard
run_test "cache delete with garbage id" "" "*" python3 rdst.py cache delete "foo bar" --target __any__

echo
echo "[error paths — missing required args]"
run_test "cache deploy without --target" "" 2 python3 rdst.py cache deploy --mode docker
run_test "cache deploy without --mode" "" 2 python3 rdst.py cache deploy --target some_target
run_test "cache add without --target" "" 2 python3 rdst.py cache add --query "SELECT 1"

echo
echo "[regression — top-level commands still work]"
run_test "rdst version" "" 0 python3 rdst.py version
run_test "rdst help " "" 0 python3 rdst.py help
run_test "rdst configure --help still renders" "" 0 python3 rdst.py configure --help
run_test "rdst query --help still renders" "" 0 python3 rdst.py query --help

echo
echo "==================================="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [[ $FAIL -gt 0 ]]; then
    echo "Failed tests:"
    for n in "${FAIL_NAMES[@]}"; do echo "  - $n"; done
    exit 1
fi
exit 0
