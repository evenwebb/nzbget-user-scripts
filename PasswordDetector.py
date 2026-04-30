#!/usr/bin/env python3
########################################
### NZBGET QUEUE SCRIPT              ###
#
# PasswordDetector
#
# Detects password-protected/encrypted RAR archives early during downloading.
# Runs on FILE_DOWNLOADED events and scans the NZB's destination directory for
# .rar volumes. If encryption is detected, it can cancel the NZB immediately.
#
# This saves bandwidth and time compared to waiting for post-processing to fail.
#
### NZBGET QUEUE SCRIPT              ###
########################################
#
##############################################################################
### OPTIONS                                                                ###
#
# Which events to process (comma-separated).
# Events=FILE_DOWNLOADED
#
# Action when password protection is detected:
# - mark-bad: cancel download and mark as BAD (recommended)
# - pause: pause the NZB (requires NZBGet RPC access)
# - none: only log detection (no action)
# Action=mark-bad
#
# Dry run: log what would happen but do not act.
# DryRun=no
#
# Max RAR files to inspect per run.
# MaxRarFiles=5
#
# Max seconds to spend per RAR check command.
# CommandTimeoutSec=8
#
# Cache result in the directory to avoid repeated work.
# UseCache=yes
# CacheFilename=.nzbget_passworddetector.json
#
# External tool preference:
# - auto: try unrar then 7z
# - unrar: only unrar
# - 7z: only 7z
# Tool=auto
#
# If true, treat "encrypted headers" as password-protected too.
# CountEncryptedHeaders=yes
#
##############################################################################

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xmlrpc.client import ServerProxy


def log(kind: str, msg: str) -> None:
    print(f"[{kind}] {msg}")

# EDIT FOR YOUR SETUP
# Script defaults used when NZBGet does not pass NZBPO_* options.
DEFAULTS = {
    "Events": "FILE_DOWNLOADED",
    "Action": "mark-bad",  # mark-bad | pause | none
    "DryRun": "no",
    "MaxRarFiles": "5",
    "CommandTimeoutSec": "8",
    "UseCache": "yes",
    "CacheFilename": ".nzbget_passworddetector.json",
    "Tool": "auto",  # auto | unrar | 7z
    "CountEncryptedHeaders": "yes",
}
# END EDIT FOR YOUR SETUP


def _default(name: str, fallback: str) -> str:
    return str(DEFAULTS.get(name, fallback))


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


def _opt_int(name: str, default: int) -> int:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if not raw:
        raw = _default(name, str(default))
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def should_process_event() -> bool:
    event = os.environ.get("NZBNA_EVENT", "")
    allowed = {e.upper() for e in _split_csv(_opt_str("Events", "FILE_DOWNLOADED"))}
    return event.upper() in allowed


def nzb_dir() -> Optional[Path]:
    d = os.environ.get("NZBNA_DIRECTORY", "")
    return Path(d) if d else None


def cache_path(root: Path) -> Path:
    return root / _opt_str("CacheFilename", ".nzbget_passworddetector.json")


def read_cache(root: Path) -> Dict[str, object]:
    if not _opt_bool("UseCache", True):
        return {}
    p = cache_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_cache(root: Path, payload: Dict[str, object]) -> None:
    if not _opt_bool("UseCache", True):
        return
    p = cache_path(root)
    try:
        p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def iter_rar_files(root: Path, max_files: int) -> List[Path]:
    """
    Find up to max_files RAR volumes to test, preferring likely "first" volumes.
    Uses an early-stopping os.walk scan to avoid expensive rglob on large trees.
    """
    max_files = max(1, max_files)

    part01: List[Path] = []
    plain_rar: List[Path] = []
    r00: List[Path] = []
    other: List[Path] = []

    part01_re = re.compile(r"\.part0*1\.rar$", re.IGNORECASE)
    rxx_re = re.compile(r"\.r\d\d$", re.IGNORECASE)

    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            low = fn.lower()
            if not (low.endswith(".rar") or rxx_re.search(low)):
                continue
            p = Path(dirpath) / fn

            if part01_re.search(low):
                part01.append(p)
            elif low.endswith(".rar"):
                plain_rar.append(p)
            elif low.endswith(".r00"):
                r00.append(p)
            else:
                other.append(p)

            if len(part01) >= max_files:
                break
        if len(part01) >= max_files:
            break

        # Early stop if we have enough in combined top tiers.
        if len(part01) + len(plain_rar) >= max_files:
            break

    out: List[Path] = []
    for bucket in (part01, plain_rar, r00, other):
        for p in bucket:
            out.append(p)
            if len(out) >= max_files:
                return out
    return out


