#!/bin/sh
set -eu

UV_VERSION="0.11.23"
PYTHON_VERSION="3.12"
DEFAULT_INDEX="https://pypi.org/simple"
UV_RELEASE_BASE_URL="https://releases.astral.sh/github/uv/releases/download/${UV_VERSION}"

normalize_path() {
  normalized="$1"
  while [ "$normalized" != "/" ] && [ "${normalized%/}" != "$normalized" ]; do
    normalized=${normalized%/}
  done
  printf '%s' "$normalized"
}

HOME=$(normalize_path "${HOME:-}")
export HOME
BIN_DIR=$(normalize_path "${XDG_BIN_HOME:-${HOME}/.local/bin}")
TOOL_BIN_DIR="$BIN_DIR"
DATA_HOME=$(normalize_path "${XDG_DATA_HOME:-${HOME}/.local/share}")
CACHE_HOME=$(normalize_path "${XDG_CACHE_HOME:-${HOME}/.cache}")
CONFIG_DIR=$(normalize_path "${HOME}/.rdst")
DATA_DIR=$(normalize_path "${DATA_HOME}/rdst")
CACHE_DIR=$(normalize_path "${CACHE_HOME}/rdst")
BOOTSTRAP_DIR="${DATA_DIR}/bootstrap/bin"
UV_BIN="${BOOTSTRAP_DIR}/uv"
TOOL_DIR="${DATA_DIR}/tools"
ACTIVE_TOOL_LINK="${TOOL_DIR}/current"
INSTALL_TOOL_DIR="$TOOL_DIR"
GENERATION_DIR=""
CURRENT_LINK_TMP=""
ACTIVATION_STARTED=0
PYTHON_DIR="${DATA_DIR}/python"
STATE_FILE="${CONFIG_DIR}/install-state"
LOCK_DIR="${CONFIG_DIR}/.rdst-operation-lock"
LOCK_TOKEN=""
LOCK_HELD=0
PATH_PROFILE=""

DEFAULT_RDST_VERSION="latest"
VERSION="$DEFAULT_RDST_VERSION"
MODIFY_PATH=1
FORCE=0
UNINSTALL=0

info() {
  printf '%s\n' "$*"
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Install RDST without sudo or a preinstalled Python.

Usage:
  install.sh [--version VERSION] [--no-modify-path] [--force]
  install.sh --uninstall

Options:
  --version VERSION   Install an exact RDST version instead of the latest release.
  --no-modify-path    Do not update the shell profile when the bin directory is absent from PATH.
  --force             Proceed when another rdst executable is elsewhere on PATH.
  --uninstall         Remove the installer-managed RDST runtime. User data in ~/.rdst is preserved.
  -h, --help          Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)
      [ "$#" -ge 2 ] || fail "--version requires a value"
      VERSION="$2"
      shift 2
      ;;
    --no-modify-path)
      MODIFY_PATH=0
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

validate_path() {
  label="$1"
  value="$2"
  [ -n "$value" ] || fail "$label cannot be empty"
  case "$value" in
    *'
'*) fail "$label cannot contain a newline" ;;
    /*) ;;
    *) fail "$label must be an absolute path" ;;
  esac
}

validate_path "HOME" "${HOME:-}"
validate_path "bin directory" "$BIN_DIR"
validate_path "data directory" "$DATA_DIR"
validate_path "cache directory" "$CACHE_DIR"
validate_path "config directory" "$CONFIG_DIR"
case "$BIN_DIR" in
  *:*) fail "bin directory cannot contain a colon" ;;
esac
case "$DATA_DIR:$CACHE_DIR" in
  */rdst:*/rdst) ;;
  *) fail "data and cache directories must end in /rdst" ;;
esac
case "$CONFIG_DIR/" in
  "$DATA_DIR/"*) fail "config directory cannot be inside the data directory" ;;
esac

case "$VERSION" in
  latest) ;;
  -*|*[!A-Za-z0-9._+!-]*|'') fail "invalid version: $VERSION" ;;
esac

is_managed_install() {
  [ -f "$STATE_FILE" ] || return 1
  [ "$(grep -c '^method=readyset-uv$' "$STATE_FILE" || true)" -eq 1 ]
}

validate_state() {
  for state_key in format method data_dir bin_dir cache_dir python uv_version; do
    state_count=$(grep -c "^${state_key}=" "$STATE_FILE" || true)
    [ "$state_count" -eq 1 ] || fail "installer state has an invalid $state_key entry"
  done
  path_profile_count=$(grep -c '^path_profile=' "$STATE_FILE" || true)
  [ "$path_profile_count" -le 1 ] || fail "installer state has an invalid path_profile entry"
}

