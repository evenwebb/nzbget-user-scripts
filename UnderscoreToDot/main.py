#!/usr/bin/env python3
########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Underscore To Dot
#
# Replaces underscores with dots in filenames (and optionally directory names)
# so poorly named indexer releases (often HONE-style) match Sonarr/Radarr scene
# naming and avoid redownload loops.
#
# Improvements over minimal one-liners:
# - Runs on SUCCESS by default; optional DryRun.
# - Handles target collisions (skip or numeric suffix).
# - Safe stem vs whole-name modes; optional extension filtering.
# - Bottom-up directory renames so parents still resolve.
#
### NZBGET POST-PROCESSING SCRIPT    ###
########################################
#
##############################################################################
### OPTIONS                                                                ###
#
# When to run (success-only, always).
# RunMode=success-only
#
# Dry run: log what would be renamed but do not rename anything.
# DryRun=no
#
# What to rename.
# RenameFiles=yes
# RenameDirs=no
#
# Apply to which directory.
# TargetDir=directory
# - directory: uses NZBPP_DIRECTORY
# - final: uses NZBPP_FINALDIR (fallback to directory)
#
# Where to replace underscores with dots.
# ReplaceScope=stem
# - stem: only in the part before the last dot (recommended)
# - all: stem and extension segment (everything after last dot, without the dot)
#
# File extensions eligible for rename (comma-separated, case-insensitive).
# Leave empty to allow all files. Example: .mkv,.mp4,.avi,.nfo,.srt
# EligibleExts=
#
# If a target name exists, skip rename (otherwise we add numeric suffix).
# SkipIfTargetExists=no
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

RunMode=success-only
DryRun=no
RenameFiles=yes
RenameDirs=no
TargetDir=directory
ReplaceScope=stem
EligibleExts=
SkipIfTargetExists=no

### NZBGET SCRIPT CONFIGURATION
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple

# NZBGet extension exit codes (post-processing).
POSTPROCESS_SUCCESS = 93
POSTPROCESS_ERROR = 94
POSTPROCESS_NONE = 95


def log(kind: str, message: str) -> None:
    print(f"[{kind}] {message}")


def _opt_str(name: str, default: str) -> str:
    raw = os.environ.get(f"NZBPO_{name}", "")
    return raw if raw != "" else default


def _opt_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if not raw:
        return default
    return raw.strip().lower() in {"yes", "true", "1", "on"}


def _lower_set_csv(value: str) -> Set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def should_run() -> bool:
    mode = _opt_str("RunMode", "success-only").strip().lower()
    if mode == "always":
        return True
    return os.environ.get("NZBPP_TOTALSTATUS", "") == "SUCCESS"


def pick_target_dir() -> Optional[Path]:
    directory = os.environ.get("NZBPP_DIRECTORY", "")
    finaldir = os.environ.get("NZBPP_FINALDIR", "")
    mode = _opt_str("TargetDir", "directory").strip().lower()
    if mode == "final":
        p = finaldir or directory
    else:
        p = directory
    return Path(p) if p else None


def split_stem_suffix(filename: str) -> Tuple[str, str]:
    """Splits basename into (stem, suffix) using the last dot (scene-friendly)."""
    idx = filename.rfind(".")
    if idx <= 0:
        return filename, ""
    return filename[:idx], filename[idx:]


def with_unique_suffix(target: Path) -> Path:
    stem, suffix = split_stem_suffix(target.name)
    for i in range(1, 1000):
        candidate = target.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return target


def new_basename_stem_only(name: str) -> str:
    stem, sfx = split_stem_suffix(name)
    return f"{stem.replace('_', '.')}{sfx}"


def new_basename_all(name: str) -> str:
    stem, sfx = split_stem_suffix(name)
    new_stem = stem.replace("_", ".")
    if not sfx:
        return new_stem
    if sfx.startswith("."):
        ext_body = sfx[1:]
        if not ext_body:
            return new_stem
        new_ext = ext_body.replace("_", ".")
        return f"{new_stem}.{new_ext}"
    return f"{new_stem.replace('_', '.')}{sfx.replace('_', '.')}"


def compute_new_name(name: str, replace_scope: str) -> str:
    scope = replace_scope.strip().lower()
    if scope == "all":
        return new_basename_all(name)
    return new_basename_stem_only(name)


@dataclass(frozen=True)
class Plan:
    root: Path
    dry_run: bool
    rename_files: bool
    rename_dirs: bool
    replace_scope: str
    eligible_exts: Set[str]
    skip_if_target_exists: bool


def build_plan(root: Path) -> Plan:
    scope = _opt_str("ReplaceScope", "stem").strip().lower()
    if scope not in {"stem", "all"}:
        scope = "stem"

    exts_raw = _opt_str("EligibleExts", "")
    eligible = _lower_set_csv(exts_raw)

    return Plan(
        root=root,
        dry_run=_opt_bool("DryRun", False),
        rename_files=_opt_bool("RenameFiles", True),
        rename_dirs=_opt_bool("RenameDirs", False),
        replace_scope=scope,
        eligible_exts=eligible,
        skip_if_target_exists=_opt_bool("SkipIfTargetExists", False),
    )


def iter_paths(root: Path, plan: Plan) -> Iterable[Path]:
    if plan.rename_dirs:
        for dirpath, dirnames, _ in os.walk(root, topdown=False):
            for d in dirnames:
                yield Path(dirpath) / d
    if plan.rename_files:
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                yield Path(dirpath) / fn


def rename_path(p: Path, new_name: str, plan: Plan) -> bool:
    if p.name == new_name:
        return False

    target = p.with_name(new_name)
    if target.exists():
        if plan.skip_if_target_exists:
            log("DETAIL", f"Skipping (target exists): {p.relative_to(plan.root)} -> {target.name}")
            return False
        target = with_unique_suffix(target)

    rel = str(p.relative_to(plan.root))
    if plan.dry_run:
        log("INFO", f"[dry-run] Rename: {rel} -> {target.name}")
        return True

    try:
        p.rename(target)
        log("INFO", f"Renamed: {rel} -> {target.name}")
        return True
    except OSError as e:
        log("WARNING", f"Failed to rename {rel}: {e}")
        return False


def process(plan: Plan) -> int:
    if not plan.root.exists() or not plan.root.is_dir():
        log("DETAIL", "No directory to process (missing or not a directory).")
        return 0

    changed = 0
    for p in iter_paths(plan.root, plan):
        if p.name in {".DS_Store", "Thumbs.db"}:
            continue

        if p.is_file():
            ext = p.suffix.lower()
            if plan.eligible_exts and ext not in plan.eligible_exts:
                continue

        if "_" not in p.name:
            continue

        new_name = compute_new_name(p.name, plan.replace_scope)
        if rename_path(p, new_name, plan):
            changed += 1

    return changed


def main() -> int:
    if not should_run():
        return POSTPROCESS_NONE

    root = pick_target_dir()
    if not root:
        log("DETAIL", "No target directory (NZBPP_DIRECTORY / NZBPP_FINALDIR missing).")
        return POSTPROCESS_NONE

    plan = build_plan(root)
    changed = process(plan)
    log("INFO", f"UnderscoreToDot complete: renamed={changed}")
    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"UnderscoreToDot crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)
