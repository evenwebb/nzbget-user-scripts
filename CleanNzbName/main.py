#!/usr/bin/env python3
from __future__ import annotations

########################################
### NZBGET SCAN SCRIPT               ###
#
# Clean NZB Name
#
# Strips common indexer / obfuscation / cross-post suffixes from the NZB
# filename before the job is queued. Output uses NZBGet's scan control line:
#   [NZB] NZBNAME=...
#
### NZBGET SCAN SCRIPT               ###
########################################
#
##############################################################################
### OPTIONS                                                                ###
#
# Dry run: log the new name but do not emit [NZB] NZBNAME= (no rename).
# DryRun=no
#
# Comma-separated extra literal suffixes to strip (without ".nzb"), e.g.:
#   -MyIndexer,-FooBar
# Each becomes a case-insensitive "-MyIndexer.nzb$" style removal.
# ExtraSuffixes=
#
# Re-enable the legacy Clean.py regex that strips an optional "-????"
# (four chars) before ".nzb" after certain tokens. This can false-positive on
# legitimate release tokens; keep off unless you relied on it historically.
# LegacyD3Strip=no
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

DryRun=no
ExtraSuffixes=
LegacyD3Strip=no

### NZBGET SCRIPT CONFIGURATION
"""

import os
import re
from typing import List, Pattern

# NZBGet extension exit codes (post-process, queue, scan, feed, scheduler).
POSTPROCESS_SUCCESS = 93
POSTPROCESS_ERROR = 94


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


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


# Suffixes are stripped with case-insensitive regex (re.I). Order matters within
# each block (longer / more specific before shorter). Blocks are concatenated in
# `_merge_strip_pattern_blocks()` so cross-post compounds run before generic "-xpost".
#
# Sources: classic NZBGet Clean.py / TRaSH guides, NZBGet forums, and common
# Newznab indexer branding seen on NZB filenames (not scene release tags).


def _merge_strip_pattern_blocks() -> List[str]:
    # ----- Cross-post, “as requested”, and other compound tails -----
    crosspost: List[str] = [
        r"-AsRequested-xpost\.nzb$",
        r"\[TGx\]-xpost\.nzb$",
        r"-NinjaCentral-xpost\.nzb$",
        r"-AlternativeToRequested\.nzb$",
        r"\.mkv-xpost\.nzb$",
        r"-xpost\.nzb$",
    ]

    # ----- Bracket tags: indexer / repack site wrappers (strip before hyphen indexers) -----
    bracket_indexers: List[str] = [
        r"\[eztv([ ._-]re)?\]\.nzb$",
        r"\[TGx\]\.nzb$",
        r"\[ettv\]\.nzb$",
        r"\[nzb\.su\]\.nzb$",
        r"\[nzbsu\]\.nzb$",
        r"\[NZBGeek\]\.nzb$",
        r"\[WtFnZb\]\.nzb$",
        r"\[TorrentGalaxy\]\.nzb$",
        r"\[SceneNZBs\]\.nzb$",
        r"\[SceneNZB\]\.nzb$",
    ]

    # ----- Obfuscation, mirror, spam, and “repack site” style filler (not scene groups) -----
    obfuscation: List[str] = [
        r"-Obfuscated\.nzb$",
        r"-Obfuscation\.nzb$",
        r"-Scrambled\.nzb$",
        r"-BUYMORE\.nzb$",
        r"-CAPTCHA\.nzb$",
        r"-Chamele0n\.nzb$",
        r"-GEROV\.nzb$",
        r"-iNC0GNiTO\.nzb$",
        r"-Rakuv[a-z0-9]*\.nzb$",
        r"-RePACKPOST\.nzb$",
        r"-WhiteRev\.nzb$",
        r"-WRTEAM\.nzb$",
        r"-Z0iDS3N\.nzb$",
        r"-4Planet\.nzb$",
        r"-4P\.nzb$",
        r"-AsRequested\.nzb$",
        r"-AlteZachen\.nzb$",
        r"-postbot\.nzb$",
        r"-postbox\.nzb$",
    ]

    # ----- Hyphen suffix: indexer / NZB API brands (alphabetical by tag) -----
    indexer_hyphen: List[str] = [
        # A
        r"-abNZB\.nzb$",
        r"-AltHUB\.nzb$",
        r"-AnimeNZB\.nzb$",
        # B
        r"-Binsearch\.nzb$",
        # D
        r"-Digitalcarnage\.nzb$",
        r"-DOGnzb\.nzb$",
        r"-DrunkenSlug\.nzb$",
        # E
        r"-ExtremeNZB\.nzb$",
        # F
        r"-FastNZB\.nzb$",
        # L
        r"-lulz\.nzb$",
        # M
        r"-MiatrixRelease\.nzb$",
        r"-Miatrix\.nzb$",
        # N (NZB* / Nzb*)
        r"-NewzNZB\.nzb$",
        r"-NinjaCentral\.nzb$",
        r"-NWEB\.nzb$",
        r"-NZBCat\.nzb$",
        r"-NZBFinder\.nzb$",
        r"-NZBForyou\.nzb$",
        r"-NZBGeek\.nzb$",
        r"-NZBIndex\.nzb$",
        r"-NZBKing\.nzb$",
        r"-NZBMatrix\.nzb$",
        r"-NZBPlanet\.nzb$",
        r"-NzbPlanet\.nzb$",
        r"-NZBs\.org\.nzb$",
        r"-NZBUnity\.nzb$",
        r"-NzbNoob\.nzb$",
        r"-NzBTornado\.nzb$",
        r"-nzb\.su\.nzb$",  # hyphen+dot form of nzb.su branding
        # O
        r"-Omicron\.nzb$",
        r"-OZnzb\.nzb$",
        # P
        r"-PFMonkey\.nzb$",
        r"-Potuk\.nzb$",
        r"-PreToMe\.nzb$",
        # S
        r"-SceneNZBs\.nzb$",
        r"-SceneNZB\.nzb$",
        r"-SecretUsenet\.nzb$",
        r"-SimplyNZBs\.nzb$",
        r"-SpeedCD\.nzb$",
        r"-Spotweb\.nzb$",
        # T
        r"-TabulaRasa\.nzb$",
        r"-Tabula-Rasa\.nzb$",
        r"-TorrentGalaxy\.nzb$",
        # U
        r"-uploadgig\.nzb$",
        r"-UploadGIG\.nzb$",
        r"-Usenet-Crawler\.nzb$",
        r"-UsenetExpress\.nzb$",
        r"-UsenetFarm\.nzb$",
        # W
        r"-WtFnZb\.nzb$",
        # 6 (numeric brand)
        r"-6Box\.nzb$",
        # Console / upload host markers sometimes appended to NZB names
        r"-Console\.nzb$",
        r"-CONSOLE\.nzb$",
    ]

    return crosspost + bracket_indexers + obfuscation + indexer_hyphen


_DEFAULT_STRIP_PATTERNS: List[str] = _merge_strip_pattern_blocks()

_LEGACY_D3: Pattern[str] = re.compile(r"(?i)(-D-Z0N3|\-[^-.\n]*)(\-.{4})?\.nzb$")

_COMPILED_DEFAULT: List[Pattern[str]] = [re.compile(p, re.I) for p in _DEFAULT_STRIP_PATTERNS]


def _extra_patterns(csv: str) -> List[Pattern[str]]:
    out: List[Pattern[str]] = []
    for piece in _split_csv(csv):
        if not piece.startswith("-"):
            piece = f"-{piece}"
        escaped = re.escape(piece) + r"\.nzb$"
        out.append(re.compile(escaped, re.I))
    return out


def _strip_once(name: str, patterns: List[Pattern[str]]) -> str:
    for pat in patterns:
        name = pat.sub(".nzb", name)
    return name


def clean_nzb_name(
    name: str,
    *,
    extra_suffixes_csv: str,
    legacy_d3: bool,
    max_passes: int = 24,
) -> str:
    patterns: List[Pattern[str]] = list(_COMPILED_DEFAULT)
    patterns.extend(_extra_patterns(extra_suffixes_csv))

    prev = None
    current = name
    for _ in range(max_passes):
        prev = current
        current = _strip_once(current, patterns)
        if legacy_d3:
            current = _LEGACY_D3.sub(r"\1.nzb", current)
        if current == prev:
            break

    return current


def _basename_non_empty(name: str) -> bool:
    if not name or not name.lower().endswith(".nzb"):
        return False
    stem = name[:-4].strip()
    return len(stem) > 0


def _scan_target_basename() -> str:
    """NZBNP_NZBNAME is preferred; NZBNP_FILENAME is a documented fallback."""
    n = os.environ.get("NZBNP_NZBNAME", "").strip()
    if n:
        return n
    return os.environ.get("NZBNP_FILENAME", "").strip()


def _running_under_nzbget_scan() -> bool:
    # Scan scripts receive NZBNP_DIRECTORY; NZBOP_SCRIPTDIR is set for extensions broadly.
    return bool(os.environ.get("NZBNP_DIRECTORY") or os.environ.get("NZBOP_SCRIPTDIR"))


def main() -> int:
    if not _running_under_nzbget_scan():
        log("ERROR", "Not running as NZBGet scan extension (missing NZBNP_DIRECTORY / NZBOP_SCRIPTDIR).")
        return POSTPROCESS_ERROR

    raw = _scan_target_basename()
    if not raw:
        log("ERROR", "NZBNP_NZBNAME and NZBNP_FILENAME missing or empty.")
        return POSTPROCESS_ERROR

    # NZBGet may invoke scan extensions for any file in NzbDir (e.g. zips); only rewrite .nzb names.
    if not raw.lower().endswith(".nzb"):
        log("DETAIL", f"Skipping non-NZB scan target: {raw!r}")
        return POSTPROCESS_SUCCESS

    dry_run = _opt_bool("DryRun", False)
    legacy = _opt_bool("LegacyD3Strip", False)
    extras = _opt_str("ExtraSuffixes", "")

    cleaned = clean_nzb_name(raw, extra_suffixes_csv=extras, legacy_d3=legacy)

    if not _basename_non_empty(cleaned):
        log("WARNING", "Cleaning would produce an invalid NZB name; leaving unchanged.")
        return POSTPROCESS_SUCCESS

    if cleaned != raw:
        log("INFO", f"NZB name: {raw!r} -> {cleaned!r}")
        if not dry_run:
            # NZBGet scan control command (no space after '=' per convention).
            print(f"[NZB] NZBNAME={cleaned}", flush=True)
    else:
        log("DETAIL", "NZB name unchanged.")

    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"CleanNzbName crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)
