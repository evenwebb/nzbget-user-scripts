#!/usr/bin/env bash
#
# nzbget-scripts-updater.sh
# Updates NZBGet extension scripts from a GitHub ZIP while preserving local edits
# inside an optional editable block.
#
# Repo: https://github.com/evenwebb/nzbget-user-scripts
# License: GPL-3.0
#
set -u
set -o pipefail

###############################################################################
# EDIT FOR YOUR SETUP
###############################################################################

# GitHub ZIP URL (main branch by default). You can also point this at a tagged release ZIP.
ZIP_URL="https://github.com/evenwebb/nzbget-user-scripts/archive/refs/heads/main.zip"

# NZBGet ScriptDir (where extension folders live).
# Examples:
# - Docker: /config/scripts
# - Bare metal: /opt/nzbget/scripts
NZBGET_SCRIPTDIR="/path/to/your/nzbget/scriptdir"

# 1 = dry run (no writes), 0 = apply
DRY_RUN="1"

# 1 = fetch a fresh ZIP each run, 0 = reuse cached ZIP if present
FETCH_UPDATES="1"

# 1 = clear cached ZIP/extraction before running
CLEAR_CACHE="0"

# 1 = install scripts that do not already exist in ScriptDir
# 0 = update only scripts that already exist (recommended default)
INSTALL_MISSING="0"

# 1 = overwrite scripts as-is (no merge)
# 0 = preserve values inside editable block (if present)
RESET_CONFIG="0"

# Backups of replaced scripts
BACKUP_DIR="./backups"

# Working directory for downloads/extraction (must be writable)
WORK_DIR="/tmp/nzbget-scripts-updater"

# Download timeouts (seconds)
DOWNLOAD_CONNECT_TIMEOUT="15"
DOWNLOAD_MAX_TIME="300"

###############################################################################
# END EDIT FOR YOUR SETUP

timestamp() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(timestamp)] $*"; }
log_err() { echo "[$(timestamp)] ERROR: $*" >&2; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { log_err "Missing required command: $1"; exit 1; }
}

download_file() {
  local url="$1"
  local out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -R --connect-timeout "$DOWNLOAD_CONNECT_TIMEOUT" -m "$DOWNLOAD_MAX_TIME" "$url" -o "$out"
    return $?
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -qO "$out" "$url"
    return $?
  fi
  log_err "Need curl or wget to download: $url"
  return 1
}

download_file_if_modified() {
  # Returns: 0 downloaded, 2 not modified, 1 error
  local url="$1"
  local out="$2"

  if ! command -v curl >/dev/null 2>&1; then
    download_file "$url" "$out"
    return $?
  fi

  if [[ -f "$out" ]]; then
    local http
    http="$(curl -sS -L -R --connect-timeout "$DOWNLOAD_CONNECT_TIMEOUT" -m "$DOWNLOAD_MAX_TIME" -z "$out" -o "$out.tmp" -w '%{http_code}' "$url" 2>/dev/null || true)"
    if [[ "$http" == "304" ]]; then
      rm -f "$out.tmp" 2>/dev/null || true
      return 2
    fi
    if [[ "$http" == "200" ]]; then
      mv "$out.tmp" "$out"
      return 0
    fi
    rm -f "$out.tmp" 2>/dev/null || true
    log_err "Download failed (HTTP $http): $url"
    return 1
  fi

  download_file "$url" "$out"
  return $?
}

ensure_work_dir() {
  mkdir -p "$WORK_DIR" 2>/dev/null || true
  [[ -d "$WORK_DIR" ]] || { log_err "WORK_DIR not usable: $WORK_DIR"; return 1; }
}

clear_cache_if_requested() {
  [[ "$CLEAR_CACHE" != "1" ]] && return 0
  ensure_work_dir || return 1
  log "Clearing cache in WORK_DIR: $WORK_DIR"
  rm -rf "$WORK_DIR/extracted" 2>/dev/null || true
  rm -f "$WORK_DIR/nzbget-user-scripts.zip" 2>/dev/null || true
}

normalize_for_compare() {
  # Remove CR so comparisons don't churn on CRLF.
  tr -d '\r' < "$1"
}

