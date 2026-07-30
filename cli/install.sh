#!/bin/sh
set -eu

# RDST_INSTALLER_BASE_URL exists so CI can smoke-test an install against the
# archives a build just produced, before they are published anywhere.
ARCHIVE_BASE_URL="${RDST_INSTALLER_BASE_URL:-https://downloads.readyset.io/packages/rdst-cli}"
# Every Mach-O in a published macOS archive is signed by this team. Checking it
# is what a checksum cannot do: the checksum travels from the same host as the
# archive, so a compromised host can rewrite both, but it cannot produce
# loadable code carrying Readyset's signature.
APPLE_TEAM_ID="MK994N7JPH"

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
DATA_HOME=$(normalize_path "${XDG_DATA_HOME:-${HOME}/.local/share}")
CONFIG_DIR=$(normalize_path "${HOME}/.rdst")
DATA_DIR=$(normalize_path "${DATA_HOME}/rdst")
TOOL_DIR="${DATA_DIR}/tools"
ACTIVE_TOOL_LINK="${TOOL_DIR}/current"
GENERATION_DIR=""
CURRENT_LINK_TMP=""
ACTIVATION_STARTED=0
PLATFORM=""
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

Supported platforms: macOS on Apple Silicon, Linux on x86_64 and arm64.

Usage:
  install.sh [--version VERSION] [--no-modify-path] [--force]
  install.sh --uninstall

Options:
  --version VERSION   Install an exact RDST version instead of the one this
                      installer publishes.
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
validate_path "config directory" "$CONFIG_DIR"
case "$BIN_DIR" in
  *:*) fail "bin directory cannot contain a colon" ;;
esac
case "$DATA_DIR" in
  */rdst) ;;
  *) fail "data directory must end in /rdst" ;;
esac
case "$CONFIG_DIR/" in
  "$DATA_DIR/"*) fail "config directory cannot be inside the data directory" ;;
esac

case "$VERSION" in
  latest) ;;
  -*|*[!A-Za-z0-9._+!-]*|'') fail "invalid version: $VERSION" ;;
esac

# A published installer always downloads over https. The override exists so CI
# can smoke-test against a build's own artifacts before they are published, and
# only then may the transport be a loopback address or a local file.
ALLOW_INSECURE_TRANSPORT=0
case "$ARCHIVE_BASE_URL" in
  https://*) ;;
  http://127.0.0.1|http://127.0.0.1[:/]*|http://localhost|http://localhost[:/]*|file:///*)
    [ -n "${RDST_INSTALLER_BASE_URL:-}" ] \
      || fail "the RDST download location must use https"
    ALLOW_INSECURE_TRANSPORT=1
    warn "downloading RDST over an unverified transport: $ARCHIVE_BASE_URL"
    ;;
  *) fail "the RDST download location must use https" ;;
esac

# A build smoke-testing this script installs the archive it just produced, and
# only a release signs one with Readyset's certificate. Both conditions are
# required: the request opts out, and the archive came off the local filesystem
# or a loopback address. A published install reaches neither.
REQUIRE_READYSET_TEAM=1
if [ "$ALLOW_INSECURE_TRANSPORT" -eq 1 ] && [ "${RDST_ALLOW_ADHOC_SIGNATURE:-0}" = "1" ]; then
  REQUIRE_READYSET_TEAM=0
  warn "accepting an RDST archive that Readyset has not signed"
fi

INSTALL_METHOD="readyset-archive"

is_managed_install() {
  [ -f "$STATE_FILE" ] || return 1
  [ "$(grep -c "^method=${INSTALL_METHOD}\$" "$STATE_FILE" || true)" -eq 1 ]
}

