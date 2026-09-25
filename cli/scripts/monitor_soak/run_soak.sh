#!/usr/bin/env bash
# Start or stop the local monitor soak; the backend is started separately.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOGS="$ROOT/docs/plans/continuous_analysis/logs/soak"
mkdir -p "$LOGS"
cd "$ROOT"
export RDST_DATA_DIR="${RDST_DATA_DIR:-$HOME/.rdst-monitor-dev}"
export LOCALDEV_PASSWORD="${LOCALDEV_PASSWORD:-rdst-localdev}"
export RDST_MONITOR_JUDGE_INTERVAL_S="${RDST_MONITOR_JUDGE_INTERVAL_S:-600}"

alive() {
  local name="$1" pid
  [[ -f "$LOGS/$name.pid" ]] || return 1
  pid="$(cat "$LOGS/$name.pid")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o args= | grep -Fq "$HERE/$name.py"
}

group_running() {
  local pgid="$1"
  ps -o stat= -g "$pgid" 2>/dev/null | grep -Eq '^[[:space:]]*[^Z[:space:]]'
}

stop_group() {
  local name="$1" pgid="$2" n
  kill -TERM -- "-$pgid" 2>/dev/null || true
  for n in $(seq 1 30); do
    group_running "$pgid" || break
    sleep 1
  done
  if group_running "$pgid"; then
    echo "$name exceeded 30s shutdown grace; sending SIGKILL (see $LOGS/$name.log)" >&2
    kill -KILL -- "-$pgid" 2>/dev/null || true
    for n in $(seq 1 5); do
      group_running "$pgid" || break
      sleep 1
    done
    if group_running "$pgid"; then
      echo "$name is still running after SIGKILL" >&2
      return 1
    fi
  fi
}

stop_one() {
  local name="$1" pid pgid seen=" " failed=0
  local pids=()
  if alive "$name"; then pids+=("$(cat "$LOGS/$name.pid")"); fi
  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then pids+=("$pid"); fi
  done < <(pgrep -f "$HERE/$name.py" || true)
  for pid in "${pids[@]}"; do
    pgid="$(ps -p "$pid" -o pgid= 2>/dev/null | tr -d ' ')"
    [[ "$pgid" =~ ^[0-9]+$ ]] || continue
    if [[ "$seen" == *" $pgid "* ]]; then continue; fi
    seen+="$pgid "
    stop_group "$name" "$pgid" || failed=1
  done
  if (( failed )); then return 1; fi
  unlink "$LOGS/$name.pid" 2>/dev/null || true
}

start_one() {
  local name="$1"
  setsid uv run --offline python "$HERE/$name.py" >>"$LOGS/$name.log" 2>&1 &
  echo "$!" >"$LOGS/$name.pid"
}

case "${1:-start}" in
  start)
    for name in load incidents checker; do
      if alive "$name" || pgrep -f "$HERE/$name.py" >/dev/null; then
        echo "soak already has a running $name process; stop it before starting" >&2
        exit 1
      fi
    done
    start_one load
    start_one checker
    start_one incidents
    echo "soak started; logs: $LOGS"
    ;;
  stop)
    failed=0
    stop_one incidents || failed=1
    stop_one load || failed=1
    stop_one checker || failed=1
    if (( failed )); then
      echo "soak stop incomplete; inspect the process logs in $LOGS" >&2
      exit 1
    fi
    echo "soak stopped"
    ;;
  status)
    for name in load incidents checker; do
      if alive "$name"; then echo "$name running"; else echo "$name stopped"; fi
    done
    ;;
  *)
    echo "usage: $0 [start|stop|status]" >&2
    exit 2
    ;;
esac
