#!/usr/bin/env python3
from __future__ import annotations

########################################
### NZBGET SCHEDULER SCRIPT          ###
#
# NZBGet Health Monitor
#
# Monitors NZBGet server health metrics via the JSON-RPC API and alerts
# on anomalies: stalled downloads, high failure rate, queue growth, low
# disk space, and paused/stopped server state.
#
# Designed to run on a schedule (e.g. every 5-15 minutes via NZBGet's
# TaskX scheduler directive). State is tracked between runs via a JSON
# file so transient blips don't trigger false alerts.
#
### NZBGET SCHEDULER SCRIPT          ###
########################################
#
##############################################################################
### OPTIONS                                                                ###
#
# NZBGet JSON-RPC URL (including scheme and port).
# NzbGetUrl=http://127.0.0.1:6789
#
# Control username (ControlUsername in nzbget.conf).
# NzbGetUsername=nzbget
#
# Control password (ControlPassword in nzbget.conf).
# NzbGetPassword=
#
# Minimum download speed in MB/s below which an alert is triggered
# (must remain below this for AlertConsecutiveChecks consecutive runs).
# MinDownloadSpeedMBs=0
#
# Failure rate threshold as a fraction (0.0-1.0). If the fraction of
# recent history items marked FAILURE exceeds this, an alert fires.
# MaxFailureRate=0.30
#
# Number of recent history items to check for failure rate.
# FailureHistoryCount=50
#
# Warn when queue item count exceeds this (0 = no alert).
# MaxQueueSize=0
#
# Warn when free disk space on the download path drops below this GB.
# MinFreeDiskGB=5
#
# Number of consecutive checks before an alert fires (prevents flapping).
# AlertConsecutiveChecks=3
#
# Path to a state file for cross-run tracking.
# StateFile=/tmp/nzbget-health-state.json
#
# Whether to exit with error code when an alert fires (so NZBGet logs it).
# ExitOnAlert=yes
#
##############################################################################

NZBGET_CONFIG = r"""
### NZBGET SCRIPT CONFIGURATION (read by NZBGet; ignored by Python)

# NZBGet connection details
NzbGetUrl=http://127.0.0.1:6789
NzbGetUsername=nzbget
NzbGetPassword=

# Thresholds
MinDownloadSpeedMBs=0
MaxFailureRate=0.30
FailureHistoryCount=50
MaxQueueSize=0
MinFreeDiskGB=5

# General behaviour
AlertConsecutiveChecks=3
StateFile=/tmp/nzbget-health-state.json
ExitOnAlert=yes

### NZBGET SCRIPT CONFIGURATION
"""

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_SUCCESS = 93
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


def _rpc_call(url: str, username: str, password: str, method: str, params: Any) -> Dict:
    """Make a JSON-RPC call to NZBGet."""
    body = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    if username and password:
        encoded_user = urllib.parse.quote(username, safe="")
        encoded_pass = urllib.parse.quote(password, safe="")
        creds = f"{encoded_user}:{encoded_pass}"
        req.add_header(
            "Authorization",
            "Basic " + __import__("base64").b64encode(creds.encode()).decode(),
        )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"RPC call '{method}' failed: {e}") from e


