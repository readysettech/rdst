#!/usr/bin/env bash
# Run the relentless UI tester against the web app and the Linux desktop
# package this build produced. Runs inside docker-compose.relentless.yml.
#
# With the argument "plan" it runs nothing: it checks the choice, records it
# against the selector's recommendation, and uploads one job per mode and per
# part (the change mission, each flow, each area) so they run side by side,
# then a job that reports them together. It needs only Node and the agent.
#
# Required:
#   RDST_RELENTLESS_ANTHROPIC_API_KEY  key the app under test uses for AI
#   RDST_RELENTLESS_CLIPROXY_API_KEY   the tester agent's key to CLIProxyAPI
#                                      (CLAUDE_CODE_OAUTH_TOKEN instead under
#                                      RELENTLESS_UI_AGENT=claude)
# Optional:
#   RELENTLESS_UI_AREAS        area names, "all", or "none"; the manual
#                              block's field when unset
#   RELENTLESS_UI_CHANGE       "run" or "skip": a change mission over the
#                              change the detector wrote (run first); the
#                              block's field when unset, else skip
#   RELENTLESS_UI_FLOWS        flow ids for one mission, or "none"; the
#                              block's field when unset, else none
#   RELENTLESS_UI_MODES        default: "desktop"
#   RELENTLESS_UI_PART         set in a job that is one part of a run: the
#                              plan recorded the choice, and the report job
#                              annotates the build
#   RELENTLESS_UI_JOB          the job's step key, which names its self-test
#   RELENTLESS_UI_VERIFY       set in the job that checks the parts' P1/P2
#                              findings on main's latest desktop package
#   RELENTLESS_UI_AGENT        the tester agent: pi (default) or claude
#   RELENTLESS_UI_MODEL        its model; default gpt-6.1-sol under pi
#   RDST_RELENTLESS_CLIPROXY_URL  CLIProxyAPI, default the build account's
#   RELENTLESS_UI_BUDGET       per-run agent budget in USD, default none
#   RELENTLESS_UI_TIMEOUT_MIN  per-run timeout, default 60
#   RELENTLESS_UI_DESKTOP_FLAVOR the desktop flavor whose package this build
#                              tests: cl (default) or staging
#   RELENTLESS_UI_DESKTOP_EXEC use this executable instead of the build's
#                              AppImage (local runs)
#   RELENTLESS_UI_RUNS_DIR     where runs go, cleared first; default
#                              test-results/relentless
set -euo pipefail
# A crash dump of the app or its keychain holds the keys it was given.
ulimit -c 0

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
WEB_APPS_DIR="${REPO_ROOT}/web-apps"
APP_DIR="${WEB_APPS_DIR}/apps/rdst"
RDST_ROOT="${REPO_ROOT}/rdst"
RUNS_DIR="${RELENTLESS_UI_RUNS_DIR:-${APP_DIR}/test-results/relentless}"
FLAVOR="${RELENTLESS_UI_DESKTOP_FLAVOR:-cl}"
PACKAGE_DIR="rdst/.buildkite-artifacts/rdst-desktop-linux-${FLAVOR}"
PNPM_VERSION="${PNPM_VERSION:-11.20.0}"
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-2.1.250}"
PI_VERSION="${PI_VERSION:-0.82.1}"
PI_CLIPROXYAPI_VERSION="${PI_CLIPROXYAPI_VERSION:-1.4.21}"
AGENT="${RELENTLESS_UI_AGENT:-pi}"
export RDST_RELENTLESS_CLIPROXY_URL="${RDST_RELENTLESS_CLIPROXY_URL:-http://10.3.153.177:8317}"
# The manual block's fields choose areas and modes when the environment does not.
if command -v buildkite-agent >/dev/null 2>&1; then
  : "${RELENTLESS_UI_AREAS:=$(buildkite-agent meta-data get relentless-ui-areas --default '')}"
  : "${RELENTLESS_UI_MODES:=$(buildkite-agent meta-data get relentless-ui-modes --default '')}"
  : "${RELENTLESS_UI_FLOWS:=$(buildkite-agent meta-data get relentless-ui-flows --default '')}"
  : "${RELENTLESS_UI_CHANGE:=$(buildkite-agent meta-data get relentless-ui-change --default '')}"