state_value() {
  key="$1"
  [ -f "$STATE_FILE" ] || return 1
  awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "$STATE_FILE"
}

write_state() {
  state_tmp="$CONFIG_DIR/.install-state.$$"
  cat > "$state_tmp" <<EOF
format=1
method=readyset-uv
data_dir=$DATA_DIR
bin_dir=$BIN_DIR
cache_dir=$CACHE_DIR
python=$PYTHON_VERSION
uv_version=$UV_VERSION
path_profile=$PATH_PROFILE
EOF
  mv "$state_tmp" "$STATE_FILE"
  state_tmp=""
}

remove_path_block() {
  profile="$1"
  [ -f "$profile" ] || return 0
  marker_start="# >>> rdst >>>"
  marker_end="# <<< rdst <<<"
  grep -qF "$marker_start" "$profile" || return 0
  grep -qF "$marker_end" "$profile" || return 0
  profile_content="$profile.rdst-uninstall.$$"
  awk -v start="$marker_start" -v end="$marker_end" '
    $0 == start && !removing { removing = 1; buffered = $0 ORS; next }
    removing {
      buffered = buffered $0 ORS
      if ($0 == end) { removing = 0; buffered = "" }
      next
    }
    { print }
    END { if (removing) printf "%s", buffered }
  ' "$profile" > "$profile_content"
  cat "$profile_content" > "$profile"
  rm -f "$profile_content"
}

run_uv() {
  (
    for uv_name in $(env | sed -n 's/^\(UV_[A-Za-z0-9_]*\)=.*/\1/p'); do
      unset "$uv_name"
    done
    UV_TOOL_DIR="$INSTALL_TOOL_DIR" \
    UV_TOOL_BIN_DIR="$TOOL_BIN_DIR" \
    UV_PYTHON_INSTALL_DIR="$PYTHON_DIR" \
    UV_CACHE_DIR="${CACHE_DIR}/uv" \
      "$UV_BIN" "$@"
  )
}

is_managed_link() {
  path="$1"
  [ -L "$path" ] || return 1
  target=$(readlink "$path" 2>/dev/null || true)
  executable_name=$(basename "$path")
  case "$target" in
    "$ACTIVE_TOOL_LINK/rdst/bin/$executable_name"|"$TOOL_DIR/rdst/bin/$executable_name") return 0 ;;
    *) return 1 ;;
  esac
}

remove_managed_link() {
  path="$1"
  if is_managed_link "$path"; then
    rm -f "$path"
  fi
}

replace_link() {
  source_link="$1"
  destination_link="$2"
  if mv -fT "$source_link" "$destination_link" 2>/dev/null; then
    return
  fi
  if mv -fh "$source_link" "$destination_link" 2>/dev/null; then
    return
  fi
  fail "could not activate the prepared RDST environment"
}

validate_generation_path() {
  generation_path="$1"
  generation_name=${generation_path##*/}
  case "$generation_name" in
    .rdst-generation-*) ;;
    *) return 1 ;;
  esac
  [ "$generation_path" = "$TOOL_DIR/$generation_name" ] || return 1
  if [ -e "$generation_path" ] || [ -L "$generation_path" ]; then
    [ -d "$generation_path" ] && [ ! -L "$generation_path" ]
  fi
}

acquire_operation_lock() {
  mkdir -p "$CONFIG_DIR"
  LOCK_TOKEN="$$-$(date +%s)"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    lock_owner=$(cat "$LOCK_DIR/owner" 2>/dev/null || true)
    lock_pid=${lock_owner%%-*}
    [ -n "$lock_pid" ] || lock_pid=unknown
    fail "another RDST install, update, or uninstall operation holds the lock (PID $lock_pid). If no operation is running, remove $LOCK_DIR and retry"
  fi
  if ! chmod 700 "$LOCK_DIR" \
    || ! printf '%s\n' "$LOCK_TOKEN" > "$LOCK_DIR/owner"; then
    rm -rf "$LOCK_DIR"
    fail "could not initialize the RDST operation lock"
  fi
  LOCK_HELD=1
}

release_operation_lock() {
  if [ "$LOCK_HELD" -eq 1 ] \
    && [ "$(cat "$LOCK_DIR/owner" 2>/dev/null || true)" = "$LOCK_TOKEN" ]; then
    rm -rf "$LOCK_DIR"
  fi
  LOCK_HELD=0
}

