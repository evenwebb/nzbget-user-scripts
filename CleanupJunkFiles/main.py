#!/usr/bin/env python3
########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Cleanup Junk Files
#
# Removes leftover "junk" files after a successful download/unpack to keep
# your library directories clean (e.g. .sfv, .url, .nzb, .par2, etc.).
#
# Safety:
# - Runs only when NZBPP_TOTALSTATUS=SUCCESS (configurable).
# - By default removes only low-risk junk file types.
# - Always exits with SUCCESS (93) when it runs, so cleanup cannot cause the
#   download to be marked as failed.
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
# Dry run: log what would be deleted but do not delete anything.
# DryRun=no
#
# Delete empty directories after cleanup.
# DeleteEmptyDirs=yes
#
# File globs (comma-separated) to delete (relative to NZBPP_DIRECTORY; recursive).
# Keep this conservative: these are typically safe and not needed for import.
# DeleteGlobs=*.sfv,*.srr,*.url,*.nzb,*.nfo-orig,*.jpg,*.jpeg,*.png,*.gif,*.webp,*.lnk,Thumbs.db,.DS_Store
#
# Extra globs to delete if enabled.
# DeletePar2=yes
# DeletePar2Globs=*.par2,*.par
#
# Remove leftover archives/volumes after unpack (DISABLED by default).
# Enable only if you're sure you don't need archives after import.
# DeleteArchives=no
# DeleteArchiveGlobs=*.rar,*.r??,*.part??.rar,*.7z,*.zip
#
# Never delete these extensions (comma-separated, case-insensitive).
# Defaults protect common import targets (video/subtitles/metadata).
# NeverDeleteExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.iso,.srt,.ass,.ssa,.sub,.idx,.sup,.nfo
#
# Delete sample videos.
# DeleteSamples=yes
#
# Folder names which are treated as sample folders (comma-separated, case-insensitive).
# SampleDirNames=sample,samples
#
# Video extensions to consider when deleting "sample" files (comma-separated, case-insensitive).
# SampleVideoExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts
#
# If a video filename contains "sample", delete it only if it is smaller than this many MB.
# This prevents accidental deletion of legitimate content.
# SampleMaxSizeMB=250
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

RunMode=success-only
DryRun=no
DeleteEmptyDirs=yes
DeleteGlobs=*.sfv,*.srr,*.url,*.nzb,*.nfo-orig,*.jpg,*.jpeg,*.png,*.gif,*.webp,*.lnk,Thumbs.db,.DS_Store
DeletePar2=yes
DeletePar2Globs=*.par2,*.par
DeleteArchives=no
DeleteArchiveGlobs=*.rar,*.r??,*.part??.rar,*.7z,*.zip
NeverDeleteExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.iso,.srt,.ass,.ssa,.sub,.idx,.sup,.nfo

DeleteSamples=yes
SampleDirNames=sample,samples
SampleVideoExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts
SampleMaxSizeMB=250

KeepGlobs=
KeepDirs=

ArchiveDeleteRequiresMedia=yes
MediaExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.iso