fi
# Account flows run when their secrets are set, against the keyservice the
# step names, else staging.
if [[ -n "${RDST_RELENTLESS_RESEND_API_KEY:-}" && -n "${RDST_RELENTLESS_ATTESTATION_SECRET:-}" ]]; then
  export RDST_RELENTLESS_KEYSERVICE_URL="${RDST_RELENTLESS_KEYSERVICE_URL:-https://keyservice-staging.readyset.io}"
fi
ALL_AREAS="$(basename -s .md "${APP_DIR}"/relentless/areas/*.md | tr '\n' ' ')"
# A blank choice is an error, not "everything": every area costs real usage.
case "${RELENTLESS_UI_AREAS:-}" in
  "") echo "ERROR: choose areas: area names, all, or none" >&2; exit 2 ;;
  none) AREAS="" ;;
  all) AREAS="$ALL_AREAS" ;;
  *) AREAS="$RELENTLESS_UI_AREAS" ;;
esac
FLOWS="${RELENTLESS_UI_FLOWS:-none}"
[[ "$FLOWS" == "none" ]] && FLOWS=""
for flow in $FLOWS; do
  if [[ ! -f "${APP_DIR}/relentless/flows/${flow}.md" ]]; then
    echo "ERROR: unknown flow ${flow}" >&2
    exit 2
  fi
done
CHANGE="${RELENTLESS_UI_CHANGE:-skip}"
[[ "$CHANGE" == run || "$CHANGE" == skip ]] \
  || { echo "ERROR: change mission must be run or skip, not ${CHANGE}" >&2; exit 2; }
for area in $AREAS; do
  if [[ ! -f "${APP_DIR}/relentless/areas/${area}.md" ]]; then
    echo "ERROR: unknown area ${area}; areas are: ${ALL_AREAS}" >&2
    exit 2
  fi
done
MODES="${RELENTLESS_UI_MODES:-desktop}"
for mode in $MODES; do
  [[ "$mode" == desktop || "$mode" == browser ]] \
    || { echo "ERROR: unknown mode ${mode}" >&2; exit 2; }
done
# A plan runs nothing, so it keeps earlier runs; a run starts clean.
[[ "${1:-}" == plan ]] || rm -rf "$RUNS_DIR"
mkdir -p "$RUNS_DIR"
# What the selector recommended and what ran, for its metrics, recorded before
# anything runs so a choice of nothing is measured too.
if command -v buildkite-agent >/dev/null 2>&1 && [[ -z "${RELENTLESS_UI_PART:-}" ]]; then
  meta() { buildkite-agent meta-data get "$1" --default ''; }
  RECOMMENDED="$(meta relentless-ui-recommended)" SELECTOR_STATUS="$(meta relentless-ui-status)" \
    RECOMMENDED_FLOWS="$(meta relentless-ui-recommended-flows)" CHOSEN_FLOWS="$FLOWS" \
    SUGGESTED_FLOWS="$(meta relentless-ui-suggested-flows)" \
    CHOSEN="$AREAS" CHOSEN_MODES="$MODES" \
    CHOSEN_CHANGE="$CHANGE" CHANGE_RANGE="$(meta relentless-ui-change-range)" \
    node -e 'const e = process.env; process.stdout.write(JSON.stringify({
      recommended: e.RECOMMENDED.split(" ").filter((a) => a && a !== "none"),
      status: e.SELECTOR_STATUS || null,
      chosen: e.CHOSEN.split(" ").filter(Boolean), modes: e.CHOSEN_MODES.split(" "),
      recommended_flows: e.RECOMMENDED_FLOWS.split(" ").filter((f) => f && f !== "none"),
      suggested_flows: e.SUGGESTED_FLOWS.split(" ").filter((f) => f && f !== "none"),
      chosen_flows: e.CHOSEN_FLOWS.split(" ").filter(Boolean),
      change_mission: e.CHOSEN_CHANGE === "run", change_range: e.CHANGE_RANGE || null,
      commit: e.BUILDKITE_COMMIT || null }, null, 2) + "\n")' \
    > "${RUNS_DIR}/selection-outcome.json"
fi
if [[ -z "$AREAS" && -z "$FLOWS" && "$CHANGE" == skip && -z "${RELENTLESS_UI_VERIFY:-}" ]]; then
  echo "No change mission, relentless UI areas, or flows chosen."
  exit 0