def which(cmd: str) -> Optional[str]:
    for path in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(path) / cmd
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return None


def run_cmd(args: List[str], timeout_sec: int) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_sec,
        )
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = ""
        try:
            out = (e.stdout or "") + (e.stderr or "")
        except Exception:
            pass
        return 124, out
    except Exception as e:
        return 127, str(e)


def is_password_protected_unrar(rar: Path, timeout_sec: int, count_encrypted_headers: bool) -> Tuple[bool, str]:
    # -p-: do not prompt, use empty password
    # "unrar t" tests extraction; "unrar l" lists; both can report encryption.
    # Use "l" first (faster); if inconclusive, fall back to "t".
    cmds = [
        ["unrar", "l", "-p-", "-idq", str(rar)],
        ["unrar", "t", "-p-", "-idq", str(rar)],
    ]

    combined = ""
    for c in cmds:
        code, out = run_cmd(c, timeout_sec)
        combined += out + "\n"
        low = out.lower()

        if "enter password" in low or "wrong password" in low:
            return True, out.strip()
        if "encrypted file" in low or "encrypted" in low:
            if count_encrypted_headers:
                return True, out.strip()
        # Some unrar builds say "Cannot open encrypted archive" etc.
        if "password" in low and ("needed" in low or "required" in low):
            return True, out.strip()

        # If list/test succeeded quickly, stop.
        if code == 0 and out:
            break

    # Extra heuristic: "headers are encrypted" / "encrypted headers"
    low = combined.lower()
    if count_encrypted_headers and ("encrypted headers" in low or "headers are encrypted" in low):
        return True, combined.strip()

    return False, combined.strip()


def is_password_protected_7z(rar: Path, timeout_sec: int, count_encrypted_headers: bool) -> Tuple[bool, str]:
    # 7z list with empty password:
    # -p: set password (empty)
    # If archive is encrypted, output includes "Enter password" or "Can not open encrypted archive".
    args = ["7z", "l", "-p", str(rar)]
    code, out = run_cmd(args, timeout_sec)
    low = out.lower()

    if "enter password" in low or "wrong password" in low:
        return True, out.strip()
    if "encrypted" in low and count_encrypted_headers:
        return True, out.strip()
    if "can not open encrypted archive" in low:
        return True, out.strip()

    # If 7z returns nonzero and mentions password, treat as protected.
    if code != 0 and "password" in low:
        return True, out.strip()

    return False, out.strip()


def detect_password(rars: List[Path]) -> Tuple[bool, str, Optional[Path]]:
    tool_pref = _opt_str("Tool", "auto").strip().lower()
    timeout_sec = max(1, _opt_int("CommandTimeoutSec", 8))
    count_encrypted_headers = _opt_bool("CountEncryptedHeaders", True)

    have_unrar = which("unrar") is not None
    have_7z = which("7z") is not None

    for rar in rars:
        if tool_pref in {"auto", "unrar"} and have_unrar:
            ok, evidence = is_password_protected_unrar(rar, timeout_sec, count_encrypted_headers)
            if ok:
                return True, evidence, rar
        if tool_pref in {"auto", "7z"} and have_7z:
            ok, evidence = is_password_protected_7z(rar, timeout_sec, count_encrypted_headers)
            if ok:
                return True, evidence, rar

    if tool_pref in {"auto", "unrar"} and not have_unrar and tool_pref != "7z":
        log("DETAIL", "unrar not found in PATH")
    if tool_pref in {"auto", "7z"} and not have_7z and tool_pref != "unrar":
        log("DETAIL", "7z not found in PATH")

    return False, "", None