### NZBGET SCRIPT CONFIGURATION
"""

import fnmatch
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set, Tuple

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


def _opt_int(name: str, default: int) -> int:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if not raw:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _lower_set_csv(value: str) -> Set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def _iter_files_recursive(root: Path) -> Iterable[Path]:
    # rglob('*') includes dirs; filter to files.
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def _matches_any_glob(rel_path: str, globs: List[str]) -> bool:
    # Match against rel-path and basename to be user-friendly.
    base = os.path.basename(rel_path)
    for g in globs:
        if fnmatch.fnmatch(rel_path, g) or fnmatch.fnmatch(base, g):
            return True
    return False


def _is_archive_volume(name: str) -> bool:
    # e.g. r00, r01, ... or part01.rar handled by glob, but keep extra guard.
    return bool(re.search(r"\.r\d\d$", name.lower()))


def _path_parts_lower(p: Path) -> Tuple[str, ...]:
    return tuple(part.lower() for part in p.parts)


def _is_in_sample_dir(root: Path, p: Path, sample_dir_names: Set[str]) -> bool:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return False
    parts = _path_parts_lower(rel)
    return any(part in sample_dir_names for part in parts)


def _looks_like_sample_file(name: str) -> bool:
    return "sample" in name.lower()


def _path_parts_lower_rel(root: Path, p: Path) -> Tuple[str, ...]:
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = p
    return tuple(part.lower() for part in rel.parts)


def _should_keep(plan: "Plan", p: Path, rel: str) -> bool:
    if plan.keep_globs and _matches_any_glob(rel, plan.keep_globs):
        return True
    if plan.keep_dir_names:
        parts = _path_parts_lower_rel(plan.root, p)
        if any(part in plan.keep_dir_names for part in parts):
            return True
    return False


def _has_unpacked_media(plan: "Plan") -> bool:
    if not plan.media_exts:
        return False
    for p in _iter_files_recursive(plan.root):
        if p.suffix.lower() in plan.media_exts:
            return True
    return False


@dataclass
class Plan:
    root: Path
    dry_run: bool
    delete_empty_dirs: bool
    delete_globs: List[str]
    delete_par2: bool
    delete_par2_globs: List[str]
    delete_archives: bool
    delete_archive_globs: List[str]
    never_delete_exts: Set[str]
    delete_samples: bool
    sample_dir_names: Set[str]
    sample_video_exts: Set[str]
    sample_max_size_bytes: int
    keep_globs: List[str]
    keep_dir_names: Set[str]
    archive_delete_requires_media: bool
    media_exts: Set[str]


def build_plan() -> Plan:
    directory = os.environ.get("NZBPP_DIRECTORY", "")
    root = Path(directory) if directory else Path()

    return Plan(
        root=root,
        dry_run=_opt_bool("DryRun", False),
        delete_empty_dirs=_opt_bool("DeleteEmptyDirs", True),
        delete_globs=_split_csv(_opt_str("DeleteGlobs", "*.sfv,*.srr,*.url,*.nzb,.DS_Store,Thumbs.db")),
        delete_par2=_opt_bool("DeletePar2", True),
        delete_par2_globs=_split_csv(_opt_str("DeletePar2Globs", "*.par2,*.par")),
        delete_archives=_opt_bool("DeleteArchives", False),
        delete_archive_globs=_split_csv(_opt_str("DeleteArchiveGlobs", "*.rar,*.r??,*.part??.rar,*.7z,*.zip")),
        never_delete_exts=_lower_set_csv(
            _opt_str(
                "NeverDeleteExts",
                ".mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.iso,.srt,.ass,.ssa,.sub,.idx,.sup,.nfo",
            )
        ),
        delete_samples=_opt_bool("DeleteSamples", True),
        sample_dir_names=_lower_set_csv(_opt_str("SampleDirNames", "sample,samples")),
        sample_video_exts=_lower_set_csv(_opt_str("SampleVideoExts", ".mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts")),
        sample_max_size_bytes=max(1, _opt_int("SampleMaxSizeMB", 250)) * 1024 * 1024,
        keep_globs=_split_csv(_opt_str("KeepGlobs", "")),
        keep_dir_names=_lower_set_csv(_opt_str("KeepDirs", "")),
        archive_delete_requires_media=_opt_bool("ArchiveDeleteRequiresMedia", True),
        media_exts=_lower_set_csv(_opt_str("MediaExts", ".mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.iso")),
    )


def should_run() -> bool:
    mode = _opt_str("RunMode", "success-only").strip().lower()
    if mode == "always":
        return True
    # default: success-only
    return os.environ.get("NZBPP_TOTALSTATUS", "") == "SUCCESS"


def delete_samples(plan: Plan) -> int:
    if not plan.delete_samples:
        return 0
    if not plan.root or not plan.root.exists() or not plan.root.is_dir():
        return 0

    deleted = 0
    for p in _iter_files_recursive(plan.root):
        ext = p.suffix.lower()
        if ext not in plan.sample_video_exts:
            continue

        rel = str(p.relative_to(plan.root))
        if _should_keep(plan, p, rel):
            continue

        in_sample_dir = _is_in_sample_dir(plan.root, p, plan.sample_dir_names)
        looks_like_sample = _looks_like_sample_file(p.name)

        if not in_sample_dir and not looks_like_sample:
            continue

        if not in_sample_dir:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > plan.sample_max_size_bytes:
                log("DETAIL", f"Skipping large 'sample' candidate: {rel} ({size} bytes)")
                continue

        if plan.dry_run:
            log("INFO", f"[dry-run] Would delete sample: {rel}")
            deleted += 1
            continue

        try:
            p.unlink()
            log("INFO", f"Deleted sample: {rel}")
            deleted += 1
        except OSError as e:
            log("WARNING", f"Failed to delete sample {rel}: {e}")

    return deleted


def delete_files(plan: Plan) -> int:
    if not plan.root or not plan.root.exists() or not plan.root.is_dir():
        log("DETAIL", "No directory to clean (NZBPP_DIRECTORY missing or not a directory).")
        return 0

    base_globs = list(plan.delete_globs)
    if plan.delete_par2:
        base_globs.extend(plan.delete_par2_globs)

    allow_archive_delete = plan.delete_archives
    if plan.delete_archives and plan.archive_delete_requires_media and not _has_unpacked_media(plan):
        allow_archive_delete = False
        log("DETAIL", "Archive deletion skipped: no unpacked media detected (ArchiveDeleteRequiresMedia=yes).")

    globs = base_globs + (list(plan.delete_archive_globs) if allow_archive_delete else [])

    deleted = 0
    for p in _iter_files_recursive(plan.root):
        rel = str(p.relative_to(plan.root))
        ext = p.suffix.lower()
        if ext in plan.never_delete_exts:
            continue
        if _should_keep(plan, p, rel):
            continue

        if plan.delete_archives and _is_archive_volume(p.name):
            # extra allow for r00/r01 even if glob misses it
            match = allow_archive_delete
        else:
            match = _matches_any_glob(rel, globs)

        if not match:
            continue

        if plan.dry_run:
            log("INFO", f"[dry-run] Would delete: {rel}")
            deleted += 1
            continue

        try:
            p.unlink()
            log("INFO", f"Deleted: {rel}")
            deleted += 1
        except OSError as e:
            log("WARNING", f"Failed to delete {rel}: {e}")

    return deleted


def delete_empty_dirs(root: Path, dry_run: bool) -> int:
    removed = 0
    # Walk bottom-up so children are removed first.
    for d in sorted([p for p in root.rglob("*") if p.is_dir()], reverse=True):
        try:
            if any(d.iterdir()):
                continue
            rel = str(d.relative_to(root))
            if dry_run:
                log("INFO", f"[dry-run] Would remove empty dir: {rel}")
                removed += 1
            else:
                d.rmdir()
                log("DETAIL", f"Removed empty dir: {rel}")
                removed += 1
        except OSError:
            continue
    return removed


def main() -> int:
    if not should_run():
        return POSTPROCESS_NONE

    plan = build_plan()
    # Delete samples first so empty sample dirs can be removed afterwards.
    deleted = delete_samples(plan) + delete_files(plan)
    removed_dirs = 0
    if plan.delete_empty_dirs and plan.root and plan.root.exists() and plan.root.is_dir():
        removed_dirs = delete_empty_dirs(plan.root, plan.dry_run)

    log("INFO", f"Cleanup complete: deleted={deleted}, empty_dirs_removed={removed_dirs}")
    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"Cleanup crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)

