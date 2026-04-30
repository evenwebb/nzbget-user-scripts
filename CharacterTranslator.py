#!/usr/bin/env python3
########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# CharacterTranslator (Filename Encoding Fixer)
#
# Fixes common filename encoding / mojibake issues (e.g. "Ã©" -> "é"),
# normalizes Unicode (NFC/NFKC), and optionally sanitizes filesystem-unsafe
# characters for better Sonarr/Radarr matching.
#
# Notes:
# - This script does NOT "translate Chinese to English" (that would require
#   external libraries/dictionaries). It can, however, normalize and clean names
#   and optionally transliterate to ASCII (best-effort, lossy).
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
# RenameDirs=yes
#
# Apply to which directory.
# TargetDir=directory
# - directory: uses NZBPP_DIRECTORY
# - final: uses NZBPP_FINALDIR (fallback to directory)
#
# Unicode normalization form: NFC, NFKC, NFD, NFKD, or none.
# Normalization=NFC
#
# Attempt to fix UTF-8-as-Latin1 / CP1252 / CP437 mojibake.
# FixMojibake=yes
#
# Replace filesystem-unsafe characters (Windows-unsafe and control chars).
# Sanitize=yes
#
# Replacement character for sanitized characters.
# SanitizeReplacement=_
#
# Collapse repeated spaces/underscores/dots after sanitation.
# CollapseRepeats=yes
#
# Optional: transliterate to ASCII (lossy). Useful if you want to remove
# non-Latin scripts. Off by default.
# AsciiOnly=no
#
# If a target name exists, skip rename (otherwise we add numeric suffix).
# SkipIfTargetExists=no
#
##############################################################################

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

POSTPROCESS_SUCCESS = 93
POSTPROCESS_ERROR = 94
POSTPROCESS_NONE = 95

# EDIT FOR YOUR SETUP
# Script defaults used when NZBGet does not pass NZBPO_* options.
DEFAULTS = {
    "RunMode": "success-only",  # success-only | always
    "DryRun": "no",
    "RenameFiles": "yes",
    "RenameDirs": "yes",
    "TargetDir": "directory",  # directory | final
    "Normalization": "NFC",  # NFC | NFKC | NFD | NFKD | none
    "FixMojibake": "yes",
    "Sanitize": "yes",
    "SanitizeReplacement": "_",
    "CollapseRepeats": "yes",
    "AsciiOnly": "no",
    "SkipIfTargetExists": "no",
}
# END EDIT FOR YOUR SETUP


def _default(name: str, fallback: str) -> str:
    return str(DEFAULTS.get(name, fallback))


def log(kind: str, message: str) -> None:
    print(f"[{kind}] {message}")


def _opt_str(name: str, default: str) -> str:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if raw != "":
        return raw
    return _default(name, default)


def _opt_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if not raw:
        raw = _default(name, "yes" if default else "no")
    return raw.strip().lower() in {"yes", "true", "1", "on"}


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


def _text_quality(s: str) -> float:
    """
    Heuristic: prefer strings with fewer replacement chars and more letters/digits.
    """
    if not s:
        return 0.0
    bad = s.count("�")
    letters_digits = sum(1 for ch in s if ch.isalnum())
    return (letters_digits + 1) / (len(s) + 1) - bad * 0.5


def fix_mojibake_once(s: str) -> str:
    """
    Attempts common repairs where UTF-8 bytes were decoded as latin1/cp1252/cp437.
    Only applies a fix if it improves quality.
    """
    best = s
    best_q = _text_quality(s)

    for enc in ("latin-1", "cp1252", "cp437"):
        try:
            raw = s.encode(enc, errors="strict")
            cand = raw.decode("utf-8", errors="strict")
        except Exception:
            continue

        q = _text_quality(cand)
        if q > best_q + 0.02:
            best, best_q = cand, q

    return best


