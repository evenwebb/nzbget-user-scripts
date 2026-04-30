#!/usr/bin/env python3
########################################
### NZBGET POST-PROCESSING SCRIPT    ###
#
# Permissions (Unraid Default)
#
# Applies Unraid-style ownership and permissions to the download directory:
# - Owner/Group: nobody:users (configurable)
# - Directories: 0775 (configurable)
# - Files:       0664 (configurable)
#
# Notes:
# - In many Docker setups the script may not have permission to chown.
#   By default, chown failures are logged but do NOT fail the NZBGet job.
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
# Dry run: log what would change but do not apply changes.
# DryRun=no
#
# Apply to which directory.
# TargetDir=directory
# - directory: uses NZBPP_DIRECTORY
# - final: uses NZBPP_FINALDIR (fallback to directory)
#
# Ownership (set empty to skip chown).
# Owner=nobody
# Group=users
#
# Permissions (octal).
# DirMode=0775
# FileMode=0664
#
# If chown fails, do not mark job as failed.
# IgnoreChownErrors=yes
#
# Follow symlinks when walking the tree.
# FollowSymlinks=no
#
##############################################################################

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

POSTPROCESS_SUCCESS = 93
POSTPROCESS_ERROR = 94
POSTPROCESS_NONE = 95

# EDIT FOR YOUR SETUP
# Script defaults used when NZBGet does not pass NZBPO_* options.
DEFAULTS = {
    "RunMode": "success-only",  # success-only | always
    "DryRun": "no",
    "TargetDir": "directory",  # directory | final
    "Owner": "nobody",
    "Group": "users",
    "DirMode": "0775",
    "FileMode": "0664",
    "IgnoreChownErrors": "yes",
    "FollowSymlinks": "no",
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


def _opt_octal(name: str, default: int) -> int:
    raw = os.environ.get(f"NZBPO_{name}", "")
    if not raw:
        # Prefer a parseable octal string (0775), not Python's "0o775".
        raw = _default(name, format(default, "04o"))
    s = raw.strip().lower()
    try:
        # Allow "775", "0775", or "0o775".
        if s.startswith("0o"):
            s = s[2:]
        if s.startswith("0"):
            return int(s, 8)
        return int("0" + s, 8)
    except ValueError:
        return default


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


@dataclass(frozen=True)
class Plan:
    root: Path
    dry_run: bool
    owner: str
    group: str
    uid: int
    gid: int
    dir_mode: int
    file_mode: int
    ignore_chown_errors: bool
    follow_symlinks: bool


def build_plan(root: Path) -> Plan:
    owner = _opt_str("Owner", "nobody").strip()
    group = _opt_str("Group", "users").strip()
    uid = _uid(owner) if owner else -1
    gid = _gid(group) if group else -1
    return Plan(
        root=root,
        dry_run=_opt_bool("DryRun", False),
        owner=owner,
        group=group,
        uid=uid,
        gid=gid,
        dir_mode=_opt_octal("DirMode", 0o775),
        file_mode=_opt_octal("FileMode", 0o664),
        ignore_chown_errors=_opt_bool("IgnoreChownErrors", True),
        follow_symlinks=_opt_bool("FollowSymlinks", False),
    )


_printed_chown_hint = False


def _docker_chown_hint() -> None:
    global _printed_chown_hint
    if _printed_chown_hint:
        return
    _printed_chown_hint = True
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log(
            "DETAIL",
            "chown failed (likely running unprivileged/in Docker). Hint: run NZBGet container as root or set PUID/PGID so the process can chown, or keep IgnoreChownErrors=yes.",
        )


def _maybe_chown(path: Path, plan: Plan, dry_run: bool, ignore_errors: bool) -> bool:
    if not plan.owner and not plan.group:
        return True
    if dry_run:
        log("DETAIL", f"[dry-run] chown {plan.owner}:{plan.group} {path}")
        return True
    try:
        os.chown(path, plan.uid, plan.gid)
        return True
    except Exception as e:
        msg = f"chown failed for {path}: {e}"
        if ignore_errors:
            log("WARNING", msg)
            _docker_chown_hint()
            return True
        log("ERROR", msg)
        return False


def _uid(owner: str) -> int:
    # Resolve user -> uid, fall back to numeric.
    if owner == "":
        return -1
    try:
        import pwd  # type: ignore

        return pwd.getpwnam(owner).pw_uid
    except Exception:
        try:
            return int(owner)
        except ValueError:
            return -1


def _gid(group: str) -> int:
    # Resolve group -> gid, fall back to numeric.
    if group == "":
        return -1
    try:
        import grp  # type: ignore

        return grp.getgrnam(group).gr_gid
    except Exception:
        try:
            return int(group)
        except ValueError:
            return -1


def _chmod(path: Path, mode: int, dry_run: bool) -> bool:
    if dry_run:
        log("DETAIL", f"[dry-run] chmod {oct(mode)} {path}")
        return True
    try:
        os.chmod(path, mode)
        return True
    except Exception as e:
        log("WARNING", f"chmod failed for {path}: {e}")
        return False


def apply_permissions(plan: Plan) -> Tuple[int, int, int]:
    changed = 0
    chmod_fail = 0
    chown_fail_hard = 0

    def maybe_fix(path: Path, is_dir: bool) -> None:
        nonlocal changed, chmod_fail, chown_fail_hard
        try:
            st = path.stat()
        except Exception:
            return

        desired_mode = plan.dir_mode if is_dir else plan.file_mode
        current_mode = st.st_mode & 0o777
        if current_mode != desired_mode:
            if _chmod(path, desired_mode, plan.dry_run):
                changed += 1
            else:
                chmod_fail += 1

        # Fast-path: only chown if different.
        need_chown = False
        if plan.uid != -1 and st.st_uid != plan.uid:
            need_chown = True
        if plan.gid != -1 and st.st_gid != plan.gid:
            need_chown = True
        if need_chown:
            if not _maybe_chown(path, plan, plan.dry_run, plan.ignore_chown_errors):
                chown_fail_hard += 1

    # Apply to root too.
    if plan.root.exists():
        maybe_fix(plan.root, plan.root.is_dir())

    for dirpath, dirnames, filenames in os.walk(plan.root, followlinks=plan.follow_symlinks):
        dpath = Path(dirpath)

        # Directories
        for d in dirnames:
            p = dpath / d
            maybe_fix(p, True)

        # Files
        for f in filenames:
            p = dpath / f
            maybe_fix(p, False)

    return changed, chmod_fail, chown_fail_hard


def main() -> int:
    if not should_run():
        return POSTPROCESS_NONE

    root = pick_target_dir()
    if not root or not root.exists():
        log("DETAIL", "No target directory found (NZBPP_DIRECTORY/NZBPP_FINALDIR missing).")
        return POSTPROCESS_NONE

    plan = build_plan(root)
    changed, chmod_fail, chown_fail_hard = apply_permissions(plan)

    log(
        "INFO",
        f"Permissions complete: changed={changed}, chmod_failures={chmod_fail}, chown_hard_failures={chown_fail_hard}",
    )

    if chmod_fail > 0 or chown_fail_hard > 0:
        # chmod failures are usually permissions-related but shouldn't break NZBGet workflow.
        # Only hard-fail if chown errors are configured to be strict (IgnoreChownErrors=no)
        if chown_fail_hard > 0:
            return POSTPROCESS_ERROR

    return POSTPROCESS_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"Permissions crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)

