#!/usr/bin/env python3
########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Failed Download Classifier
#
# Classifies common failure patterns (DMCA/missing articles/password/bad archive/etc.)
# using NZBGet status variables and (optionally) error text found in the download folder.
#
# Output:
# - Prints a single high-signal summary line to NZBGet log.
# - Writes a small JSON + text file into the download directory for later inspection.
#
# Install:
# - Put this file in NZBGet's ScriptDir and make it executable.
# - Add it to category "Extensions" (or global Extensions) and place it late in ScriptOrder.
#
### NZBGET POST-PROCESSING SCRIPT    ###
########################################
#
##############################################################################
### OPTIONS                                                                ###
#
# Where to store artifacts (download, final, both).
#   download: use NZBPP_DIRECTORY
#   final:    use NZBPP_FINALDIR (if set, else falls back to directory)
#   both:     write to both locations (when different)
# ArtifactDir=download
#
# Also create a one-line marker file named "FAILURE_<class>.txt".
# CreateMarkerFile=yes
#
# Maximum number of bytes to read from any single candidate log/text file.
# MaxBytesPerFile=262144
#
# Maximum number of files to scan in the directory tree.
# MaxFiles=40
#
# Relative subdirs to prefer scanning (comma-separated). Leave empty to scan root only.
# PreferSubdirs=_unpack,unpack,logs,log
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

ArtifactDir=download
CreateMarkerFile=yes
MaxBytesPerFile=262144
MaxFiles=40
PreferSubdirs=_unpack,unpack,logs,log

### NZBGET SCRIPT CONFIGURATION
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

POSTPROCESS_SUCCESS = 93
POSTPROCESS_ERROR = 94
POSTPROCESS_NONE = 95

# Precompiled patterns (performance + avoids re-compilation per run).
_PASSWORD_PATTERNS = [
    re.compile(p)
    for p in [
        r"wrong password",
        r"password (is )?incorrect",
        r"encrypted archive",
        r"enter password",
        r"password required",
        r"checksum error in (the )?encrypted file",
        r"rar5.*wrong password",
    ]
]

_DMCA_PATTERNS = [
    re.compile(p)
    for p in [
        r"\bdmca\b",
        r"copyright",
        r"takedown",
        r"removed (by|due to)",
        r"cancel(?:led|ed) by (?:author|poster|user)",
    ]
]

_MISSING_ARTICLE_PATTERNS = [
    re.compile(p)
    for p in [
        r"missing articles",
        r"not enough articles",
        r"could not (find|fetch) article",
        r"430 .*no such article",
        r"article.*not found",
        r"download.*incomplete",
        r"server failure.*430",
    ]
]

_ARCHIVE_PATTERNS = [
    re.compile(p)
    for p in [
        r"crc failed",
        r"checksum error",
        r"unexpected end of archive",
        r"corrupt",
        r"bad archive",
        r"cannot open (the )?file as archive",
        r"invalid archive",
        r"header error",
        r"data error",
    ]
]

_SPACE_PATTERNS = [
    re.compile(p)
    for p in [
        r"no space left on device",
        r"disk full",
        r"write error",
        r"cannot write",
        r"error writing",
        r"not enough space",
        r"warning/space",
    ]
]


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


def _opt_str(name: str, default: str) -> str:
    raw = os.environ.get(f"NZBPO_{name}", "")
    return raw if raw != "" else default


def log(kind: str, message: str) -> None:
    # kind: DETAIL|INFO|WARNING|ERROR
    print(f"[{kind}] {message}")


@dataclass(frozen=True)
class Classification:
    failure_class: str
    retry_recommended: bool
    confidence: str  # low|medium|high
    evidence: List[str]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _env_get(*names: str) -> List[str]:
    out: List[str] = []
    for n in names:
        v = os.environ.get(n, "")
        if v:
            out.append(v)
    return out


def _pick_artifact_dirs() -> List[Path]:
    directory = os.environ.get("NZBPP_DIRECTORY", "")
    finaldir = os.environ.get("NZBPP_FINALDIR", "")

    base_dir = Path(directory) if directory else None
    base_final = Path(finaldir) if finaldir else None

    mode = _opt_str("ArtifactDir", "download").strip().lower()
    if mode not in {"download", "final", "both"}:
        mode = "download"

    if mode == "download":
        return [p for p in [base_dir] if p]
    if mode == "final":
        return [p for p in [base_final or base_dir] if p]

    # both
    dirs: List[Path] = []
    for p in [base_dir, base_final]:
        if not p:
            continue
        if p not in dirs:
            dirs.append(p)
    return dirs