validate_state() {
  for state_key in format method data_dir bin_dir platform; do
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
method=$INSTALL_METHOD
data_dir=$DATA_DIR
bin_dir=$BIN_DIR
platform=$PLATFORM
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

is_managed_link() {
  path="$1"
  [ -L "$path" ] || return 1
  target=$(readlink "$path" 2>/dev/null || true)
  executable_name=$(basename "$path")
  case "$target" in
    # The archive publishes both entrypoints side by side in the generation.
    "$ACTIVE_TOOL_LINK/rdst/$executable_name") return 0 ;;
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
  validate_path "saved data directory" "$saved_data_dir"
  validate_path "saved bin directory" "$saved_bin_dir"
  case "$saved_data_dir" in
    */rdst) ;;
    *) fail "saved data directory must end in /rdst" ;;
  esac
  case "$CONFIG_DIR/" in
    "$saved_data_dir/"*) fail "refusing to remove a data directory containing configuration" ;;
  esac
  DATA_DIR="$saved_data_dir"
  BIN_DIR="$saved_bin_dir"
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
    Darwin:arm64|Darwin:aarch64) PLATFORM="macos-arm64" ;;
    Darwin:x86_64|Darwin:amd64)
      fail "Intel Macs are not supported. RDST requires an Apple Silicon Mac."
      ;;
    Linux:arm64|Linux:aarch64) PLATFORM="linux-arm64" ;;
    Linux:x86_64|Linux:amd64) PLATFORM="linux-x86_64" ;;
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
  PATH_PROFILE=$(state_value path_profile || true)
  if [ -n "$PATH_PROFILE" ]; then
    validate_path "saved shell profile" "$PATH_PROFILE"
  fi
  if [ "$DATA_DIR" != "$saved_data_dir" ] || [ "$BIN_DIR" != "$saved_bin_dir" ]; then
    fail "existing RDST installation uses different directories; uninstall it before changing paths"
  fi
  saved_platform=$(state_value platform || true)
  if [ -n "$saved_platform" ] && [ "$saved_platform" != "$PLATFORM" ]; then
    fail "existing RDST installation is for $saved_platform, not $PLATFORM. Uninstall it first."
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
mkdir -p "$BIN_DIR" "$TOOL_DIR" "$CONFIG_DIR"
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
    if [ "$ALLOW_INSECURE_TRANSPORT" -eq 1 ]; then
      curl --fail --silent --show-error --location "$url" --output "$destination"
    else
      curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
        "$url" --output "$destination"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if [ "$ALLOW_INSECURE_TRANSPORT" -eq 1 ]; then
      wget --quiet --output-document="$destination" "$url"
    else
      wget --https-only --quiet --output-document="$destination" "$url"
    fi
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

resolve_existing_path() {
  path="$1"
  path_dir=${path%/*}
  path_name=${path##*/}
  resolved_dir=$(CDPATH='' cd -P "$path_dir" 2>/dev/null && pwd) || return 1
  printf '%s/%s' "$resolved_dir" "$path_name"
}

# A member name can carry the archive outside the directory it is unpacked
# into, and unpacking is what puts it there: the signature can only be checked
# against a tree that already exists on disk. Read the names first and refuse
# the archive whole, rather than auditing where its contents landed. Extractors
# differ in what they refuse on their own, so this does not rely on that.
verify_archive_members() {
  archive="$1"
  member_list="$tmp_dir/members"
  tar -tzf "$archive" > "$member_list" || fail "could not read the RDST archive"
  [ -s "$member_list" ] || fail "the RDST archive is empty"
  while IFS= read -r member; do
    [ -n "$member" ] || continue
    case "$member" in
      /*|..|../*|*/../*|*/..)
        fail "RDST archive contains a path that leaves the install: $member"
        ;;
    esac
  done < "$member_list"
}