fi

# Flows of a mission that run in a mode.
flows_for() {
  local flow flow_modes out=()
  for flow in $FLOWS; do
    flow_modes="$(sed -n 's/^Modes: //p' "${APP_DIR}/relentless/flows/${flow}.md")"
    [[ " ${flow_modes//,/ } " == *" $1 "* ]] && out+=("$flow")
  done
  echo "${out[*]:-}"
}
# Whether an area runs in a mode; an area without Modes runs in both.
area_runs_in() {
  local area_modes
  area_modes="$(sed -n 's/^Modes: //p' "${APP_DIR}/relentless/areas/$1.md")"
  [[ -z "$area_modes" || " ${area_modes//,/ } " == *" $2 "* ]]
}

if [[ "${1:-}" == plan ]]; then
  step_sh="${REPO_ROOT}/rdst/.buildkite/relentless_ui_step.sh"
  plan="$(mktemp)"
  parts=()
  # The parts sign in to the keyservice this build's binaries name: a stack
  # that changes the keyservice gets its own CL env, once it has deployed.
  keyservice="${RDST_BUILD_KEYSERVICE_URL:-https://keyservice-staging.readyset.io}"
  keyservice_args=(--keyservice "$keyservice")
  [[ "$keyservice" == https://keyservice-cl*.readyset.io ]] \
    && keyservice_args+=(--depends keyservice-cl-env-deploy)
  part() { # KEY LABEL MODE CHANGE FLOWS AREAS
    parts+=("$1")
    bash "$step_sh" --key "$1" --label "$2" --depends "package-rdst-desktop-linux-${FLAVOR}" \
      "${keyservice_args[@]}" --env RELENTLESS_UI_PART=1 --env "RELENTLESS_UI_MODES=$3" \
      --env "RELENTLESS_UI_CHANGE=$4" --env "RELENTLESS_UI_FLOWS=$5" \
      --env "RELENTLESS_UI_AREAS=$6" --env "RELENTLESS_UI_DESKTOP_FLAVOR=$FLAVOR" >> "$plan"
  }
  echo "steps:" > "$plan"
  for mode in $MODES; do
    [[ "$CHANGE" == run ]] \
      && part "relentless-ui-${mode}-change" ":mag: ${mode} / change mission" "$mode" run none none
    # Each flow gets its own app: flows assume their own starting state, such
    # as a fresh install with no AI key.
    for flow in $(flows_for "$mode"); do
      part "relentless-ui-${mode}-flow-${flow//\//-}" ":dart: ${mode} / ${flow}" "$mode" skip "$flow" none
    done
    for area in $AREAS; do
      area_runs_in "$area" "$mode" \
        && part "relentless-ui-${mode}-${area}" ":robot_face: ${mode} / ${area}" "$mode" skip none "$area"
    done
  done
  report_args=()
  for key in "${parts[@]}"; do report_args+=(--depends "$key"); done
  # Before anything reaches the change, its serious findings are checked on
  # main, so the comment names only what main does not also do, against
  # staging, which runs main's keyservice.
  bash "$step_sh" --key relentless-ui-verify --label ":scales: Check the findings on main" \
    "${report_args[@]}" --allow-dependency-failure \
    --env RELENTLESS_UI_PART=1 --env RELENTLESS_UI_VERIFY=1 --env RELENTLESS_UI_MODES=desktop \
    --env RELENTLESS_UI_CHANGE=skip --env RELENTLESS_UI_FLOWS=none --env RELENTLESS_UI_AREAS=none \
    --env "RELENTLESS_UI_DESKTOP_FLAVOR=$FLAVOR" >> "$plan"
  bash "$step_sh" --report --key relentless-ui-report --label ":memo: Report the Relentless UI Tester" \
    "${report_args[@]}" --depends relentless-ui-verify \
    --env "RELENTLESS_REPORT_TITLE=Relentless UI tester" >> "$plan"
  buildkite-agent pipeline upload < "$plan"
  echo "Uploaded ${#parts[@]} tester jobs"
  exit 0
fi
# The detector wrote the change (the whole stack at its tip) as artifacts.
CHANGE_DIR=/tmp/rt-change
if [[ "$CHANGE" == run ]]; then
  rm -rf "$CHANGE_DIR" && mkdir -p "$CHANGE_DIR"
  buildkite-agent artifact download \
    "web-apps/apps/rdst/test-results/relentless-selector/change.*" "$CHANGE_DIR"
  CHANGE_DIR="${CHANGE_DIR}/web-apps/apps/rdst/test-results/relentless-selector"
  [[ -f "${CHANGE_DIR}/change.md" ]] \
    || { echo "ERROR: the detector wrote no change to test" >&2; exit 1; }
fi
limit_args=(--timeout-min "${RELENTLESS_UI_TIMEOUT_MIN:-60}")
[[ -n "${RELENTLESS_UI_BUDGET:-}" ]] && limit_args+=(--budget "$RELENTLESS_UI_BUDGET")

case "$AGENT" in
  pi) agent_secret=RDST_RELENTLESS_CLIPROXY_API_KEY ;;
  claude) agent_secret=CLAUDE_CODE_OAUTH_TOKEN ;;
  *) echo "ERROR: unknown agent ${AGENT}; use pi or claude" >&2; exit 2 ;;
