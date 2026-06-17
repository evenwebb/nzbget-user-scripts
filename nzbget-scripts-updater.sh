#!/usr/bin/env bash
#
# nzbget-scripts-updater.sh
# Update NZBGet extension scripts from GitHub while preserving local config edits.
#
# Description:
#   Downloads this repo (or uses a local copy), updates installed extension folders
#   (manifest.json + main.py), and merges NZBGET_CONFIG blocks in Python scripts.
#   The updater script itself preserves your EDIT FOR YOUR SETUP bash settings.
#
# Usage:
#   Edit variables in EDIT FOR YOUR SETUP below, then run manually or on a schedule.
#   DRY_RUN: 1 = preview only (default), 0 = apply updates
#
# Configuration (edit script variables below):
#   - SOURCE_MODE: zip or local
#   - ZIP_URL / REPO_DIR / NZBGET_SCRIPTDIR
#   - FETCH_UPDATES / CLEAR_CACHE / INSTALL_MISSING
#   - DRY_RUN / BACKUP_DIR / WORK_DIR / RESET_CONFIG
#   - INCLUDE_FOLDERS / EXCLUDE_FOLDERS
#   - DOWNLOAD_CONNECT_TIMEOUT / DOWNLOAD_MAX_TIME
#
# Note: Progress and errors print to stdout; NZBGet and cron logs show that output.
#
# Author: https://github.com/evenwebb
# Project: https://github.com/evenwebb/nzbget-user-scripts
# License: GPL-3.0

set -u
set -o pipefail

###############################################################################
# EDIT FOR YOUR SETUP
###############################################################################

# Where to read scripts from:
# - zip: download ZIP_URL (no git required)
# - local: use REPO_DIR
SOURCE_MODE="zip"

# GitHub ZIP URL (main branch by default). You can also point this at a tagged release ZIP.
ZIP_URL="https://github.com/evenwebb/nzbget-user-scripts/archive/refs/heads/main.zip"

# Local checkout of this repo (only used when SOURCE_MODE="local")
REPO_DIR="/path/to/nzbget-user-scripts"

# NZBGet ScriptDir (where extension folders live).
# Examples:
# - Docker: /config/scripts
# - Bare metal: /opt/nzbget/scripts
NZBGET_SCRIPTDIR="/path/to/your/nzbget/scriptdir"

# 1 = fetch a fresh ZIP each run (zip mode), 0 = reuse cached ZIP (if present)
FETCH_UPDATES="1"

# 1 = dry run (no writes), 0 = apply
DRY_RUN="1"

# Backups of replaced scripts
BACKUP_DIR="./backups"

# Working directory for downloads/extraction (must be writable)
WORK_DIR="/tmp/nzbget-scripts-updater"

# Download timeouts (seconds)
DOWNLOAD_CONNECT_TIMEOUT="15"
DOWNLOAD_MAX_TIME="300"

# 1 = clear cached ZIP/extraction before running
CLEAR_CACHE="0"

# 1 = install extensions that do not already exist in ScriptDir
# 0 = update only extensions that already exist (recommended default)
INSTALL_MISSING="0"

# 1 = overwrite scripts as-is (no merge of NZBGET_CONFIG / EDIT blocks)
# 0 = preserve local config values by merging (recommended default)
RESET_CONFIG="0"

# Selective update: only update folders matching these names (empty = all)
INCLUDE_FOLDERS=()

# Selective update: skip these folder names (empty = none excluded)
EXCLUDE_FOLDERS=()

###############################################################################
# END EDIT FOR YOUR SETUP

timestamp() { date +"%Y-%m-%d %H:%M:%S"; }

log() { echo "[$(timestamp)] $*"; }

log_err() {
  local msg="[$(timestamp)] ERROR: $*"
  echo "$msg"
  echo "$msg" >&2
}

log_stderr() {
  # Use this for messages inside functions that return data via stdout.
  echo "[$(timestamp)] $*" >&2
}