files_equal() {
  local a="$1" b="$2"
  [[ -f "$a" && -f "$b" ]] || return 1
  cmp -s <(normalize_for_compare "$a") <(normalize_for_compare "$b")
}

get_edit_range() {
  # Deprecated: scripts no longer use EDIT FOR YOUR SETUP blocks.
  return 1
}

get_nzbget_config_range() {
  # Prints: "<start_line> <end_line>" for NZBGET_CONFIG triple-quote blocks.
  # Start: NZBGET_CONFIG = r"""
  # End:   """
  local file="$1"
  tr -d '\r' < "$file" | awk '
    BEGIN { start=0; end=0 }
    start==0 && $0 ~ /^NZBGET_CONFIG[[:space:]]*=[[:space:]]*r"""/ { start=NR+1; next }
    start>0 && end==0 && $0 ~ /^"""/ { end=NR-1; print start, end; exit }
    END { }
  '
}

replace_block_with_local() {
  # Usage: replace_block_with_local <dest_existing> <src_new> <out_path> <range_func_name>
  # Replaces the block in <src_new> with the corresponding block from <dest_existing>,
  # based on line ranges produced by <range_func_name>.
  local dest_existing="$1" src_new="$2" out="$3" range_func="$4"

  local s_src e_src s_dest e_dest
  read -r s_src e_src < <($range_func "$src_new" || true)
  read -r s_dest e_dest < <($range_func "$dest_existing" || true)

  if [[ -z "${s_src:-}" || -z "${e_src:-}" || -z "${s_dest:-}" || -z "${e_dest:-}" ]]; then
    cp "$src_new" "$out"
    return 0
  fi

  local tmp clean_src
  tmp="$(mktemp)"
  clean_src="$(mktemp)"
  tr -d '\r' < "$src_new" > "$clean_src"
  head -n $((s_src - 1)) "$clean_src" > "$tmp"
  tr -d '\r' < "$dest_existing" | sed -n "${s_dest},${e_dest}p" >> "$tmp"
  tail -n +"$((e_src + 2))" "$clean_src" >> "$tmp"
  rm -f "$clean_src"
  mv "$tmp" "$out"
}

merge_local_customizations() {
  # Usage: merge_local_customizations <dest_existing> <src_new> <out_path>
  local dest_existing="$1" src_new="$2" out="$3"

  if [[ ! -f "$dest_existing" || "$RESET_CONFIG" == "1" ]]; then
    cp "$src_new" "$out"
    return 0
  fi

  # Preserve NZBGET_CONFIG block (WebUI defaults) (if present)
  replace_block_with_local "$dest_existing" "$src_new" "$out" get_nzbget_config_range
}

backup_file() {
  local src="$1"
  local base stamp
  base="$(basename "$src")"
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP_DIR" 2>/dev/null || true
  cp "$src" "$BACKUP_DIR/$base.$stamp.bak"
}

sync_one_file() {
  # Usage: sync_one_file <src_file> <dest_file> <label>
  local src_file="$1"
  local dest_file="$2"
  local label="$3"

  if [[ ! -f "$src_file" ]]; then
    log_err "Missing source file: $src_file"
    return 1
  fi

  local merged
  merged="$(mktemp)"
  merge_local_customizations "$dest_file" "$src_file" "$merged"

  if [[ -f "$dest_file" ]] && files_equal "$merged" "$dest_file"; then
    rm -f "$merged"
    log "No changes: $label"
    return 2
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ -f "$dest_file" ]]; then
      log "[dry-run] Would update: $label"
    else
      log "[dry-run] Would install: $label"
    fi
    rm -f "$merged"
    return 0
  fi

  if [[ -f "$dest_file" ]]; then
    backup_file "$dest_file"
  else
    mkdir -p "$(dirname "$dest_file")" 2>/dev/null || true
  fi

  mv "$merged" "$dest_file"
  chmod +x "$dest_file" 2>/dev/null || true
  log "Updated: $label"
  return 0
}