def _iter_candidate_files(root: Path, prefer_subdirs: List[str], max_files: int) -> Iterable[Path]:
    """
    Yield up to max_files likely-text files without sorting entire directory trees.
    Sorting + rglob on huge download directories can be expensive; this stops early.
    """
    seen: set[Path] = set()
    count = 0

    def is_text_candidate(p: Path) -> bool:
        if p.suffix.lower() in {".log", ".txt", ".nfo", ".out"}:
            return True
        return p.name.lower() in {"unrar.log", "par2.log", "7z.log"}

    def consider(p: Path) -> bool:
        nonlocal count
        if count >= max_files:
            return False
        if p in seen:
            return False
        if not p.is_file():
            return False
        if not is_text_candidate(p):
            return False
        seen.add(p)
        count += 1
        return True

    def walk_dir(d: Path) -> Iterator[Path]:
        # Walk depth-first but stop early.
        for dirpath, _, filenames in os.walk(d):
            for fn in filenames:
                p = Path(dirpath) / fn
                if consider(p):
                    yield p
                if count >= max_files:
                    return

    # Preferred subdirs first.
    for sub in prefer_subdirs:
        if not sub or count >= max_files:
            break
        d = root / sub
        if d.exists() and d.is_dir():
            yield from walk_dir(d)

    # Root-level candidates.
    if count < max_files:
        try:
            for p in root.iterdir():
                if consider(p):
                    yield p
                if count >= max_files:
                    return
        except OSError:
            return


def _read_text_snippets(files: Iterable[Path], max_bytes_per_file: int) -> List[str]:
    chunks: List[str] = []
    for f in files:
        try:
            with f.open("rb") as fh:
                data = fh.read(max_bytes_per_file)
            # Keep as UTF-8-ish text; replace errors.
            chunks.append(data.decode("utf-8", errors="replace"))
        except OSError:
            continue
    return chunks


def classify_failure(total_status: str, status: str, text_blobs: List[str], env_evidence: List[str]) -> Classification:
    # Combine evidence sources into a single normalized string.
    blobs = [*env_evidence, *text_blobs]
    norm = "\n".join(_normalize(b) for b in blobs if b)

    status_norm = _normalize(status)

    # 1) Password/encrypted archives
    if "warning/password" in status_norm or any(p.search(norm) for p in _PASSWORD_PATTERNS):
        return Classification(
            failure_class="password",
            retry_recommended=False,
            confidence="high" if "warning/password" in status_norm else "medium",
            evidence=["Detected password/encryption indicators."],
        )

    # 2) Missing articles / DMCA-like symptoms
    if "failure/health" in status_norm or "failure/par" in status_norm:
        if any(p.search(norm) for p in _MISSING_ARTICLE_PATTERNS):
            # Attempt to separate DMCA/takedown from generic missing-articles when hints exist.
            if any(p.search(norm) for p in _DMCA_PATTERNS):
                return Classification(
                    failure_class="dmca",
                    retry_recommended=True,
                    confidence="medium",
                    evidence=["Par/health failure with DMCA/takedown indicators."],
                )
            return Classification(
                failure_class="missing_articles",
                retry_recommended=True,
                confidence="medium",
                evidence=["Par/health failure with missing-article indicators."],
            )

    # 3) Bad/corrupt archive (CRC, unexpected EOF, etc.)
    if "failure/unpack" in status_norm or "failure/par" in status_norm:
        if any(p.search(norm) for p in _ARCHIVE_PATTERNS):
            return Classification(
                failure_class="bad_archive",
                retry_recommended=True,
                confidence="medium",
                evidence=["Unpack/par failure with archive-corruption indicators."],
            )

    # 4) Disk space / write error
    if "warning/space" in status_norm or any(p.search(norm) for p in _SPACE_PATTERNS):
        return Classification(
            failure_class="disk_space",
            retry_recommended=True,
            confidence="high" if "warning/space" in status_norm else "medium",
            evidence=["Detected disk space / write error indicators."],
        )

    # 5) Repairable but repair disabled/manual
    if "warning/repairable" in status_norm:
        return Classification(
            failure_class="repairable",
            retry_recommended=True,
            confidence="high",
            evidence=["NZBGet marked as WARNING/REPAIRABLE."],
        )

    # 6) Generic PAR failure (no stronger signal)
    if "failure/par" in status_norm:
        return Classification(
            failure_class="par_failure",
            retry_recommended=True,
            confidence="low",
            evidence=["NZBGet marked as FAILURE/PAR."],
        )

    # 7) Generic unpack failure
    if "failure/unpack" in status_norm:
        return Classification(
            failure_class="unpack_failure",
            retry_recommended=True,
            confidence="low",
            evidence=["NZBGet marked as FAILURE/UNPACK."],
        )

    # 8) Script failure (prior scripts)
    if "warning/script" in status_norm:
        return Classification(
            failure_class="script_failure",
            retry_recommended=True,
            confidence="low",
            evidence=["NZBGet marked as WARNING/SCRIPT."],
        )

    # Fallback: unknown
    return Classification(
        failure_class="unknown",
        retry_recommended=True,
        confidence="low",
        evidence=[f"Unrecognized failure. NZBPP_TOTALSTATUS={total_status} NZBPP_STATUS={status}"],
    )