_friendly_curl_err() {
  local msg="$1"
  msg="${msg#curl: }"
  if [[ "$msg" == *"Could not resolve host"* ]]; then
    echo "The server name could not be found — check ZIP_URL in this script."
  elif [[ "$msg" == *"Connection refused"* ]] || [[ "$msg" == *"Failed to connect"* ]]; then
    echo "Could not connect — check ZIP_URL and your network."
  elif [[ "$msg" == *"timed out"* ]] || [[ "$msg" == *"Timeout"* ]]; then
    echo "The download timed out — increase DOWNLOAD_MAX_TIME in this script or check your network."
  elif [[ "$msg" == *"404"* ]]; then
    echo "The file was not found at that URL — check ZIP_URL in this script."
  else
    echo "$msg"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { log_err "Missing required command: $1"; exit 1; }
}

_normalize_for_compare() {
  tr -d '\r' < "$1" | awk '{ print $0 }'
}

files_equal() {
  local a="$1" b="$2"
  [[ -f "$a" && -f "$b" ]] || return 1
  cmp -s <(_normalize_for_compare "$a") <(_normalize_for_compare "$b")
}

get_edit_setup_range() {
  # Prints: "<start_line> <end_line>" for bash EDIT FOR YOUR SETUP blocks.
  local file="$1"
  local line lineno=0 start=0 end=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    lineno=$((lineno + 1))
    if [[ $start -eq 0 ]]; then
      if [[ "$line" =~ ^#[[:space:]]*EDIT[[:space:]]+FOR[[:space:]]+YOUR[[:space:]]+SETUP ]]; then
        start=$((lineno + 1))
      fi
      continue
    fi
    if [[ "$line" =~ ^#[[:space:]]*END[[:space:]]+EDIT[[:space:]]+FOR[[:space:]]+YOUR[[:space:]]+SETUP ]]; then
      end=$((lineno - 1))
      printf '%s %s\n' "$start" "$end"
      return 0
    fi
  done < "$file"
  if [[ $start -gt 0 ]]; then
    printf '%s %s\n' "$start" "$lineno"
  fi
}

upstream_heads_and_tails_match() {
  # True when everything outside the EDIT block matches between dest and upstream.
  local dest_file="$1"
  local src_file="$2"
  local range_fn="$3"
  local s_src e_src s_dest e_dest
  local hf1 hf2 tf1 tf2 rc=0
  read -r s_src e_src < <($range_fn "$src_file") || return 1
  read -r s_dest e_dest < <($range_fn "$dest_file") || return 1
  [[ "$s_src" == "$s_dest" && "$e_src" == "$e_dest" ]] || return 1

  hf1="$(mktemp)"
  hf2="$(mktemp)"
  tf1="$(mktemp)"
  tf2="$(mktemp)"
  tr -d '\r' < "$dest_file" | head -n $((s_src - 1)) > "$hf1"
  tr -d '\r' < "$src_file" | head -n $((s_src - 1)) > "$hf2"
  tr -d '\r' < "$dest_file" | tail -n +$((e_src + 1)) > "$tf1"
  tr -d '\r' < "$src_file" | tail -n +$((e_src + 1)) > "$tf2"

  files_equal "$hf1" "$hf2" && files_equal "$tf1" "$tf2" || rc=1
  rm -f "$hf1" "$hf2" "$tf1" "$tf2"
  return "$rc"
}

get_nzbget_config_range() {
  # Prints: "<start_line> <end_line>" for NZBGET_CONFIG triple-quote blocks.
  local file="$1"
  tr -d '\r' < "$file" | awk '
    BEGIN { start=0; end=0 }
    start==0 && $0 ~ /^NZBGET_CONFIG[[:space:]]*=[[:space:]]*r"""/ { start=NR+1; next }
    start>0 && end==0 && $0 ~ /^"""/ { end=NR-1; print start, end; exit }
    END { }
  '
}

replace_block_with_local() {
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
  local dest_existing="$1" src_new="$2" out="$3"

  if [[ ! -f "$dest_existing" || "$RESET_CONFIG" == "1" ]]; then
    cp "$src_new" "$out"
    return 0
  fi

  if [[ "$src_new" == *.sh ]]; then
    replace_block_with_local "$dest_existing" "$src_new" "$out" get_edit_setup_range
    return 0
  fi

  replace_block_with_local "$dest_existing" "$src_new" "$out" get_nzbget_config_range
}

download_file() {
  local url="$1"
  local out="$2"
  local curl_err

  if command -v curl >/dev/null 2>&1; then
    curl_err=$(mktemp) || { log_err "Could not create temp file for download."; return 1; }
    if curl -fsSL -R --connect-timeout "$DOWNLOAD_CONNECT_TIMEOUT" -m "$DOWNLOAD_MAX_TIME" "$url" -o "$out" 2>"$curl_err"; then
      rm -f "$curl_err"
      return 0
    fi
    log_err "Could not download from ${url}. $(_friendly_curl_err "$(tr '\n' ' ' <"$curl_err")") Check ZIP_URL in this script."
    rm -f "$curl_err"
    return 1
  fi
  if command -v wget >/dev/null 2>&1; then
    if wget -qO "$out" "$url"; then
      return 0
    fi
    log_err "Could not download from ${url} using wget. Check ZIP_URL and network."
    return 1
  fi

  log_err "Need curl or wget to download updates. Check ZIP_URL in this script: $url"
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
    if [[ "$http" == "401" || "$http" == "403" ]]; then
      log_err "Download was rejected (HTTP $http). Check ZIP_URL in this script."
    elif [[ "$http" == "404" ]]; then
      log_err "Download URL not found (HTTP 404). Check ZIP_URL in this script: $url"
    else
      log_err "Download failed (HTTP ${http:-unknown}). Check ZIP_URL and network: $url"
    fi
    return 1
  fi

  download_file "$url" "$out"
  return $?
}

ensure_work_dir() {
  if [[ -z "$WORK_DIR" ]]; then
    log_err "WORK_DIR is empty"
    return 1
  fi
  mkdir -p "$WORK_DIR" 2>/dev/null || true
  [[ -d "$WORK_DIR" ]] || { log_err "WORK_DIR not usable: $WORK_DIR"; return 1; }
}

clear_cache_if_requested() {
  [[ "$CLEAR_CACHE" != "1" ]] && return 0
  ensure_work_dir || return 1
  log_stderr "Clearing cache in WORK_DIR: $WORK_DIR"
  rm -rf "$WORK_DIR/extracted" 2>/dev/null || true
  rm -f "$WORK_DIR/nzbget-user-scripts.zip" 2>/dev/null || true
}

prepare_source_repo() {
  if [[ "$SOURCE_MODE" != "zip" && "$SOURCE_MODE" != "local" ]]; then
    log_err "SOURCE_MODE must be 'zip' or 'local' (you entered: ${SOURCE_MODE}). Edit the settings at the top of this script."
    return 1
  fi

  if [[ "$SOURCE_MODE" == "local" ]]; then
    if [[ -z "$REPO_DIR" || ! -d "$REPO_DIR" ]]; then
      log_err "REPO_DIR not found: $REPO_DIR. Check REPO_DIR in this script."
      return 1
    fi
    echo "$REPO_DIR"
    return 0
  fi

  if [[ -z "$ZIP_URL" ]]; then
    log_err "ZIP_URL is empty. Set ZIP_URL in this script."
    return 1
  fi

  ensure_work_dir || return 1
  clear_cache_if_requested || return 1

  local zip_path="$WORK_DIR/nzbget-user-scripts.zip"
  local extract_dir="$WORK_DIR/extracted"

  if [[ "$FETCH_UPDATES" == "1" || ! -f "$zip_path" ]]; then
    log_stderr "Downloading ZIP: $ZIP_URL"
    if [[ "$DRY_RUN" == "1" ]]; then
      log_stderr "DRY_RUN: downloading/extracting is allowed (no destination writes)."
    fi
    local dl_rc=0
    download_file_if_modified "$ZIP_URL" "$zip_path" || dl_rc=$?
    if [[ $dl_rc -eq 1 ]]; then
      return 1
    fi
    if [[ $dl_rc -eq 0 ]]; then
      log_stderr "ZIP updated."
      rm -rf "$extract_dir" 2>/dev/null || true
    else
      log_stderr "ZIP not modified (no download)."
    fi
  else
    log_stderr "Using cached ZIP: $zip_path"
  fi

  require_cmd unzip

  local zip_fingerprint
  zip_fingerprint=$(md5sum "$zip_path" 2>/dev/null | awk '{print $1}' || true)
  local fingerprint_file="$extract_dir/.zip-fingerprint"

  if [[ -d "$extract_dir" && -f "$fingerprint_file" && -n "$zip_fingerprint" ]]; then
    local stored_fp
    stored_fp=$(tr -d '\r\n' < "$fingerprint_file" 2>/dev/null || true)
    if [[ "$zip_fingerprint" == "$stored_fp" ]]; then
      log_stderr "ZIP fingerprint unchanged; reusing cached extraction."
    else
      log_stderr "ZIP fingerprint changed; re-extracting."
      rm -rf "$extract_dir" 2>/dev/null || true
      mkdir -p "$extract_dir" 2>/dev/null || true
      unzip -q "$zip_path" -d "$extract_dir" || {
        log_err "Failed to unzip: $zip_path"
        return 1
      }
      printf '%s\n' "$zip_fingerprint" > "$fingerprint_file"
    fi
  else
    rm -rf "$extract_dir" 2>/dev/null || true
    mkdir -p "$extract_dir" 2>/dev/null || true
    unzip -q "$zip_path" -d "$extract_dir" || {
      log_err "Failed to unzip: $zip_path"
      return 1
    }
    if [[ -n "$zip_fingerprint" ]]; then
      printf '%s\n' "$zip_fingerprint" > "$fingerprint_file"
    fi
  fi

  local top_dir
  top_dir="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -n1)"
  [[ -n "$top_dir" ]] || { log_err "Unexpected ZIP layout (no top dir) in $extract_dir"; return 1; }
  echo "$top_dir"
}

backup_file() {
  local src="$1"
  local rel="$2"
  local base stamp out_dir
  base="$(basename "$src")"
  stamp="$(date +%Y%m%d-%H%M%S)"
  out_dir="$BACKUP_DIR/$rel"
  mkdir -p "$out_dir" 2>/dev/null || true
  cp "$src" "$out_dir/$base.$stamp.bak"
}

atomic_replace_file() {
  local src="$1"
  local dest="$2"
  local dest_dir dest_base tmp
  dest_dir="$(dirname "$dest")"
  dest_base="$(basename "$dest")"
  mkdir -p "$dest_dir" 2>/dev/null || true
  tmp="$(mktemp "$dest_dir/.${dest_base}.tmp.XXXXXX")" || return 1
  cp "$src" "$tmp" || {
    rm -f "$tmp"
    return 1
  }
  mv "$tmp" "$dest"
}

sync_one_file() {
  # Usage: sync_one_file <src_file> <dest_file> <label> [quiet]
  # Returns: 0 updated/would update, 2 unchanged, 1 error
  local src_file="$1"
  local dest_file="$2"
  local label="$3"
  local quiet="${4:-0}"
  local is_updater_sh=0

  if [[ "$label" == *"(self)"* ]]; then
    is_updater_sh=1
  fi

  if [[ ! -f "$src_file" ]]; then
    log_err "Missing source file: $src_file"
    return 1
  fi

  local merged
  merged="$(mktemp)"
  merge_local_customizations "$dest_file" "$src_file" "$merged"

  # Updater self-update: if only EDIT-block settings differ (formatting), keep on-disk copy.
  if [[ $is_updater_sh -eq 1 && -f "$dest_file" ]] &&
    ! files_equal "$merged" "$dest_file" &&
    upstream_heads_and_tails_match "$dest_file" "$src_file" get_edit_setup_range; then
    cp "$dest_file" "$merged"
  fi

  if [[ -f "$dest_file" ]] && files_equal "$merged" "$dest_file"; then
    rm -f "$merged"
    [[ "$quiet" == "0" ]] && log "Unchanged: $label"
    return 2
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ -f "$dest_file" ]]; then
      [[ "$quiet" == "0" ]] && log "Would update: $label"
    else
      [[ "$quiet" == "0" ]] && log "Would install: $label"
    fi
    rm -f "$merged"
    return 0
  fi

  if [[ -f "$dest_file" ]]; then
    backup_file "$dest_file" "$label"
  else
    mkdir -p "$(dirname "$dest_file")" 2>/dev/null || true
  fi

  atomic_replace_file "$merged" "$dest_file" || {
    rm -f "$merged"
    log_err "Could not write: $dest_file"
    return 1
  }
  rm -f "$merged"
  [[ "$dest_file" == */main.py ]] && chmod +x "$dest_file" 2>/dev/null || true
  [[ "$quiet" == "0" ]] && log "Updated: $label"
  return 0
}

sync_one_extension() {
  # Usage: sync_one_extension <src_dir> <dest_dir> <folder_name>
  # Returns: 0 updated/would update, 2 unchanged, 1 error
  local src_dir="$1"
  local dest_dir="$2"
  local folder_name="$3"
  local man_rc=0 py_rc=0
  local changed_parts=()

  log "Checking: $folder_name"

  sync_one_file "$src_dir/manifest.json" "$dest_dir/manifest.json" "$folder_name/manifest.json" 1
  man_rc=$?
  sync_one_file "$src_dir/main.py" "$dest_dir/main.py" "$folder_name/main.py" 1
  py_rc=$?

  if [[ $man_rc -eq 1 || $py_rc -eq 1 ]]; then
    log_err "Failed: $folder_name"
    return 1
  fi

  [[ $man_rc -eq 0 ]] && changed_parts+=("manifest.json")
  [[ $py_rc -eq 0 ]] && changed_parts+=("main.py")

  if [[ ${#changed_parts[@]} -eq 0 ]]; then
    log "Unchanged: $folder_name"
    return 2
  fi

  local part_csv="${changed_parts[*]}"
  part_csv="${part_csv// /, }"
  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ -d "$dest_dir" ]]; then
      log "Would update: $folder_name (${part_csv})"
    else
      log "Would install: $folder_name (${part_csv})"
    fi
  else
    log "Updated: $folder_name (${part_csv})"
  fi
  return 0
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
  require_cmd sort

  if [[ -z "$NZBGET_SCRIPTDIR" ]]; then
    log_err "NZBGET_SCRIPTDIR is empty. Edit NZBGET_SCRIPTDIR in this script."
    return 1
  fi
  if [[ ! -d "$NZBGET_SCRIPTDIR" ]]; then
    log_err "NZBGET_SCRIPTDIR not found: $NZBGET_SCRIPTDIR. Edit NZBGET_SCRIPTDIR in this script."
    return 1
  fi

  if [[ "$DRY_RUN" != "0" && "$DRY_RUN" != "1" ]]; then
    log_err "DRY_RUN must be 0 or 1"
    return 1
  fi
  if [[ "$FETCH_UPDATES" != "0" && "$FETCH_UPDATES" != "1" ]]; then
    log_err "FETCH_UPDATES must be 0 or 1"
    return 1
  fi
  if [[ "$CLEAR_CACHE" != "0" && "$CLEAR_CACHE" != "1" ]]; then
    log_err "CLEAR_CACHE must be 0 or 1"
    return 1
  fi
  if [[ "$INSTALL_MISSING" != "0" && "$INSTALL_MISSING" != "1" ]]; then
    log_err "INSTALL_MISSING must be 0 or 1"
    return 1
  fi
  if [[ "$RESET_CONFIG" != "0" && "$RESET_CONFIG" != "1" ]]; then
    log_err "RESET_CONFIG must be 0 or 1"
    return 1
  fi

  local src_root
  src_root="$(prepare_source_repo | tail -n 1)" || return 1
  if [[ -z "$src_root" || ! -d "$src_root" ]]; then
    log_err "Invalid source path: $src_root"
    return 1
  fi

  if [[ "$DRY_RUN" == "0" ]]; then
    mkdir -p "$BACKUP_DIR" 2>/dev/null || true
  fi

  log "Syncing NZBGet extension scripts"
  log "Source: $src_root"
  log "Dest: $NZBGET_SCRIPTDIR"
  log "DryRun: $DRY_RUN"

  # Self-update (moving the file does not affect the already-running process).
  local this_path src_updater
  this_path="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  src_updater="$src_root/$(basename "$0")"
  if [[ -f "$src_updater" ]]; then
    log "Checking: $(basename "$0") (self)"
    sync_one_file "$src_updater" "$this_path" "$(basename "$0") (self)" 0
  else
    log "No upstream updater found at: $src_updater"
  fi

  local updated=0 unchanged=0 skipped=0 failed=0
  local -a src_dir_list=()
  local src_dir folder_name dest_dir

  while IFS= read -r src_dir; do
    [[ -n "$src_dir" ]] && src_dir_list+=("$src_dir")
  done < <(find "$src_root" -mindepth 1 -maxdepth 1 -type d -exec test -f "{}/manifest.json" -a -f "{}/main.py" \; -print 2>/dev/null | sort)

  log "Found ${#src_dir_list[@]} extension folder(s) in source"

  for src_dir in "${src_dir_list[@]}"; do
    folder_name="$(basename "$src_dir")"
    dest_dir="$NZBGET_SCRIPTDIR/$folder_name"

    if [[ ${#INCLUDE_FOLDERS[@]} -gt 0 ]]; then
      local _found=0 _inc
      for _inc in "${INCLUDE_FOLDERS[@]}"; do
        [[ "$folder_name" == "$_inc" ]] && { _found=1; break; }
      done
      if [[ $_found -eq 0 ]]; then
        log "Skipped (not in INCLUDE_FOLDERS): $folder_name"
        skipped=$((skipped + 1))
        continue
      fi
    fi

    local _exc
    for _exc in "${EXCLUDE_FOLDERS[@]}"; do
      if [[ "$folder_name" == "$_exc" ]]; then
        log "Skipped (in EXCLUDE_FOLDERS): $folder_name"
        skipped=$((skipped + 1))
        continue 2
      fi
    done

    if [[ ! -d "$dest_dir" ]]; then
      if [[ "$INSTALL_MISSING" != "1" ]]; then
        log "Skipped (not installed): $folder_name"
        skipped=$((skipped + 1))
        continue
      fi
      if [[ "$DRY_RUN" == "0" ]]; then
        mkdir -p "$dest_dir" 2>/dev/null || true
      fi
    fi

    sync_one_extension "$src_dir" "$dest_dir" "$folder_name"
    case $? in
      0) updated=$((updated + 1)) ;;
      2) unchanged=$((unchanged + 1)) ;;
      *) failed=$((failed + 1)) ;;
    esac
  done

  log "Done. Updated: ${updated:-0}, Unchanged: ${unchanged:-0}, Skipped: ${skipped:-0}, Failed: ${failed:-0}"
  if [[ "${failed:-0}" -gt 0 ]]; then
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