uninstall() {
  if ! is_managed_install; then
    fail "no Readyset installer-managed RDST installation was found"
  fi
  validate_state

  saved_data_dir=$(normalize_path "$(state_value data_dir)")
  saved_bin_dir=$(normalize_path "$(state_value bin_dir)")
  saved_cache_dir=$(normalize_path "$(state_value cache_dir)")
  validate_path "saved data directory" "$saved_data_dir"
  validate_path "saved bin directory" "$saved_bin_dir"
  validate_path "saved cache directory" "$saved_cache_dir"
  case "$saved_data_dir:$saved_cache_dir" in
    */rdst:*/rdst) ;;
    *) fail "saved data and cache directories must end in /rdst" ;;
  esac
  case "$CONFIG_DIR/" in
    "$saved_data_dir/"*) fail "refusing to remove a data directory containing configuration" ;;
  esac
  DATA_DIR="$saved_data_dir"
  BIN_DIR="$saved_bin_dir"
  CACHE_DIR="$saved_cache_dir"
  TOOL_DIR="${DATA_DIR}/tools"
  ACTIVE_TOOL_LINK="${TOOL_DIR}/current"

  [ ! -L "$DATA_DIR" ] || fail "refusing to use a symbolic-link data directory: $DATA_DIR"
  [ -f "$DATA_DIR/.rdst-managed" ] || fail "refusing to use an unmarked data directory: $DATA_DIR"

  trap release_operation_lock 0
  trap 'exit 1' HUP INT TERM
  acquire_operation_lock

  saved_path_profile_count=$(grep -c '^path_profile=' "$STATE_FILE" || true)
  saved_path_profile=$(state_value path_profile || true)
  if [ "$saved_path_profile_count" -eq 1 ]; then
    if [ -n "$saved_path_profile" ]; then
      validate_path "saved shell profile" "$saved_path_profile"
      remove_path_block "$saved_path_profile"
    fi
  else
    for legacy_profile in \
      "${ZDOTDIR:-$HOME}/.zshrc" \
      "$HOME/.bash_profile" \
      "$HOME/.bash_login" \
      "$HOME/.profile" \
      "$HOME/.bashrc" \
      "${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"; do
      remove_path_block "$legacy_profile"
    done
  fi

  remove_managed_link "$BIN_DIR/rdst"
  remove_managed_link "$BIN_DIR/rdst-mcp"

  case "$DATA_DIR" in
    ''|'/'|"$HOME") fail "refusing to remove unsafe data directory: $DATA_DIR" ;;
    *) rm -rf "$DATA_DIR" ;;
  esac
  rm -rf "${CACHE_DIR}/uv"
  rm -f "$STATE_FILE"
  release_operation_lock
  trap - 0 HUP INT TERM

  info "RDST was uninstalled."
  info "User configuration and saved data remain in $CONFIG_DIR."
}

if [ "$UNINSTALL" -eq 1 ]; then
  uninstall
  exit 0
fi

check_platform() {
  os=$(uname -s 2>/dev/null || true)
  arch=$(uname -m 2>/dev/null || true)

  case "$os:$arch" in
    Darwin:arm64|Darwin:aarch64)
      UV_TARGET="aarch64-apple-darwin"
      UV_SHA256="71ef9de85db820749b3b12b7585624ee279e9c5afcbc6f8236bc3d628c4305b0"
      ;;
    Darwin:x86_64|Darwin:amd64)
      UV_TARGET="x86_64-apple-darwin"
      UV_SHA256="7a88155033cc469bba5bd5a24212e355eb92e3e2a276320b669ec576296c1e25"
      ;;
    Linux:arm64|Linux:aarch64)
      UV_TARGET="aarch64-unknown-linux-gnu"
      UV_SHA256="1873a77350f6621279ae1a0d2227f2bd8b67131598f14a7eb0ba2215d3da2c98"
      ;;
    Linux:x86_64|Linux:amd64)
      UV_TARGET="x86_64-unknown-linux-gnu"
      UV_SHA256="e12c4cda2fe8c305510a78380a88f2c32a27e90cdcd123cefd2873388f0ebb5f"
      ;;
    Darwin:*|Linux:*)
      fail "unsupported architecture: ${arch:-unknown}. RDST supports x86_64 and arm64."
      ;;
    *)
      fail "unsupported operating system: ${os:-unknown}. RDST supports macOS and Linux."
      ;;
  esac

  if [ "$os" = "Linux" ]; then
    if (ldd --version 2>&1 || true) | grep -qi musl || ls /lib/ld-musl-*.so.1 >/dev/null 2>&1; then
      fail "musl-based Linux distributions are not supported yet. Use a glibc-based distribution."
    fi
  fi
}