esac
for name in RDST_RELENTLESS_ANTHROPIC_API_KEY "$agent_secret"; do
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: ${name} is required" >&2
    exit 2
  fi
done

# Benchmarks, health checks, and the demo start containers through the host
# daemon. Put them on this runner's Compose network, as
# run-postgres-e2e-ci.sh does, and remove the managed sandbox on exit.
if docker info >/dev/null 2>&1 && [[ -z "${RDST_DOCKER_NETWORK:-}" ]]; then
  RDST_DOCKER_NETWORK="$(
    docker inspect "$HOSTNAME" \
      --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
      2>/dev/null | sed '/^$/d' | head -n 1
  )"
  [[ -n "$RDST_DOCKER_NETWORK" ]] && export RDST_DOCKER_NETWORK
fi
cleanup_sandbox() {
  docker ps -aq \
    --filter "name=^/rdst-readyset-sandbox" \
    --filter "label=io.readyset.rdst.sandbox=true" \
    | xargs -r docker rm -f >/dev/null 2>&1 || true
}
trap cleanup_sandbox EXIT

# shellcheck source=/dev/null
source "${RDST_ROOT}/.buildkite/ensure_node.sh"
ensure_node

# The package first: a job whose build has none stops before any setup.
desktop_exec="${RELENTLESS_UI_DESKTOP_EXEC:-}"
if [[ " ${MODES} " == *" desktop "* && -z "$desktop_exec" && -z "${RELENTLESS_UI_VERIFY:-}" ]]; then
  echo "--- Unpacking the desktop AppImage from this build"
  cd "$REPO_ROOT"
  buildkite-agent artifact download \
    "${PACKAGE_DIR}/*.AppImage" .
  appimage="$(find "$PACKAGE_DIR" -maxdepth 1 -name '*.AppImage' -print -quit)"
  [[ -n "$appimage" ]] || { echo "ERROR: no AppImage artifact" >&2; exit 1; }
  chmod +x "$appimage"
  unpack_dir=/tmp/rdst-relentless-desktop
  rm -rf "$unpack_dir" && mkdir -p "$unpack_dir"
  (cd "$unpack_dir" && "${REPO_ROOT}/${appimage}" --appimage-extract >/dev/null)
  # Each flavor names its executable after itself, such as rdst-desktop-cl.
  desktop_exec="$(find "${unpack_dir}/squashfs-root" -maxdepth 1 -type f -executable \
    -regex '.*/rdst-desktop[a-z-]*' -print -quit)"
  [[ -n "$desktop_exec" ]] || { echo "ERROR: no rdst-desktop executable in the AppImage" >&2; exit 1; }
  # The driver needs --inspect, which a flavor that ships to users (staging) fuses off.
  node "${REPO_ROOT}/web-apps/apps/rdst-desktop/scripts/inspect-fuse.mjs" "$desktop_exec"
fi

# The CI machine's AWS credentials are for its agent, not for the app or the
# tester, and nothing in this container needs them: the app's AWS features see
# what a new user without credentials sees.
if ! iptables -I OUTPUT -d 169.254.169.254 -j REJECT 2>/dev/null; then
  if command -v buildkite-agent >/dev/null 2>&1; then
    echo "ERROR: cannot block instance metadata; the container needs NET_ADMIN and iptables" >&2
    exit 1
  fi
  echo "WARNING: instance metadata is reachable from this run" >&2