# A tar archive can carry symlinks pointing anywhere. Refuse any that leave the
# unpacked tree, so activating an archive cannot publish a path elsewhere on
# the machine under an RDST name.
verify_extracted_links() {
  root="$1"
  resolved_root=$(resolve_existing_path "$root") \
    || fail "could not resolve the prepared RDST environment"
  link_report="$tmp_dir/unsafe-links"
  : > "$link_report"
  find "$root" -type l -print > "$tmp_dir/links"
  while IFS= read -r link; do
    [ -n "$link" ] || continue
    target=$(readlink "$link" 2>/dev/null || true)
    case "$target" in
      /*)
        printf '%s -> %s\n' "$link" "$target" >> "$link_report"
        continue
        ;;
    esac
    # A target whose parent does not exist cannot be shown to stay inside the
    # tree, so it is reported alongside the ones that provably escape.
    resolved=$(resolve_existing_path "${link%/*}/$target" 2>/dev/null || true)
    case "$resolved" in
      "$resolved_root"/*) ;;
      *) printf '%s -> %s\n' "$link" "$target" >> "$link_report" ;;
    esac
  done < "$tmp_dir/links"
  if [ -s "$link_report" ]; then
    while IFS= read -r entry; do
      warn "unresolvable or escaping symlink in the RDST archive: $entry"
    done < "$link_report"
    fail "RDST archive contains symlinks that do not stay inside the install"
  fi
}

# The macOS archive is a signed tree, and the signature is the one check the
# checksum cannot stand in for: both the archive and its checksum come from the
# same host, but only Readyset can produce this signature. Every tree has to
# verify against its own signature; whose signature it has to be is what
# REQUIRE_READYSET_TEAM decides.
#
# Every Mach-O in the tree is verified, not just the entrypoint. The frozen CLI
# loads its libraries from the tree at run time and is signed with library
# validation disabled, so the loader re-checks none of them; verifying the
# entrypoint alone would leave the rest resting on the checksum.
verify_signature() {
  root="$1"
  [ "$os" = "Darwin" ] || return 0
  for required_command in codesign file; do
    command -v "$required_command" >/dev/null 2>&1 \
      || fail "$required_command is required to verify RDST on macOS"
  done

  mach_o_list="$tmp_dir/mach-o"
  # file reports the description last, so the greedy prefix stops at the
  # separator it printed rather than at any colon inside a path.
  find "$root" -type f -exec file {} + \
    | sed -n 's/^\(.*\): [^:]*Mach-O.*$/\1/p' > "$mach_o_list"
  grep -qxF "$root/rdst" "$mach_o_list" \
    || fail "the RDST executable in the archive is not signed code"

  while IFS= read -r mach_o; do
    signature_info=$(codesign --verify --strict -dv "$mach_o" 2>&1) \
      || fail "RDST signature verification failed: ${mach_o#"$root"/}"
    [ "$REQUIRE_READYSET_TEAM" -eq 1 ] || continue
    signature_team=$(printf '%s\n' "$signature_info" \
      | sed -n 's/^TeamIdentifier=//p' | head -1)
    [ "$signature_team" = "$APPLE_TEAM_ID" ] \
      || fail "RDST is not signed by Readyset (team ${signature_team:-none}): ${mach_o#"$root"/}"
  done < "$mach_o_list"
}

# A published installer carries the version it was published with, so latest
# is only ever left over from an unpublished copy of this script.
if [ "$VERSION" = "latest" ]; then
  fail "this copy of the installer has no published version. Pass --version VERSION."
fi

archive_name="rdst-${VERSION}-${PLATFORM}.tar.gz"
archive_url="${ARCHIVE_BASE_URL%/}/versions/${VERSION}/${archive_name}"
archive_path="$tmp_dir/$archive_name"

info "Downloading RDST $VERSION for $PLATFORM..."
download "$archive_url" "$archive_path" \
  || fail "no RDST $VERSION build is available for $PLATFORM at $archive_url"
download "${archive_url}.sha256" "${archive_path}.sha256" \
  || fail "RDST $VERSION for $PLATFORM has no published checksum"
expected_sha=$(awk '{print $1; exit}' "${archive_path}.sha256")
case "$expected_sha" in
  *[!0-9a-f]*|'') fail "published checksum is malformed" ;;
esac
[ "${#expected_sha}" -eq 64 ] || fail "published checksum is malformed"
verify_sha256 "$archive_path" "$expected_sha"

info "Installing RDST $VERSION..."
if [ -L "$ACTIVE_TOOL_LINK" ]; then
  previous_generation=$(readlink "$ACTIVE_TOOL_LINK" 2>/dev/null || true)
  validate_generation_path "$previous_generation" \
    || fail "$ACTIVE_TOOL_LINK points outside the managed generation directory"
elif [ -e "$ACTIVE_TOOL_LINK" ]; then
  fail "$ACTIVE_TOOL_LINK is not an installer-managed link"
fi

verify_archive_members "$archive_path"
GENERATION_DIR=$(mktemp -d "$TOOL_DIR/.rdst-generation-XXXXXX")
tar -xzf "$archive_path" -C "$GENERATION_DIR"

# The archive holds exactly one top-level directory. Anything else did not come
# from the RDST build, so refuse it rather than reach into it.
generation_entries=$(ls -A "$GENERATION_DIR")
[ "$generation_entries" = "rdst" ] || fail "RDST archive has an unexpected layout"

verify_extracted_links "$GENERATION_DIR"
for entrypoint_name in rdst rdst-mcp; do
  [ -x "$GENERATION_DIR/rdst/$entrypoint_name" ] \
    || fail "RDST archive is missing the $entrypoint_name entrypoint"
done
verify_signature "$GENERATION_DIR/rdst"

# Redirect rather than pipe, so the exit status is the executable's own and a
# crash is reported as one instead of as a version mismatch.
version_output="$tmp_dir/staged-version"
"$GENERATION_DIR/rdst/rdst" --version > "$version_output" 2>&1 \
  || fail "the prepared RDST executable did not start"
# Match the version out of the output rather than reading the last line: the
# executable shares this stream with whatever its libraries print.
staged_version=$(sed -nE 's/.*[Vv]ersion ([0-9][^[:space:]]*).*/\1/p' "$version_output" | tail -1)
[ "$staged_version" = "$VERSION" ] \
  || fail "RDST archive reports ${staged_version:-no version}, expected $VERSION"

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
  entrypoint_target="$ACTIVE_TOOL_LINK/rdst/$entrypoint_name"
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

[ -x "$BIN_DIR/rdst" ] || fail "installation completed without creating $BIN_DIR/rdst"
[ -x "$BIN_DIR/rdst-mcp" ] || fail "installation completed without creating $BIN_DIR/rdst-mcp"

# Nothing references the previous generation once the new one is published, so
# retire it here. Removing it earlier would strand a running rdst, and leaving
# it grows the data directory by a full copy on every install.
if [ -n "${previous_generation:-}" ] \
  && validate_generation_path "$previous_generation"; then
  rm -rf "$previous_generation"
fi

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