_UNSAFE_CHARS_RE = re.compile(r"[<>:\"/\\\\|?*]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_name(name: str, replacement: str) -> str:
    # Replace control and Windows-unsafe characters.
    out = _CONTROL_RE.sub(replacement, name)
    out = _UNSAFE_CHARS_RE.sub(replacement, out)

    # Strip trailing spaces/dots (Windows friendliness).
    out = out.rstrip(" .")

    # Avoid empty names.
    return out if out else replacement


def collapse_repeats(name: str) -> str:
    # Collapse repeated separators introduced by sanitation/normalization.
    name = re.sub(r"[ \t]+", " ", name)
    name = re.sub(r"_+", "_", name)
    name = re.sub(r"\.+", ".", name)
    # IMPORTANT: do not collapse across dots, otherwise we can destroy extensions.
    name = re.sub(r"[ _-]{2,}", "_", name)
    return name.strip()


def ascii_only(s: str) -> str:
    # Best-effort transliteration by stripping diacritics and dropping non-ascii.
    n = unicodedata.normalize("NFKD", s)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    return n.encode("ascii", errors="ignore").decode("ascii", errors="ignore")


def split_stem_suffix(filename: str) -> Tuple[str, str]:
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


@dataclass(frozen=True)
class Plan:
    root: Path
    dry_run: bool
    rename_files: bool
    rename_dirs: bool
    normalization: str
    fix_mojibake: bool
    sanitize: bool
    sanitize_replacement: str
    collapse_repeats: bool
    ascii_only: bool
    skip_if_target_exists: bool


def build_plan(root: Path) -> Plan:
    norm = _opt_str("Normalization", "NFC").strip().upper()
    if norm not in {"NFC", "NFKC", "NFD", "NFKD", "NONE"}:
        norm = "NFC"

    repl = _opt_str("SanitizeReplacement", "_")
    if repl == "":
        repl = "_"

    return Plan(
        root=root,
        dry_run=_opt_bool("DryRun", False),
        rename_files=_opt_bool("RenameFiles", True),
        rename_dirs=_opt_bool("RenameDirs", True),
        normalization=norm,
        fix_mojibake=_opt_bool("FixMojibake", True),
        sanitize=_opt_bool("Sanitize", True),
        sanitize_replacement=repl,
        collapse_repeats=_opt_bool("CollapseRepeats", True),
        ascii_only=_opt_bool("AsciiOnly", False),
        skip_if_target_exists=_opt_bool("SkipIfTargetExists", False),
    )


def iter_paths(root: Path, plan: Plan) -> Iterable[Path]:
    # Use os.walk to avoid multiple rglob passes on large trees.
    # Dirs bottom-up first (so children are renamed before parents), then files.
    if plan.rename_dirs:
        for dirpath, dirnames, _ in os.walk(root, topdown=False):
            for d in dirnames:
                yield Path(dirpath) / d
    if plan.rename_files:
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                yield Path(dirpath) / fn


def transform_component(name: str, plan: Plan) -> str:
    # Preserve file extensions by transforming stem and extension separately.
    stem, suffix = split_stem_suffix(name)

    out_stem = stem
    out_suffix = suffix

    if plan.fix_mojibake:
        once = fix_mojibake_once(out_stem)
        out_stem = fix_mojibake_once(once) if once != out_stem else once

        if out_suffix:
            once_s = fix_mojibake_once(out_suffix)
            out_suffix = fix_mojibake_once(once_s) if once_s != out_suffix else once_s

    if plan.normalization != "NONE":
        out_stem = unicodedata.normalize(plan.normalization, out_stem)
        if out_suffix:
            out_suffix = unicodedata.normalize(plan.normalization, out_suffix)

    if plan.ascii_only:
        out_stem = ascii_only(out_stem)
        if out_suffix:
            out_suffix = ascii_only(out_suffix)

    if plan.sanitize:
        out_stem = sanitize_name(out_stem, plan.sanitize_replacement)
        if out_suffix:
            dot = "." if out_suffix.startswith(".") else ""
            ext = out_suffix[1:] if dot else out_suffix
            ext = sanitize_name(ext, plan.sanitize_replacement)
            out_suffix = f"{dot}{ext}" if ext else ""

    if plan.collapse_repeats:
        out_stem = collapse_repeats(out_stem)

    if not out_stem:
        out_stem = plan.sanitize_replacement

    return f"{out_stem}{out_suffix}"


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


def process(root: Path, plan: Plan) -> int:
    if not root.exists() or not root.is_dir():
        log("DETAIL", "No directory to process.")
        return 0

    changed = 0
    for p in iter_paths(root, plan):
        if p.name in {".DS_Store", "Thumbs.db"}:
            continue

        new = transform_component(p.name, plan)
        if new and new != p.name:
            if rename_path(p, new, plan):
                changed += 1

    return changed


def main() -> int:
    if not should_run():
        return POSTPROCESS_NONE

    root = pick_target_dir()
    if not root:
        log("DETAIL", "No target directory found (NZBPP_DIRECTORY/NZBPP_FINALDIR missing).")
        return POSTPROCESS_NONE

    plan = build_plan(root)
    changed = process(root, plan)
    log("INFO", f"CharacterTranslator complete: renamed={changed}")
    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"CharacterTranslator crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)