prepare_source_repo() {
  ensure_work_dir || return 1
  clear_cache_if_requested || return 1

  local zip_path="$WORK_DIR/nzbget-user-scripts.zip"
  local extract_dir="$WORK_DIR/extracted"

  if [[ "$FETCH_UPDATES" == "1" || ! -f "$zip_path" ]]; then
    log "Downloading ZIP: $ZIP_URL"
    local dl_rc=0
    download_file_if_modified "$ZIP_URL" "$zip_path" || dl_rc=$?
    if [[ $dl_rc -eq 1 ]]; then
      return 1
    fi
    if [[ $dl_rc -eq 0 ]]; then
      log "ZIP updated."
      rm -rf "$extract_dir" 2>/dev/null || true
    else
      log "ZIP not modified (no download)."
    fi
  else
    log "Using cached ZIP: $zip_path"
  fi

  require_cmd unzip
  rm -rf "$extract_dir" 2>/dev/null || true
  mkdir -p "$extract_dir" 2>/dev/null || true
  unzip -q "$zip_path" -d "$extract_dir" || { log_err "Failed to unzip: $zip_path"; return 1; }

  local top_dir
  top_dir="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -n1)"
  [[ -n "$top_dir" ]] || { log_err "Unexpected ZIP layout (no top dir) in $extract_dir"; return 1; }
  echo "$top_dir"
}

main() {
  require_cmd awk
  require_cmd tr
  require_cmd sed
  require_cmd head
  require_cmd tail
  require_cmd mktemp
  require_cmd cp
  require_cmd mv
  require_cmd cmp
  require_cmd find

  if [[ -z "$NZBGET_SCRIPTDIR" || ! -d "$NZBGET_SCRIPTDIR" ]]; then
    log_err "NZBGET_SCRIPTDIR not found: $NZBGET_SCRIPTDIR"
    return 1
  fi

  local src_root
  src_root="$(prepare_source_repo | tail -n 1)" || return 1

  log "Syncing NZBGet scripts"
  log "Source: $src_root"
  log "Dest:   $NZBGET_SCRIPTDIR"
  log "DryRun: $DRY_RUN"

  local updated=0 skipped=0 failed=0 installed=0

  # Self-update (update the currently-running updater script in-place).
  # Moving the file does not affect the already-running process.
  local this_path src_updater
  this_path="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  src_updater="$src_root/$(basename "$0")"
  if [[ -f "$src_updater" ]]; then
    sync_one_file "$src_updater" "$this_path" "$(basename "$0") (self)"
  else
    log "No upstream updater found at: $src_updater"
  fi

  # Sync extension folders (each must have manifest.json + main.py).
  local src_dir folder_name dest_dir existed
  while IFS= read -r src_dir; do
    folder_name="$(basename "$src_dir")"
    dest_dir="$NZBGET_SCRIPTDIR/$folder_name"

    if [[ ! -d "$dest_dir" && "$INSTALL_MISSING" != "1" ]]; then
      log "Skipping not-installed: $folder_name/"
      skipped=$((skipped + 1))
      continue
    fi

    existed=0
    [[ -d "$dest_dir" ]] && existed=1
    if [[ "$DRY_RUN" == "0" ]]; then
      mkdir -p "$dest_dir" 2>/dev/null || true
    fi

    # Update manifest.json
    sync_one_file "$src_dir/manifest.json" "$dest_dir/manifest.json" "$folder_name/manifest.json"
    case $? in
      2) : ;;
      0) updated=$((updated + 1)) ;;
      *) failed=$((failed + 1)) ;;
    esac

    # Update main.py
    sync_one_file "$src_dir/main.py" "$dest_dir/main.py" "$folder_name/main.py"
    case $? in
      2) : ;;
      0) updated=$((updated + 1)) ;;
      *) failed=$((failed + 1)) ;;
    esac

    if [[ $existed -eq 0 && "$DRY_RUN" == "0" ]]; then
      installed=$((installed + 1))
    fi
  done < <(find "$src_root" -mindepth 1 -maxdepth 1 -type d -exec test -f "{}/manifest.json" -a -f "{}/main.py" \; -print 2>/dev/null | sort)

  log "Done. Updated: $updated, Installed: $installed, Skipped: $skipped, Failed: $failed"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi

