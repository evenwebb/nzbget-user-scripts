#!/usr/bin/env python3
########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Reverse Name
#
# Some release groups reverse the file/folder names (e.g. "vkm.10E20S..." etc).
# This script detects likely reversed names and renames them back.
#
# Safety:
# - Runs only on SUCCESS by default.
# - Default detection is conservative (only rename when reversed name looks valid).
# - Supports DryRun.
#
# Tip:
# - Put this script after unpack and before any importer (Sonarr/Radarr) scripts.
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
# Only rename when the reversed name "looks right" (recommended).
# OnlyIfLooksReversed=yes
#
# Require a strong identifier (SxxEyy / 1x02 / YYYY-MM-DD) in the reversed name.
# This is the main safeguard against false positives.
# RequireStrongId=yes
#
# Count a standalone year (e.g. 1999, 2024) as a strong identifier for movies.
# To reduce false positives, the reversed name must also include other release tokens
# (resolution/source/codec) according to MinScore.
# StrongIdAllowYear=yes
#
# Minimum heuristic score to rename when OnlyIfLooksReversed=yes.
# MinScore=2
#
# File extensions eligible for rename (comma-separated, case-insensitive).
# EligibleExts=.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.srt,.ass,.ssa,.sub,.idx,.sup,.nfo
#
# Skip renaming if target already exists (otherwise we add a numeric suffix).
# SkipIfTargetExists=no
#
##############################################################################

import os
import re
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


def _lower_set_csv(value: str) -> Set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def should_run() -> bool:
    mode = _opt_str("RunMode", "success-only").strip().lower()
    if mode == "always":
        return True
    return os.environ.get("NZBPP_TOTALSTATUS", "") == "SUCCESS"


def split_stem_suffix(filename: str) -> Tuple[str, str]:
    """
    Splits "name.ext" into ("name", ".ext") using the last dot.
    This is important for scene/release names which commonly contain many dots.
    """
    idx = filename.rfind(".")
    if idx <= 0:
        return filename, ""
    return filename[:idx], filename[idx:]


_EP_PATTERNS = [
    re.compile(r"\bs\d{1,2}e\d{1,2}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}x\d{2}\b", re.IGNORECASE),  # 1x02
    re.compile(r"\b\d{4}[.\-_ ]\d{2}[.\-_ ]\d{2}\b"),  # 2026-04-30 / 2026.04.30
    re.compile(r"\b\d{3,4}p\b", re.IGNORECASE),  # 720p, 1080p, 2160p
    re.compile(r"\b(?:webrip|web[-_. ]?dl|bluray|bdrip|hdtv|dvdrip)\b", re.IGNORECASE),
    re.compile(r"\b(?:x264|x265|h\.?264|h\.?265|hevc|av1)\b", re.IGNORECASE),
]

_STRONG_ID_PATTERNS = [
    re.compile(r"\bs\d{1,2}e\d{1,2}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}x\d{2}\b", re.IGNORECASE),
    re.compile(r"\b\d{4}[.\-_ ]\d{2}[.\-_ ]\d{2}\b"),
]

_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")
_ALNUM_RE = re.compile(r"[a-z0-9]")


def has_strong_id(name: str) -> bool:
    n = name.replace("_", " ").replace(".", " ").replace("-", " ")
    return any(p.search(n) for p in _STRONG_ID_PATTERNS)


def has_movie_year(name: str) -> bool:
    n = name.replace("_", " ").replace(".", " ").replace("-", " ")
    return _YEAR_PATTERN.search(n) is not None