check_platform

if [ -f "$STATE_FILE" ]; then
  is_managed_install || fail "existing installer state is not recognized"
  validate_state
  saved_data_dir=$(normalize_path "$(state_value data_dir)")
  saved_bin_dir=$(normalize_path "$(state_value bin_dir)")
  saved_cache_dir=$(normalize_path "$(state_value cache_dir)")
  PATH_PROFILE=$(state_value path_profile || true)
  if [ -n "$PATH_PROFILE" ]; then
    validate_path "saved shell profile" "$PATH_PROFILE"
  fi
  if [ "$DATA_DIR" != "$saved_data_dir" ] || [ "$BIN_DIR" != "$saved_bin_dir" ] || [ "$CACHE_DIR" != "$saved_cache_dir" ]; then
    fail "existing RDST installation uses different directories; uninstall it before changing paths"
  fi
  for executable_name in rdst rdst-mcp; do
    executable_path="$BIN_DIR/$executable_name"
    if { [ -e "$executable_path" ] || [ -L "$executable_path" ]; } \
      && ! is_managed_link "$executable_path"; then
      fail "$executable_path is no longer owned by the RDST installer"
    fi
  done
fi

existing_rdst=$(command -v rdst 2>/dev/null || true)
if [ -n "$existing_rdst" ] && [ "$existing_rdst" != "$BIN_DIR/rdst" ]; then
  if [ "$FORCE" -ne 1 ]; then
    fail "rdst is already available at $existing_rdst. Remove it first or rerun with --force."
  fi
  warn "the existing rdst at $existing_rdst may take precedence in PATH"
fi

if ! is_managed_install; then
  for executable_name in rdst rdst-mcp; do
    executable_path="$BIN_DIR/$executable_name"
    if [ -e "$executable_path" ] || [ -L "$executable_path" ]; then
      fail "$executable_path already exists and is not managed by this installer. Remove it with its package manager first."
    fi
  done
fi