def mark_bad() -> None:
    # Supported queue control command.
    print("[NZB] MARK=BAD")


def _rpc_server() -> Optional[ServerProxy]:
    try:
        host = os.environ.get("NZBOP_CONTROLIP", "")
        port = os.environ.get("NZBOP_CONTROLPORT", "")
        username = os.environ.get("NZBOP_CONTROLUSERNAME", "")
        password = os.environ.get("NZBOP_CONTROLPASSWORD", "")
        if not host or not port:
            return None
        if host == "0.0.0.0":
            host = "127.0.0.1"
        url = f"http://{username}:{password}@{host}:{port}/xmlrpc"
        return ServerProxy(url)
    except Exception:
        return None


def pause_nzb(nzb_id: str) -> bool:
    try:
        if not nzb_id:
            return False
        server = _rpc_server()
        if server is None:
            return False
        # editqueue GroupPause expects NZBID(s) as integers.
        ok = server.editqueue("GroupPause", "", [int(nzb_id)])
        return bool(ok)
    except Exception:
        return False


def main() -> int:
    # Only run on configured events.
    if not should_process_event():
        return 0

    root = nzb_dir()
    if not root or not root.exists():
        return 0

    nzb_name = os.environ.get("NZBNA_NZBNAME", "") or os.environ.get("NZBNA_FILENAME", "") or "unknown"
    nzb_id = os.environ.get("NZBNA_NZBID", "")

    cache = read_cache(root)
    if not cache.get("tool_diag_done"):
        have_unrar = which("unrar") is not None
        have_7z = which("7z") is not None
        tool_pref = _opt_str("Tool", "auto").strip().lower()
        chosen = "unrar" if (tool_pref in {"auto", "unrar"} and have_unrar) else ("7z" if have_7z else "none")
        log("DETAIL", f"Tools: unrar={'yes' if have_unrar else 'no'}, 7z={'yes' if have_7z else 'no'}, Tool={tool_pref}, chosen={chosen}")
        cache["tool_diag_done"] = True
        write_cache(root, cache)
    if cache.get("status") in {"password", "clear"}:
        return 0

    max_rars = max(1, _opt_int("MaxRarFiles", 5))
    rars = iter_rar_files(root, max_rars)
    if not rars:
        return 0

    found, evidence, rar_path = detect_password(rars)
    if found:
        msg = f"Password-protected RAR detected for '{nzb_name}'"
        if rar_path:
            msg += f" (file: {rar_path.name})"
        log("WARNING", msg)
        if evidence:
            log("DETAIL", evidence[:2000])

        write_cache(
            root,
            {
                "status": "password",
                "nzb": nzb_name,
                "nzb_id": nzb_id,
                "rar": str(rar_path) if rar_path else "",
                "timestamp": int(time.time()),
            },
        )

        action = _opt_str("Action", "mark-bad").strip().lower()
        dry_run = _opt_bool("DryRun", False)

        if action == "none":
            return 0
        if action == "pause":
            if dry_run:
                log("INFO", "[dry-run] Would pause NZB via RPC (GroupPause)")
                return 0
            if pause_nzb(nzb_id):
                log("INFO", "Paused NZB via RPC (GroupPause).")
                return 0
            log("WARNING", "Failed to pause NZB via RPC; falling back to MARK=BAD.")
            mark_bad()
            return 0
        if dry_run:
            log("INFO", "[dry-run] Would mark NZB as BAD")
            return 0

        mark_bad()
        return 0

    # If we scanned and didn't find a password, remember that too (to avoid repeating).
    write_cache(
        root,
        {
            "status": "clear",
            "nzb": nzb_name,
            "nzb_id": nzb_id,
            "timestamp": int(time.time()),
        },
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"PasswordDetector crashed: {e}")
        raise SystemExit(0)

