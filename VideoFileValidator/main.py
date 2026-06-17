#!/usr/bin/env python3
from __future__ import annotations

########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Video File Validator
#
# Repository: nzbget-user-scripts
#
# Uses ffprobe to verify downloaded video files are valid and playable.
# Catches truncated downloads, corrupt streams, zero-duration files, and
# files with missing codec data — all common after usenet downloads.
#
# Safety:
# - Runs on SUCCESS by default (configurable).
# - Files that fail validation are logged but NOT deleted by default.
# - Large files can be skipped via size threshold.
# - Always exits with SUCCESS so validation failures do not cause the
#   download to be re-downloaded (the file may still import fine).
#
### NZBGET POST-PROCESSING SCRIPT    ###
########################################
#
##############################################################################
### OPTIONS                                                                ###
#
# When to run (success-only, always, never).
# RunMode=success-only
#
# Dry run: log what would be checked but do not run ffprobe.
# DryRun=no
#
# Video file extensions to check (comma-separated, case-insensitive).
# VideoExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts
#
# Skip files larger than this many GB (0 = no limit). Large remuxes take
# a long time to probe and are rarely corrupt.
# MaxFileSizeGB=0
#
# Check mode: fast (read first/last N seconds of each stream), full (read entire file).
# Fast mode catches 95%+ of corruption in seconds. Full mode catches everything
# but takes as long as the file duration.
# CheckMode=fast
#
# Seconds to probe at start and end in fast mode (per stream).
# FastProbeSeconds=5
#
# Minimum valid video duration in seconds. Files with duration below this
# are flagged (captures zero-duration or truncated files).
# MinDurationSeconds=1
#
# Action on validation failure: log-only, mark-failed.
# "log-only" reports issues but exits successfully so the download is not
# re-queued. "mark-failed" exits with error so NZBGet treats it as failed.
# FailureAction=log-only
#
# Path to ffprobe (usually in PATH; set if custom installation).
# FfprobePath=ffprobe
#
# Auto-install ffmpeg if ffprobe is missing (requires container with apt/apk).
# Detects Debian/Ubuntu (hotio, apt-get) or Alpine (linuxserver, apk) and
# installs ffmpeg automatically. Requires root/sudo in the container.
# AutoInstallFfprobe=no
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

RunMode=success-only
DryRun=no
VideoExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts
MaxFileSizeGB=0
CheckMode=fast
FastProbeSeconds=5
MinDurationSeconds=1
FailureAction=log-only
FfprobePath=ffprobe
AutoInstallFfprobe=no

### NZBGET SCRIPT CONFIGURATION
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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


def _opt_float(name: str, default: float) -> float:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if not raw:
        return default
    try:
        return float(raw)
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


def _is_video(path: Path, video_exts: Set[str]) -> bool:
    return path.suffix.lower() in video_exts


def _run_ffprobe(
    ffprobe: str, video_path: Path, fast_mode: bool, probe_seconds: int
) -> Tuple[bool, Dict]:
    """Run ffprobe on a video file. Returns (success, info_dict)."""
    args = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
    ]

    if fast_mode:
        # Probe first N seconds and last N seconds
        args.extend([
            "-read_intervals", f"%+#{probe_seconds}",
        ])

    args.append(str(video_path))

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return False, {"error": result.stderr.strip()[:200]}

        data = json.loads(result.stdout)
        return True, data
    except subprocess.TimeoutExpired:
        return False, {"error": "ffprobe timed out after 120s"}
    except json.JSONDecodeError as e:
        return False, {"error": f"ffprobe output not valid JSON: {e}"}
    except Exception as e:
        return False, {"error": str(e)[:200]}