[ ! -L "$DATA_DIR" ] || fail "$DATA_DIR must not be a symbolic link"
if [ -d "$DATA_DIR" ] && [ ! -f "$DATA_DIR/.rdst-managed" ]; then
  if [ -n "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
    fail "$DATA_DIR already contains data and is not managed by the RDST installer"
  fi
fi
mkdir -p "$BIN_DIR" "$BOOTSTRAP_DIR" "$TOOL_DIR" "$PYTHON_DIR" "$CACHE_DIR" "$CONFIG_DIR"
[ ! -L "$TOOL_DIR" ] || fail "$TOOL_DIR must not be a symbolic link"
tmp_dir=""
state_tmp=""
cleanup() {
  release_operation_lock
  if [ -n "$tmp_dir" ]; then
    rm -rf "$tmp_dir"
  fi
  if [ -n "$state_tmp" ]; then
    rm -f "$state_tmp"
  fi
  if [ -n "$CURRENT_LINK_TMP" ]; then
    rm -f "$CURRENT_LINK_TMP"
  fi
  if [ -n "$GENERATION_DIR" ] && [ "$ACTIVATION_STARTED" -eq 0 ]; then
    rm -rf "$GENERATION_DIR"
  fi
}
trap cleanup 0
trap 'exit 1' HUP INT TERM
acquire_operation_lock
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/rdst-install.XXXXXX")

printf 'format=1\n' > "$DATA_DIR/.rdst-managed"
write_state

download() {
  url="$1"
  destination="$2"
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
      "$url" --output "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --quiet --output-document="$destination" "$url"
  elif command -v busybox >/dev/null 2>&1 && busybox wget --help >/dev/null 2>&1; then
    busybox wget -q -O "$destination" "$url"
  else
    fail "curl or wget is required to download RDST"
  fi
}

verify_sha256() {
  artifact="$1"
  expected="$2"
  if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$artifact" | awk '{print $1}')
  elif command -v shasum >/dev/null 2>&1; then
    actual=$(shasum -a 256 "$artifact" | awk '{print $1}')
  else
    fail "sha256sum or shasum is required to verify the RDST runtime"
  fi
  [ "$actual" = "$expected" ] || fail "RDST runtime checksum verification failed"
}

installed_uv_version=""
if [ -x "$UV_BIN" ]; then
  installed_uv_version=$(run_uv --version 2>/dev/null | awk '{print $2}' || true)
fi
if [ "$installed_uv_version" != "$UV_VERSION" ]; then
  info "Installing the RDST runtime manager..."
  uv_archive="$tmp_dir/uv-${UV_TARGET}.tar.gz"
  download "$UV_RELEASE_BASE_URL/uv-${UV_TARGET}.tar.gz" "$uv_archive"
  verify_sha256 "$uv_archive" "$UV_SHA256"
  tar -xzf "$uv_archive" -C "$tmp_dir"
  extracted_uv="$tmp_dir/uv-${UV_TARGET}/uv"
  [ -x "$extracted_uv" ] || fail "RDST runtime archive did not contain uv"
  mv "$extracted_uv" "$UV_BIN"
  chmod 755 "$UV_BIN"
fi

if [ "$VERSION" = "latest" ]; then
  package="rdst@latest"
else
  package="rdst==$VERSION"
fi

info "Installing $package with a managed Python $PYTHON_VERSION runtime..."
if [ -L "$ACTIVE_TOOL_LINK" ]; then
  previous_generation=$(readlink "$ACTIVE_TOOL_LINK" 2>/dev/null || true)
  validate_generation_path "$previous_generation" \
    || fail "$ACTIVE_TOOL_LINK points outside the managed generation directory"
elif [ -e "$ACTIVE_TOOL_LINK" ]; then
  fail "$ACTIVE_TOOL_LINK is not an installer-managed link"
elif [ -d "$TOOL_DIR/rdst" ]; then
  previous_generation="$TOOL_DIR/rdst"
fi

GENERATION_DIR=$(mktemp -d "$TOOL_DIR/.rdst-generation-XXXXXX")
INSTALL_TOOL_DIR="$GENERATION_DIR"
staged_bin="$tmp_dir/tool-bin"
mkdir -p "$staged_bin"
TOOL_BIN_DIR="$staged_bin"
set -- tool install \
  --python "$PYTHON_VERSION" \
  --managed-python \
  --no-build \
  --no-config \
  --default-index "$DEFAULT_INDEX" \
  --force \
  "$package"
run_uv "$@"

resolve_existing_path() {
  path="$1"
  path_dir=${path%/*}
  path_name=${path##*/}
  resolved_dir=$(CDPATH='' cd -P "$path_dir" 2>/dev/null && pwd) || return 1
  printf '%s/%s' "$resolved_dir" "$path_name"
}

entrypoint_count=0
for staged_entrypoint in "$staged_bin"/* "$staged_bin"/.[!.]* "$staged_bin"/..?*; do
  if [ ! -e "$staged_entrypoint" ] && [ ! -L "$staged_entrypoint" ]; then
    continue
  fi
  entrypoint_name=$(basename "$staged_entrypoint")
  case "$entrypoint_name" in
    rdst|rdst-mcp) ;;
    *) fail "RDST package exposed an unexpected executable: $entrypoint_name" ;;
  esac
  [ -L "$staged_entrypoint" ] || fail "RDST executable is not a managed link: $entrypoint_name"
  entrypoint_target=$(readlink "$staged_entrypoint" 2>/dev/null || true)
  case "$entrypoint_target" in
    /*) ;;
    *) entrypoint_target="$staged_bin/$entrypoint_target" ;;
  esac
  resolved_entrypoint_target=$(resolve_existing_path "$entrypoint_target" || true)
  expected_entrypoint_target=$(resolve_existing_path "$GENERATION_DIR/rdst/bin/$entrypoint_name" || true)
  if [ -z "$resolved_entrypoint_target" ] \
    || [ "$resolved_entrypoint_target" != "$expected_entrypoint_target" ]; then
    fail "RDST executable points outside the managed generation: $entrypoint_name"
  fi
  entrypoint_count=$((entrypoint_count + 1))
done
[ "$entrypoint_count" -eq 2 ] || fail "RDST package did not expose the expected executables"
"$staged_bin/rdst" --version >/dev/null 2>&1 \
  || fail "the prepared RDST executable did not start"

for entrypoint_name in rdst rdst-mcp; do
  [ -x "$GENERATION_DIR/rdst/bin/$entrypoint_name" ] \
    || fail "the prepared RDST environment is missing $entrypoint_name"
done

CURRENT_LINK_TMP="$TOOL_DIR/.rdst-current.$$"
rm -f "$CURRENT_LINK_TMP"
ln -s "$GENERATION_DIR" "$CURRENT_LINK_TMP"
ACTIVATION_STARTED=1
replace_link "$CURRENT_LINK_TMP" "$ACTIVE_TOOL_LINK"
[ "$(readlink "$ACTIVE_TOOL_LINK" 2>/dev/null || true)" = "$GENERATION_DIR" ] \
  || fail "could not activate the prepared RDST environment"
CURRENT_LINK_TMP=""
GENERATION_DIR=""

for entrypoint_name in rdst rdst-mcp; do
  entrypoint_target="$ACTIVE_TOOL_LINK/rdst/bin/$entrypoint_name"
  executable_path="$BIN_DIR/$entrypoint_name"
  if { [ -e "$executable_path" ] || [ -L "$executable_path" ]; } \
    && ! is_managed_link "$executable_path"; then
    fail "$executable_path is no longer owned by the RDST installer"
  fi
  temporary_link="$BIN_DIR/.${entrypoint_name}.rdst-install.$$"
  rm -f "$temporary_link"
  ln -s "$entrypoint_target" "$temporary_link"
  replace_link "$temporary_link" "$executable_path"
  [ "$(readlink "$executable_path" 2>/dev/null || true)" = "$entrypoint_target" ] \
    || fail "could not publish the $entrypoint_name executable"
done
TOOL_BIN_DIR="$BIN_DIR"
INSTALL_TOOL_DIR="$TOOL_DIR"

[ -x "$BIN_DIR/rdst" ] || fail "installation completed without creating $BIN_DIR/rdst"
[ -x "$BIN_DIR/rdst-mcp" ] || fail "installation completed without creating $BIN_DIR/rdst-mcp"

path_is_configured=0
case ":${PATH:-}:" in
  *":$BIN_DIR:"*) path_is_configured=1 ;;
esac

shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

configure_path() {
  shell_name=$(basename "${SHELL:-sh}")
  marker="# >>> rdst >>>"
  profile_format="sh"
  if [ -n "$PATH_PROFILE" ]; then
    profile="$PATH_PROFILE"
    case "$profile" in
      */fish/config.fish) profile_format="fish" ;;
    esac
  else
    case "$shell_name" in
      zsh) profile="${ZDOTDIR:-$HOME}/.zshrc" ;;
      bash)
        if [ "$os" = "Darwin" ]; then
          if [ -e "$HOME/.bash_profile" ]; then
            profile="$HOME/.bash_profile"
          elif [ -e "$HOME/.bash_login" ]; then
            profile="$HOME/.bash_login"
          elif [ -e "$HOME/.profile" ]; then
            profile="$HOME/.profile"
          else
            profile="$HOME/.bash_profile"
          fi
        else
          profile="$HOME/.bashrc"
        fi
        ;;
      fish)
        profile="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
        profile_format="fish"
        ;;
      *)
        warn "could not determine a supported shell profile for $shell_name"
        return
        ;;
    esac
  fi

  validate_path "shell profile" "$profile"
  mkdir -p "$(dirname "$profile")"
  touch "$profile"
  quoted_bin=$(shell_quote "$BIN_DIR")
  if grep -qF "$marker" "$profile"; then
    if grep -qF "$quoted_bin" "$profile"; then
      PATH_PROFILE="$profile"
      return
    fi
    warn "the existing RDST PATH block in $profile uses a different bin directory"
    return
  fi

  if [ "$profile_format" = "fish" ]; then
    printf '\n%s\nfish_add_path --path %s\n# <<< rdst <<<\n' "$marker" "$quoted_bin" >> "$profile"
  else
    # shellcheck disable=SC2016
    printf '\n%s\nexport PATH=%s:"$PATH"\n# <<< rdst <<<\n' "$marker" "$quoted_bin" >> "$profile"
  fi
  PATH_PROFILE="$profile"
  info "Updated PATH in $profile."
}

if [ "$path_is_configured" -eq 0 ]; then
  if [ "$MODIFY_PATH" -eq 1 ]; then
    configure_path
  fi
  info "Open a new shell or run:"
  info "  export PATH=$(shell_quote "$BIN_DIR"):\"\$PATH\""
fi
write_state

installed_version=$("$BIN_DIR/rdst" --version 2>/dev/null || "$BIN_DIR/rdst" version 2>/dev/null || true)
info "RDST installed successfully${installed_version:+: $installed_version}"
info "Next step: rdst init"
info "Update later with: rdst update"
info "Uninstall with: curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | sh -s -- --uninstall"