fi

echo "--- Preparing the relentless UI environment"
COREPACK_BIN="${COREPACK_BIN:-/tmp/rdst-relentless-corepack-bin}"
mkdir -p "$COREPACK_BIN"
corepack enable --install-directory "$COREPACK_BIN"
export PATH="${COREPACK_BIN}:${PATH}"
corepack prepare "pnpm@${PNPM_VERSION}" --activate

if ! command -v uv >/dev/null 2>&1; then
  UV_INSTALL_DIR=/tmp/uv-bin
  export UV_INSTALL_DIR
  mkdir -p "$UV_INSTALL_DIR"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${UV_INSTALL_DIR}:${PATH}"
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/rdst-relentless-uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/tmp/rdst-relentless-python}"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/tmp/rdst-relentless-venv}"
uv python install 3.12 --quiet

# Release builds generate these; stub them so uv can build the rdst wheel,
# as run-postgres-e2e-ci.sh does.
if [[ ! -f "${RDST_ROOT}/_version_build.py" ]]; then
  echo '__version__ = "0.0.0.dev0"' > "${RDST_ROOT}/_version_build.py"
fi
mkdir -p "${RDST_ROOT}/web_dist"
uv sync --directory "$RDST_ROOT" --quiet

# A shop application in both databases, beside the test data, with steady
# traffic for the whole job: monitoring, analyses and plans see a busy
# database, not only the statements the tester types.
echo "--- Seeding the shop workload"
shop_args=(
  --postgres "postgresql://rdst_test:${RDST_E2E_DB_PASSWORD:-rdst_e2e_password}@${RDST_E2E_DB_HOST:-127.0.0.1}:${RDST_E2E_DB_PORT:-15432}/rdst_test"
  --mysql "mysql://testuser:testpassword@${RDST_RELENTLESS_MYSQL_HOST:-127.0.0.1}:${RDST_RELENTLESS_MYSQL_PORT:-13307}/testdb"
  --scale 0.5
)
shop_python="${UV_PROJECT_ENVIRONMENT}/bin/python"
(cd "$RDST_ROOT" && "$shop_python" -m tests.app_sim.attached seed "${shop_args[@]}")
(cd "$RDST_ROOT" && exec "$shop_python" -m tests.app_sim.attached run "${shop_args[@]}") \
  >"${RUNS_DIR}/workload.log" 2>&1 &
shop_pid=$!
trap 'kill "$shop_pid" 2>/dev/null || true; cleanup_sandbox' EXIT

AGENT_PREFIX=/tmp/rdst-relentless-agent
if [[ "$AGENT" == pi ]]; then
  npm install --prefix "$AGENT_PREFIX" --silent \
    "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    "@router-for-me/pi-cliproxyapi-provider@${PI_CLIPROXYAPI_VERSION}"
  export RDST_RELENTLESS_PI_PROVIDER="${AGENT_PREFIX}/node_modules/@router-for-me/pi-cliproxyapi-provider"
  export PATH="${AGENT_PREFIX}/node_modules/.bin:${PATH}"
  pi --version
else
  npm install --prefix "$AGENT_PREFIX" --silent \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"
  export PATH="${AGENT_PREFIX}/node_modules/.bin:${PATH}"
  claude --version
fi
agent_args=(--agent-provider "$AGENT")
[[ -n "${RELENTLESS_UI_MODEL:-}" ]] && agent_args+=(--model "$RELENTLESS_UI_MODEL")

cd "$WEB_APPS_DIR"
pnpm install --frozen-lockfile
pnpm --filter rdst-web build

status=0
cd "$APP_DIR"
in_display() {
  if [[ "$1" == "desktop" ]]; then
    shift
    xvfb-run -a --server-args="-screen 0 1440x900x24" "$@"
  else
    shift
    "$@"
  fi
}