def _normalized_tokens(name: str) -> List[str]:
    n = name.lower()
    n = re.sub(r"[\[\]\(\)\{\}]", " ", n)
    n = re.sub(r"[._\-]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return [t for t in n.split(" ") if t]


def _token_quality(name: str) -> float:
    tokens = _normalized_tokens(name)
    if not tokens:
        return 0.0
    good = 0
    for t in tokens:
        if _ALNUM_RE.search(t):
            good += 1
    return good / len(tokens)


def score_name(name: str) -> int:
    n = name.replace("_", " ").replace(".", " ").replace("-", " ")
    score = 0
    for pat in _EP_PATTERNS:
        if pat.search(n):
            score += 1
    return score


def looks_reversed(
    original_stem: str,
    reversed_stem: str,
    min_score: int,
    require_strong_id: bool,
    allow_year: bool,
) -> bool:
    s_orig = score_name(original_stem)
    s_rev = score_name(reversed_stem)

    if s_rev < min_score or s_rev <= s_orig:
        return False

    if require_strong_id:
        rev_has = has_strong_id(reversed_stem)
        orig_has = has_strong_id(original_stem)

        if allow_year and not rev_has:
            rev_has = has_movie_year(reversed_stem)
            orig_has = orig_has or has_movie_year(original_stem)

        if not rev_has or orig_has:
            return False

    if _token_quality(reversed_stem) < _token_quality(original_stem):
        return False

    return True


@dataclass
class Plan:
    root: Path
    dry_run: bool
    rename_files: bool
    rename_dirs: bool
    only_if_looks_reversed: bool
    require_strong_id: bool
    strong_id_allow_year: bool
    min_score: int
    eligible_exts: Set[str]
    skip_if_target_exists: bool


def build_plan() -> Plan:
    directory = os.environ.get("NZBPP_DIRECTORY", "")
    root = Path(directory) if directory else Path()
    return Plan(
        root=root,
        dry_run=_opt_bool("DryRun", False),
        rename_files=_opt_bool("RenameFiles", True),
        rename_dirs=_opt_bool("RenameDirs", False),
        only_if_looks_reversed=_opt_bool("OnlyIfLooksReversed", True),
        require_strong_id=_opt_bool("RequireStrongId", True),
        strong_id_allow_year=_opt_bool("StrongIdAllowYear", True),
        min_score=max(1, _opt_int("MinScore", 2)),
        eligible_exts=_lower_set_csv(
            _opt_str(
                "EligibleExts",
                ".mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts,.srt,.ass,.ssa,.sub,.idx,.sup,.nfo",
            )
        ),
        skip_if_target_exists=_opt_bool("SkipIfTargetExists", False),
    )


def iter_targets(root: Path, plan: Plan) -> Iterable[Path]:
    if plan.rename_dirs:
        for dirpath, dirnames, _ in os.walk(root, topdown=False):
            for d in dirnames:
                yield Path(dirpath) / d
    if plan.rename_files:
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                yield Path(dirpath) / fn


def with_unique_suffix(target: Path) -> Path:
    stem, suffix = split_stem_suffix(target.name)
    for i in range(1, 1000):
        candidate = target.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return target


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
    if not plan.root or not plan.root.exists() or not plan.root.is_dir():
        log("DETAIL", "No directory to process (NZBPP_DIRECTORY missing or not a directory).")
        return 0

    changed = 0
    for p in iter_targets(plan.root, plan):
        name = p.name
        if name in {".DS_Store", "Thumbs.db"}:
            continue

        if p.is_file():
            ext = p.suffix.lower()
            if plan.eligible_exts and ext not in plan.eligible_exts:
                continue

        stem, suffix = split_stem_suffix(name)
        if not stem:
            continue

        rev_stem = stem[::-1]
        new_name = f"{rev_stem}{suffix}"

        if plan.only_if_looks_reversed:
            if not looks_reversed(
                stem,
                rev_stem,
                plan.min_score,
                plan.require_strong_id,
                plan.strong_id_allow_year,
            ):
                continue

        if rename_path(p, new_name, plan):
            changed += 1

    return changed


def main() -> int:
    if not should_run():
        return POSTPROCESS_NONE

    plan = build_plan()
    changed = process(plan)
    log("INFO", f"Reverse-name complete: renamed={changed}")
    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"Reverse-name crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)