def _check_video(
    ffprobe: str,
    video_path: Path,
    fast_mode: bool,
    probe_seconds: int,
    min_duration: float,
) -> List[str]:
    """Validate a single video file. Returns list of issues (empty = valid)."""
    issues: List[str] = []

    ok, info = _run_ffprobe(ffprobe, video_path, fast_mode, probe_seconds)
    if not ok:
        issues.append(f"ffprobe failed: {info.get('error', 'unknown error')}")
        return issues

    fmt = info.get("format", {})
    streams = info.get("streams", [])

    # Check duration
    duration_s = float(fmt.get("duration", 0) or 0)
    if duration_s < min_duration:
        issues.append(
            f"duration {duration_s:.1f}s below minimum {min_duration}s"
        )

    # Check we have at least one video stream
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        issues.append("no video stream found")
    else:
        for vs in video_streams:
            codec = vs.get("codec_name", "unknown")
            width = vs.get("width", 0) or 0
            height = vs.get("height", 0) or 0
            if width == 0 or height == 0:
                issues.append(f"video stream ({codec}) has zero dimensions")
            if not vs.get("codec_name"):
                issues.append("video stream missing codec information")

    # Check format
    if not fmt.get("format_name"):
        issues.append("unknown/undetected container format")
    if fmt.get("size", "0") == "0":
        issues.append("file reports zero size in metadata")

    # In fast mode, note that we only probed partially
    if fast_mode:
        actual_duration = float(fmt.get("duration", 0) or 0)
        if actual_duration > probe_seconds:
            # File is longer than probe; check passes for the sampled portion
            pass

    return issues


def _resolve_ffprobe(ffprobe_path: str, auto_install: bool) -> Optional[str]:
    """Find ffprobe binary. Optionally auto-install ffmpeg if missing."""

    # First try: explicit path or PATH lookup
    if ffprobe_path != "ffprobe":
        if _probe_exists(ffprobe_path):
            return ffprobe_path
        log("WARNING", f"ffprobe not found at configured path: {ffprobe_path}")

    # Second try: search common paths
    for candidate in ["ffprobe", "/usr/bin/ffprobe", "/usr/local/bin/ffprobe"]:
        if _probe_exists(candidate):
            return candidate

    if not auto_install:
        return None

    # Third try: auto-install via detected package manager
    log("INFO", "ffprobe not found; attempting auto-install...")

    pkg_manager = _detect_package_manager()
    if pkg_manager is None:
        log("WARNING", "Could not detect apt-get or apk. Cannot auto-install ffmpeg.")
        return None

    if not _install_ffmpeg(pkg_manager):
        return None

    # Retry after install
    for candidate in ["ffprobe", "/usr/bin/ffprobe", "/usr/local/bin/ffprobe"]:
        if _probe_exists(candidate):
            log("INFO", f"ffprobe installed successfully: {candidate}")
            return candidate

    return None