if [[ -n "${RELENTLESS_UI_VERIFY:-}" ]]; then
  echo "--- :scales: Checking the P1/P2 findings on main"
  parts_dir=/tmp/rt-parts
  rm -rf "$parts_dir" && mkdir -p "$parts_dir"
  runs_path="web-apps/apps/rdst/test-results/relentless"
  (cd "$parts_dir" \
    && buildkite-agent artifact download "${runs_path}/*/verdict.json" . \
    && buildkite-agent artifact download "${runs_path}/*/findings/*" .) || true
  in_display desktop node relentless/verify.ts --runs "${parts_dir}/${runs_path}" \
    --out "${RUNS_DIR}/baseline" -- "${agent_args[@]}" "${limit_args[@]}" || status=1
  MODES=""
fi

for mode in $MODES; do
  mode_args=()
  [[ "$mode" == "desktop" ]] && mode_args=(--desktop-exec "$desktop_exec")

  # The scripted self-test proves the driver and the real key before any
  # agent budget is spent on this mode. It is named after its job too, since
  # the report reads every job's.
  echo "--- :white_check_mark: ${mode} self-test"
  selftest="${RUNS_DIR}/selftest-${mode}${RELENTLESS_UI_JOB:+-${RELENTLESS_UI_JOB}}"
  if ! in_display "$mode" node relentless/selftest.ts --mode "$mode" \
      "${mode_args[@]}" --run-dir "$selftest" > "${selftest}.json"; then
    cat "${selftest}.json"
    echo "ERROR: ${mode} self-test failed; skipping its areas" >&2
    status=1
    continue
  fi

  # The change mission first: what this change, or its stack, could break.
  if [[ "$CHANGE" == run ]]; then
    echo "--- :mag: ${mode} / change mission"
    in_display "$mode" node relentless/run.ts "${agent_args[@]}" --change-dir "$CHANGE_DIR" \
      --mode "$mode" "${mode_args[@]}" "${limit_args[@]}" || status=1
  fi

  # Then the flows this change triggers, in one run.
  mission="$(flows_for "$mode")"
  if [[ -n "$mission" ]]; then
    ids="${mission// /,}"
    echo "--- :dart: ${mode} / mission: ${ids}"
    in_display "$mode" node relentless/run.ts "${agent_args[@]}" --flows "$ids" --mode "$mode" \
      "${mode_args[@]}" "${limit_args[@]}" || status=1
  fi

  for area in $AREAS; do
    if ! area_runs_in "$area" "$mode"; then
      echo "Skipping ${area}: it does not run in ${mode} mode"
      continue
    fi
    echo "--- :robot_face: ${mode} / ${area}"
    in_display "$mode" node relentless/run.ts "${agent_args[@]}" --area "$area" --mode "$mode" \
      "${mode_args[@]}" "${limit_args[@]}" || status=1
  done
done

# Each run's home holds its keychain, which contains the key; it must not
# become a build artifact.
find "$RUNS_DIR" -mindepth 2 -maxdepth 3 -name home -type d -exec rm -rf {} +
for secret in RDST_RELENTLESS_ANTHROPIC_API_KEY RDST_RELENTLESS_RESEND_API_KEY \
    RDST_RELENTLESS_ATTESTATION_SECRET RDST_RELENTLESS_CLIPROXY_API_KEY \
    CLAUDE_CODE_OAUTH_TOKEN; do
  [[ -n "${!secret:-}" ]] || continue
  if grep -rqF -- "${!secret}" "$RUNS_DIR"; then
    echo "ERROR: ${secret} leaked into run artifacts; removing them" >&2
    rm -rf "$RUNS_DIR"
    exit 1
  fi
done

# These runs become selector evidence; the updated index ships as an artifact
# for someone to commit.
node relentless/selector/ingest-evidence.ts "$RUNS_DIR" \
  && cp relentless/selector/evidence.json "${RUNS_DIR}/evidence.json" || true
node relentless/report.ts "$RUNS_DIR" | tee "${RUNS_DIR}/report.md" || true
if command -v buildkite-agent >/dev/null 2>&1 && [[ -z "${RELENTLESS_UI_PART:-}" ]]; then
  buildkite-agent annotate --context relentless-ui --style info \
    < "${RUNS_DIR}/report.md" || true
fi
# The job uploads its runs only once they are scrubbed and scanned, so a job
# stopped before then, by its timeout or a cancel, uploads none.
if command -v buildkite-agent >/dev/null 2>&1; then
  (cd "$REPO_ROOT" && buildkite-agent artifact upload "${RUNS_DIR#"${REPO_ROOT}/"}/**/*") \
    || status=1
fi
exit "$status"