def _load_state(path: str) -> Dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(path: str, data: Dict) -> None:
    d = Path(path).parent
    d.mkdir(parents=True, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def main() -> int:
    rpc_url = _opt_str("NzbGetUrl", "http://127.0.0.1:6789").rstrip("/") + "/jsonrpc"
    username = _opt_str("NzbGetUsername", "nzbget")
    password = _opt_str("NzbGetPassword", "")
    min_speed_mb = _opt_float("MinDownloadSpeedMBs", 0)
    max_failure_rate = _opt_float("MaxFailureRate", 0.30)
    failure_history_count = _opt_int("FailureHistoryCount", 50)
    max_queue_size = _opt_int("MaxQueueSize", 0)
    min_free_gb = _opt_int("MinFreeDiskGB", 5)
    alert_consecutive = _opt_int("AlertConsecutiveChecks", 3)
    state_file = _opt_str("StateFile", "/tmp/nzbget-health-state.json")
    exit_on_alert = _opt_bool("ExitOnAlert", True)

    state = _load_state(state_file)

    alerts: List[str] = []
    warnings: List[str] = []
    info_lines: List[str] = []

    # ── Gather metrics from NZBGet ──
    try:
        status = _rpc_call(rpc_url, username, password, "status", [])
        result = status.get("result", {})

        # Server state
        server_paused = result.get("ServerPaused", False)
        server_standby = result.get("ServerStandBy", False)
        download_paused = result.get("DownloadPaused", False)
        post_paused = result.get("PostPaused", False)
        scan_paused = result.get("ScanPaused", False)

        # Queue
        queue_size = int(result.get("RemainingSizeLo", 0))
        queue_count = int(result.get("RemainingFileCount", 0))

        # Speed
        download_rate = int(result.get("DownloadRate", 0))  # bytes/sec
        download_rate_mb = download_rate / (1024 * 1024)

        # Free disk
        free_disk_mb = int(result.get("FreeDiskSpaceMB", 0))
        free_disk_gb = free_disk_mb / 1024

        # Uptime
        uptime_seconds = int(result.get("UpTimeSec", 0))
        uptime_str = f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m"
        info_lines.append(f"Uptime: {uptime_str}")

        # News server info
        news_servers_active = False
        for server in result.get("NewsServers", []):
            if server.get("Active", False):
                news_servers_active = True
                break

    except Exception as e:
        log("ERROR", f"Could not connect to NZBGet: {e}")
        return POSTPROCESS_ERROR

    # ── History (for failure rate) ──
    failure_count = 0
    history_total = 0
    try:
        history = _rpc_call(rpc_url, username, password, "history", [False])
        history_items = history.get("result", [])
        recent = history_items[:failure_history_count]
        history_total = len(recent)
        failure_count = sum(
            1 for h in recent
            if h.get("Status", "").startswith("FAILURE")
            or h.get("ParStatus", "").startswith("FAILURE")
        )
    except Exception:
        log("WARNING", "Could not fetch history for failure rate check.")

    # ── Queue items for detailed state ──
    active_downloads = 0
    try:
        groups = _rpc_call(rpc_url, username, password, "listgroups", [0])
        group_list = groups.get("result", [])
        active_downloads = sum(
            1 for g in group_list
            if g.get("RemainingSizeMB", 0) > 0
            and g.get("PausedSizeMB", 0) == 0
        )
    except Exception:
        pass

    # ── Check conditions ──

    # 1. Download speed
    if min_speed_mb > 0:
        has_active = active_downloads > 0 or queue_count > 0
        speed_low = download_rate_mb < min_speed_mb
        info_lines.append(
            f"Speed: {download_rate_mb:.1f} MB/s "
            f"(threshold: {min_speed_mb} MB/s, active: {has_active})"
        )
        if has_active and speed_low:
            streak = state.get("speed_low_streak", 0) + 1
            state["speed_low_streak"] = streak
            if streak >= alert_consecutive:
                alerts.append(
                    f"Download speed {download_rate_mb:.1f} MB/s below "
                    f"{min_speed_mb} MB/s for {streak} checks"
                )
            else:
                warnings.append(
                    f"Low speed: {download_rate_mb:.1f} MB/s (streak {streak}/{alert_consecutive})"
                )
        else:
            if state.get("speed_low_streak", 0) > 0:
                log("INFO", "Download speed restored; clearing alert streak.")
            state["speed_low_streak"] = 0

    # 2. Failure rate
    if history_total > 0:
        failure_rate = failure_count / history_total
        info_lines.append(
            f"Failure rate: {failure_count}/{history_total} = {failure_rate:.1%} "
            f"(threshold: {max_failure_rate:.1%})"
        )
        if failure_rate > max_failure_rate:
            streak = state.get("failure_streak", 0) + 1
            state["failure_streak"] = streak
            if streak >= alert_consecutive:
                alerts.append(
                    f"High failure rate: {failure_rate:.1%} ({failure_count}/{history_total}) "
                    f"for {streak} checks"
                )
            else:
                warnings.append(
                    f"Elevated failure rate: {failure_rate:.1%} (streak {streak}/{alert_consecutive})"
                )
        else:
            state["failure_streak"] = 0

    # 3. Queue size
    if max_queue_size > 0:
        info_lines.append(f"Queue: {queue_count} items (threshold: {max_queue_size})")
        if queue_count > max_queue_size:
            streak = state.get("queue_streak", 0) + 1
            state["queue_streak"] = streak
            if streak >= alert_consecutive:
                alerts.append(
                    f"Queue size {queue_count} exceeds {max_queue_size} "
                    f"for {streak} checks"
                )
            else:
                warnings.append(
                    f"Large queue: {queue_count} items (streak {streak}/{alert_consecutive})"
                )
        else:
            state["queue_streak"] = 0

    # 4. Free disk space
    if min_free_gb > 0:
        info_lines.append(f"Free disk: {free_disk_gb:.1f} GB (threshold: {min_free_gb} GB)")
        if free_disk_gb < min_free_gb:
            alerts.append(
                f"Low disk space: {free_disk_gb:.1f} GB free "
                f"(below {min_free_gb} GB threshold)"
            )

    # 5. Server state
    if server_paused:
        warnings.append("NZBGet server is paused")
    if server_standby:
        warnings.append("NZBGet server is in standby")
    if download_paused:
        info_lines.append("Downloads are paused")
    if not news_servers_active and queue_count > 0:
        warnings.append("No active news servers but queue has items")

    # ── Report ──
    for line in info_lines:
        log("INFO", line)

    for warning in warnings:
        log("WARNING", warning)

    for alert in alerts:
        log("ERROR", f"ALERT: {alert}")

    # Save state
    state["last_check"] = int(time.time())
    state["last_queue_count"] = queue_count
    state["last_download_rate_mb"] = round(download_rate_mb, 2)
    state["last_free_disk_gb"] = round(free_disk_gb, 1)
    _save_state(state_file, state)

    if alerts:
        log("INFO", f"Health check complete: {len(alerts)} alert(s), {len(warnings)} warning(s)")
        return POSTPROCESS_ERROR if exit_on_alert else SCRIPT_SUCCESS

    if warnings:
        log("INFO", f"Health check complete: {len(warnings)} warning(s)")

    log("DETAIL", "Health check passed.")
    return SCRIPT_SUCCESS


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        log("ERROR", f"NZBGetHealthMonitor crashed: {e}")
        raise SystemExit(POSTPROCESS_ERROR)