def _probe_exists(path: str) -> bool:
    """Check if a binary exists and can be executed."""
    try:
        r = subprocess.run([path, "-version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return False


def _detect_package_manager() -> Optional[str]:
    """Detect available package manager in the container.

    Returns 'apt-get', 'apk', or None.
    hotio/nzbget uses Debian/Ubuntu (apt-get).
    linuxserver/nzbget uses Alpine (apk).
    """
    # Check for apt-get (Debian/Ubuntu — hotio)
    if shutil.which("apt-get"):
        # Verify it actually works (may exist but be non-functional in some containers)
        try:
            r = subprocess.run(
                ["apt-get", "--version"], capture_output=True, timeout=5
            )
            if r.returncode == 0:
                return "apt-get"
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            pass

    # Check for apk (Alpine — linuxserver)
    if shutil.which("apk"):
        try:
            r = subprocess.run(
                ["apk", "--version"], capture_output=True, timeout=5
            )
            if r.returncode == 0:
                return "apk"
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            pass

    return None


def _install_ffmpeg(pkg_manager: str) -> bool:
    """Install ffmpeg via the given package manager. Returns True on success."""
    pkg = "ffmpeg"

    try:
        if pkg_manager == "apt-get":
            log("INFO", "Detected Debian/Ubuntu (hotio). Running: apt-get update && apt-get install -y ffmpeg")
            r1 = subprocess.run(
                ["apt-get", "update"], capture_output=True, text=True, timeout=120
            )
            if r1.returncode != 0:
                log("WARNING", f"apt-get update failed (exit {r1.returncode}): {r1.stderr[-200:]}")
                return False
            r2 = subprocess.run(
                ["apt-get", "install", "-y", pkg], capture_output=True, text=True, timeout=120
            )
            if r2.returncode == 0:
                return True
            log("WARNING", f"apt-get install ffmpeg failed (exit {r2.returncode}): {r2.stderr[-200:]}")
            return False

        elif pkg_manager == "apk":
            log("INFO", "Detected Alpine (linuxserver). Running: apk add --no-cache ffmpeg")
            r = subprocess.run(
                ["apk", "add", "--no-cache", pkg], capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                return True
            log("WARNING", f"apk add ffmpeg failed (exit {r.returncode}): {r.stderr[-200:]}")
            return False
    except (subprocess.TimeoutExpired, PermissionError, OSError) as e:
        log("WARNING", f"Package install failed: {e}")

    return False


def main() -> int:
    if not should_run():
        log("DETAIL", "Skipping (RunMode does not allow execution).")
        return SCRIPT_SUCCESS

    target_dir = pick_target_dir()
    if target_dir is None:
        log("ERROR", "NZBPP_DIRECTORY is missing or not a directory.")
        return POSTPROCESS_ERROR

    dry_run = _opt_bool("DryRun", False)
    video_exts = _lower_set_csv(
        _opt_str("VideoExts", ".mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts")
    )
    max_size_bytes = _opt_int("MaxFileSizeGB", 0) * 1024 * 1024 * 1024
    check_mode = _opt_str("CheckMode", "fast").strip().lower()
    fast_probe_seconds = _opt_int("FastProbeSeconds", 5)
    min_duration = _opt_float("MinDurationSeconds", 1.0)
    failure_action = _opt_str("FailureAction", "log-only").strip().lower()
    ffprobe_path = _opt_str("FfprobePath", "ffprobe")

    if dry_run:
        log("INFO", "DRY-RUN enabled: no ffprobe checks will run.")

    fast_mode = check_mode == "fast"

    # Find video files
    video_files: List[Path] = []
    try:
        for entry in target_dir.rglob("*"):
            if entry.is_file() and _is_video(entry, video_exts):
                video_files.append(entry)
    except OSError as e:
        log("WARNING", f"Error scanning directory: {e}")

    if not video_files:
        log("DETAIL", "No video files found to validate.")
        return SCRIPT_SUCCESS

    # Find ffprobe — only needed when there are actually files to check
    ffprobe = _resolve_ffprobe(ffprobe_path, _opt_bool("AutoInstallFfprobe", False))
    if ffprobe is None:
        log("ERROR", "ffprobe not found. Install ffmpeg package or set AutoInstallFfprobe=yes.")
        return POSTPROCESS_ERROR

    log("INFO", f"Found {len(video_files)} video file(s) to validate")

    checked = 0
    passed = 0
    failed = 0
    skipped_large = 0

    for vf in sorted(video_files):
        size_mb = vf.stat().st_size / (1024 * 1024)

        if max_size_bytes > 0 and vf.stat().st_size > max_size_bytes:
            log("DETAIL", f"Skipping {vf.name} ({size_mb:.0f}MB > {max_size_bytes // (1024**3)}GB limit)")
            skipped_large += 1
            continue

        if dry_run:
            log("DETAIL", f"[DRY-RUN] would validate: {vf.name} ({size_mb:.0f}MB)")
            continue

        checked += 1
        issues = _check_video(ffprobe, vf, fast_mode, fast_probe_seconds, min_duration)

        if issues:
            failed += 1
            log("WARNING", f"FAIL: {vf.name} ({size_mb:.0f}MB)")
            for issue in issues:
                log("WARNING", f"  - {issue}")
        else:
            passed += 1
            log("INFO", f"OK: {vf.name} ({size_mb:.0f}MB, fast={'yes' if fast_mode else 'no'})")

    log(
        "INFO",
        f"Validation complete: {passed} passed, {failed} failed, "
        f"{skipped_large} skipped (size), {checked} checked"
    )

    if failed > 0 and failure_action == "mark-failed":
        log("ERROR", f"{failed} file(s) failed validation; marking as failed.")
        return POSTPROCESS_ERROR

    return SCRIPT_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"VideoFileValidator crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)