def write_artifacts(dirs: List[Path], nzb_name: str, cls: Classification) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "nzb": nzb_name,
        "failure_class": cls.failure_class,
        "retry_recommended": cls.retry_recommended,
        "confidence": cls.confidence,
        "evidence": cls.evidence,
        "timestamp_utc": now,
        "nzbpp_status": os.environ.get("NZBPP_STATUS", ""),
        "nzbpp_totalstatus": os.environ.get("NZBPP_TOTALSTATUS", ""),
    }

    create_marker = _opt_bool("CreateMarkerFile", True)

    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue

        try:
            (d / ".nzbget_failure_classification.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (d / ".nzbget_failure_classification.txt").write_text(
                f"{cls.failure_class} | retry={str(cls.retry_recommended).lower()} | confidence={cls.confidence}\n",
                encoding="utf-8",
            )
            if create_marker:
                marker = d / f"FAILURE_{cls.failure_class}.txt"
                if not marker.exists():
                    marker.write_text("Created by NZBGet Failed Download Classifier.\n", encoding="utf-8")
        except OSError:
            continue


def main() -> int:
    total_status = os.environ.get("NZBPP_TOTALSTATUS", "")
    status = os.environ.get("NZBPP_STATUS", "")
    nzb_name = os.environ.get("NZBPP_NZBNAME", "") or os.environ.get("NZBPP_NAME", "") or "unknown"

    if total_status == "SUCCESS":
        return POSTPROCESS_NONE

    # Collect "structured" evidence first.
    env_evidence = _env_get(
        "NZBPP_STATUS",
        "NZBPP_TOTALSTATUS",
        "NZBPP_PARSTATUS",
        "NZBPP_UNPACKSTATUS",
        "NZBPP_PARERROR",
        "NZBPP_UNPACKERROR",
        "NZBPP_HEALTH",
        "NZBPP_HEALTHCRITICAL",
    )

    directory = os.environ.get("NZBPP_DIRECTORY", "")
    prefer_subdirs = [s.strip() for s in _opt_str("PreferSubdirs", "_unpack,unpack,logs,log").split(",") if s.strip()]
    max_files = _opt_int("MaxFiles", 40)
    max_bytes_per_file = _opt_int("MaxBytesPerFile", 262144)

    text_blobs: List[str] = []
    if directory:
        root = Path(directory)
        if root.exists() and root.is_dir():
            files = list(_iter_candidate_files(root, prefer_subdirs, max_files))
            text_blobs = _read_text_snippets(files, max_bytes_per_file)

    cls = classify_failure(total_status, status, text_blobs, env_evidence)

    # Single high-signal log line.
    log(
        "WARNING" if total_status != "SUCCESS" else "INFO",
        f"Failure classified: {cls.failure_class} (retry={str(cls.retry_recommended).lower()}, confidence={cls.confidence})",
    )

    # Persist artifacts for humans/other automation.
    dirs = _pick_artifact_dirs()
    if dirs:
        write_artifacts(dirs, nzb_name, cls)

    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"Classifier crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)

