#!/usr/bin/env python3
from __future__ import annotations

########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Sample Remover
#
# Detects and removes sample video files left after usenet/torrent downloads.
# Uses name matching, size thresholds, and directory detection to identify
# sample files without touching real content.
#
# Safety:
# - Runs only on SUCCESS by default (configurable).
# - Size threshold prevents accidental removal of short-form content.
# - File extensions are restricted to video types.
# - Always exits with SUCCESS so cleanup cannot fail the download.
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
# Maximum file size in MB for a sample to be eligible for deletion.
# Files larger than this are never considered samples.
# SampleMaxSizeMB=200
#
# Delete files whose name contains "sample" (case-insensitive).
# DeleteSampleNamed=yes
#
# Delete directories named "sample" or "samples" and their contents.
# DeleteSampleDirs=yes
#
# Video file extensions to scan (comma-separated, case-insensitive).
# Non-video files are never deleted even if named "sample".
# VideoExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.webm
#
# Delete files in directories named "sample" or "samples" even if the
# file itself is not named "sample" (these are almost always samples).
# DeleteContentsOfSampleDirs=yes
#
# Minimum file size in MB below which any video is considered a sample
# (very small video files are unlikely to be real content).
# TinyVideoMaxMB=15
#
# Delete tiny video files even if not named "sample".
# DeleteTinyVideos=no
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

RunMode=success-only
DryRun=no
SampleMaxSizeMB=200
DeleteSampleNamed=yes
DeleteSampleDirs=yes
VideoExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.webm
DeleteContentsOfSampleDirs=yes
TinyVideoMaxMB=15
DeleteTinyVideos=no

### NZBGET SCRIPT CONFIGURATION
"""

import os
import shutil
from pathlib import Path
from typing import List, Optional, Set

SCRIPT_SUCCESS = 93
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
        return int(raw)
    except ValueError:
        return default


def _lower_set_csv(value: str) -> Set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def should_run() -> bool:
    mode = _opt_str("RunMode", "success-only").strip().lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    total_status = os.environ.get("NZBPP_TOTALSTATUS", "").strip().upper()
    return total_status == "SUCCESS"


def pick_target_dir() -> Optional[Path]:
    d = os.environ.get("NZBPP_DIRECTORY", "").strip()
    if not d:
        return None
    p = Path(d)
    return p if p.is_dir() else None


def _is_video_file(path: Path, video_exts: Set[str]) -> bool:
    return path.suffix.lower() in video_exts


def _is_sample_named(path: Path) -> bool:
    return "sample" in path.stem.lower()


def _delete_path(path: Path, dry_run: bool) -> bool:
    if dry_run:
        log("DETAIL", f"[DRY-RUN] would delete: {path}")
        return True
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        log("INFO", f"Deleted: {path}")
        return True
    except OSError as e:
        log("WARNING", f"Could not delete {path}: {e}")
        return False


def _find_sample_dirs(root: Path) -> List[Path]:
    """Find all directories named 'sample' or 'samples' under root."""
    sample_dirs: List[Path] = []
    try:
        for entry in root.rglob("*"):
            if entry.is_dir() and entry.name.lower() in {"sample", "samples"}:
                sample_dirs.append(entry)
    except OSError:
        pass
    return sample_dirs


def main() -> int:
    if not should_run():
        log("DETAIL", "Skipping (RunMode does not allow execution).")
        return SCRIPT_SUCCESS

    target_dir = pick_target_dir()
    if target_dir is None:
        log("ERROR", "NZBPP_DIRECTORY is missing or not a directory.")
        return POSTPROCESS_ERROR

    dry_run = _opt_bool("DryRun", False)
    sample_max_size = _opt_int("SampleMaxSizeMB", 200) * 1024 * 1024
    tiny_max_size = _opt_int("TinyVideoMaxMB", 15) * 1024 * 1024
    delete_sample_named = _opt_bool("DeleteSampleNamed", True)
    delete_sample_dirs = _opt_bool("DeleteSampleDirs", True)
    delete_contents = _opt_bool("DeleteContentsOfSampleDirs", True)
    delete_tiny = _opt_bool("DeleteTinyVideos", False)
    video_exts = _lower_set_csv(_opt_str("VideoExts", ".mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.webm"))

    if dry_run:
        log("INFO", "DRY-RUN enabled: no files will be deleted.")

    deleted_count = 0
    deleted_bytes = 0

    # 1. Delete files named *sample* that are video and under size threshold
    if delete_sample_named:
        log("INFO", f"Scanning for sample-named files in: {target_dir}")
        try:
            for entry in target_dir.rglob("*"):
                if not entry.is_file():
                    continue
                if not _is_video_file(entry, video_exts):
                    continue
                if not _is_sample_named(entry):
                    continue
                size = entry.stat().st_size
                if size > sample_max_size:
                    log("DETAIL", f"Skipping {entry.name} (size {size} > {sample_max_size})")
                    continue
                if _delete_path(entry, dry_run):
                    deleted_count += 1
                    deleted_bytes += size
        except OSError as e:
            log("WARNING", f"Error scanning directory: {e}")

    # 2. Delete sample directories (and optionally contents)
    sample_dirs = _find_sample_dirs(target_dir)
    if delete_sample_dirs and sample_dirs:
        log("INFO", f"Found {len(sample_dirs)} sample director(ies)")
        for sd in sample_dirs:
            if delete_contents:
                for entry in sd.rglob("*"):
                    if entry.is_file() and _is_video_file(entry, video_exts):
                        sz = entry.stat().st_size
                        if _delete_path(entry, dry_run):
                            deleted_count += 1
                            deleted_bytes += sz
            if _delete_path(sd, dry_run):
                deleted_count += 1

    # 3. Delete tiny video files (if enabled)
    if delete_tiny:
        log("INFO", f"Scanning for tiny video files (< {tiny_max_size // (1024*1024)}MB) in: {target_dir}")
        try:
            for entry in target_dir.rglob("*"):
                if not entry.is_file():
                    continue
                if not _is_video_file(entry, video_exts):
                    continue
                if _is_sample_named(entry):
                    continue  # already handled above
                size = entry.stat().st_size
                if size < tiny_max_size:
                    if _delete_path(entry, dry_run):
                        deleted_count += 1
                        deleted_bytes += size
        except OSError as e:
            log("WARNING", f"Error scanning directory: {e}")

    freed = deleted_bytes / (1024 * 1024)
    if deleted_count > 0:
        log("INFO", f"Removed {deleted_count} sample item(s), freed {freed:.1f} MB")
    else:
        log("DETAIL", "No sample files found.")

    return SCRIPT_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"SampleRemover crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)
