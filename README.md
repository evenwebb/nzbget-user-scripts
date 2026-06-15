# 📥 NZBGet Scripts

Scripts to improve NZBGet automation and your Sonarr/Radarr workflow.

These are **NZBGet extensions** (PostProcess, Queue, Scan, and Scheduler). Install them into NZBGet's `ScriptDir`, make them executable, and enable/configure them in the WebUI under **Settings → Extensions**.

---

## 🔗 Quick links

- [Scripts](#-scripts)
- [Installation (NZBGet)](#-installation-nzbget)
- [Recommended order](#-recommended-order)
- [Script details](#-script-details)
- [Contributing / Maintenance](#-contributing--maintenance)
- [License](#-license)
- [Support](#-support)

---

## 📜 Scripts

| Script | Type | Description |
|:--|:--|:--|
| **`CleanNzbName/`** | Scan | Strips indexer/obfuscation/cross-post suffixes from NZB filenames in **NzbDir** before queuing (`[NZB] NZBNAME=…`); multi-pass; optional TV episode pattern detection. |
| **`PasswordDetector/`** | Queue | Detects password-protected RARs on `FILE_DOWNLOADED` and can cancel or pause the NZB early. |
| **`NZBGetHealthMonitor/`** | 🆕 Scheduler | Monitors NZBGet server health: download speed, failure rate, queue size, disk space, and server state. Streak-based alerting prevents flapping. |
| **`FailedDownloadClassifier/`** | PostProcess | Classifies common failures (missing articles/DMCA-like, passworded, bad archive, disk space, etc.) and writes a small artifact into the download folder. |
| **`CharacterTranslator/`** | PostProcess | Fixes filename encoding/mojibake, normalizes Unicode, and optionally sanitizes / ASCII-transliterates names for better matching. |
| **`UnderscoreToDot/`** | PostProcess | Replaces underscores with dots in filenames (optional dirs) for HONE-style releases; collision-safe, custom character replacements supported. |
| **`ReverseName/`** | PostProcess | Detects and fixes "reversed filename" obfuscation (robust trigger to avoid false positives). |
| **`VideoFileValidator/`** | 🆕 PostProcess | Validates downloaded video files with ffprobe to catch corruption, truncated files, and missing codecs. Optional auto-install of ffmpeg in containers. |
| **`PermissionsUnraidDefault/`** | PostProcess | Applies Unraid-style default permissions/ownership (`nobody:users`, dirs `0775`, files `0664`) to the download directory. |
| **`CleanupJunkFiles/`** | PostProcess | Deletes junk files after successful processing (e.g. `.sfv`, `.url`, `.nzb`, optional `.par2`) and can delete **sample videos** safely. Configurable file age threshold. |
| **`SampleRemover/`** | 🆕 PostProcess | Removes sample video files by name, directory, and size thresholds. Safe defaults — deletes only small files named "sample" or in Sample/ directories. |

## 📦 Installation (NZBGet)

- **Copy scripts** into NZBGet `ScriptDir`.
- NZBGet v24+ (Extensions UI): use the **manifest-based folders** so options appear in the WebUI:
  - `CleanNzbName/`
  - `PasswordDetector/`
  - `NZBGetHealthMonitor/`
  - `FailedDownloadClassifier/`
  - `CharacterTranslator/`
  - `UnderscoreToDot/`
  - `ReverseName/`
  - `VideoFileValidator/`
  - `PermissionsUnraidDefault/`
  - `CleanupJunkFiles/`
  - `SampleRemover/`
- **Make them executable** (at minimum the `main.py` files):

```bash
chmod +x \
  CleanNzbName/main.py \
  PasswordDetector/main.py \
  NZBGetHealthMonitor/main.py \
  FailedDownloadClassifier/main.py \
  CharacterTranslator/main.py \
  UnderscoreToDot/main.py \
  ReverseName/main.py \
  VideoFileValidator/main.py \
  PermissionsUnraidDefault/main.py \
  CleanupJunkFiles/main.py \
  SampleRemover/main.py
```

## 🔄 Keeping scripts updated

If you install these scripts into your NZBGet `ScriptDir` and want an easy way to keep them up to date from GitHub, use:

- `nzbget-scripts-updater.sh` (downloads the repo ZIP and updates scripts in-place)

It supports **dry-run**, **backups**, and can optionally only update extensions you already have installed.

Option values are stored in NZBGet's config when you save in the WebUI. Some `main.py` files include an optional `NZBGET_CONFIG` block for NZBGet to read as bundled defaults; the Python code still uses `NZBPO_*` at runtime, so Git updates do not require you to merge those blocks manually.

- **Enable extensions** in NZBGet WebUI:
  - Go to **Settings → Extensions**
  - Enable/configure each extension (and enable per-category if you prefer)
  - **All options are configurable in the NZBGet WebUI** (declared in each extension's `manifest.json`); after editing, click **Save changes** so values are written to NZBGet's config and passed to scripts as `NZBPO_*` (see [Extension Scripts](https://nzbget.com/documentation/extension-scripts/)).
  - **Reorder extensions** sets **post-processing** order only. **Queue**, **scan**, and **scheduler** extensions are not in that list (they use events / `NzbDir` / `TaskX` instead).

### 📋 Recommended order

- **`FailedDownloadClassifier`**: early/neutral (safe; will skip on success)
- **`CharacterTranslator`**: early (after unpack), before rename/import steps (helps matching)
- **`UnderscoreToDot`**: after unpack, with other rename normalizers; before importers
- **`ReverseName`**: after unpack, before any import scripts (Sonarr/Radarr)
- **`VideoFileValidator`**: after unpack/rename, before import (validates file integrity)
- **`PermissionsUnraidDefault`**: after import (or near-last if you don't run import scripts via NZBGet)
- **`CleanupJunkFiles`**: near the end (removes junk before final import/notify)
- **`SampleRemover`**: last (after cleanup, removes sample files that survived other cleanup)

Queue scripts:

- **`PasswordDetector`**: runs during download on `FILE_DOWNLOADED` events (not part of the post-process order)

Scan scripts:

- **`CleanNzbName`**: runs when NZBs appear in the incoming **NzbDir** (before queuing); not part of post-process order

Scheduler scripts:

- **`NZBGetHealthMonitor`**: runs on a schedule via NZBGet's TaskX directive; monitors server health independently

### 🧩 Suggested full setup order (using all scripts)

Queue scripts (event-driven):

1. **`PasswordDetector`** (Queue Script): runs while downloading; cancels password-protected RARs early.

Scan scripts (incoming folder):

- **`CleanNzbName`**: normalizes NZB filenames dropped into **NzbDir** (and from apps that save NZBs there).

Scheduler scripts (time-based):

- **`NZBGetHealthMonitor`**: runs every 5-15 minutes via `TaskX.Time` in NZBGet settings.

Post-processing extensions (set via **Reorder extensions**, top → bottom):

1. **`FailedDownloadClassifier`**: classifies failures (skips on success).
2. **`CharacterTranslator`**: fix mojibake/Unicode/sanitize names before anything else.
3. **`UnderscoreToDot`**: turn indexer underscores into dots (often needed before matching).
4. **`ReverseName`**: fix reversed names (best before importers try to match).
5. **`VideoFileValidator`**: validate video file integrity (before import, so corrupt files are caught early).
6. **(Your Sonarr/Radarr import script, if you run one via NZBGet)**.
7. **`PermissionsUnraidDefault`**: apply final ownership/modes after files are in their final place.
8. **`CleanupJunkFiles`**: delete junk files.
9. **`SampleRemover`**: remove sample videos last.

---

## 🚀 Quick Start

1. Copy scripts into NZBGet `ScriptDir` and `chmod +x` them (see [Installation (NZBGet)](#-installation-nzbget)).
2. In NZBGet WebUI, enable them under **Settings → Extensions**.
3. Use **Reorder extensions** to match the [Recommended order](#-recommended-order).
4. First run:
   - Set **Dry run** / `DryRun=yes` for `ReverseName`, `UnderscoreToDot`, `CleanNzbName`, `CleanupJunkFiles`, `SampleRemover`, and `VideoFileValidator`, then **Save changes**.
   - **Post-processing**: use **Post-process again** on a history item to validate behavior.
   - **Scan** (`CleanNzbName`): test by dropping a copy of an `.nzb` into **NzbDir** (or use your app's normal add flow) and watch the NZBGet log / resulting queue name (not covered by **Post-process again**).
   - **Scheduler** (`NZBGetHealthMonitor`): configure `TaskX.Time=` in NZBGet settings to set the check interval, then watch the log for health reports.
5. Optional: set up the updater:
   - Edit `nzbget-scripts-updater.sh` (`ZIP_URL`, `NZBGET_SCRIPTDIR`, `DRY_RUN`)
   - Run it on a schedule (cron) or manually after pulling changes.

## 📖 Script details

Sections below follow roughly **scan → queue → scheduler → post-process** order. Option **names** match `manifest.json` (WebUI labels may differ).

### `CleanNzbName`

[Scan script](https://nzbget.com/documentation/scan-scripts/) that reads `NZBNP_NZBNAME` (with fallback to `NZBNP_FILENAME`) and, when a known tail is removed, prints `[NZB] NZBNAME=…` so NZBGet queues a cleaner display name.

- **Behavior**:
  - NZBGet may call scan extensions for **any** file under **NzbDir** (not only `.nzb`); this script **no-ops** for non-`.nzb` targets and exits **93** so zips and other drops are not errors.
  - Case-insensitive patterns (compiled once), **multi-pass** so stacked suffixes (e.g. `Release-BUYMORE-Obfuscated.nzb` → `Release.nzb`) still collapse.
  - Emits `[NZB] NZBNAME=` **only when** the name changes and the result is still a non-empty `*.nzb`.
  - Large built-in list of indexer / obfuscation / cross-post tails, grouped in `CleanNzbName/main.py` (cross-post → bracket tags → obfuscation → hyphen indexers). Use **`ExtraSuffixes`** for local additions.
  - Optional TV episode pattern detection (`DetectTVEpisode=yes`) finds `SxxExx` patterns in obfuscated names and restructures them for better Sonarr matching.

- **Options** (`manifest.json`):
  - `DryRun` — log only; do not emit `[NZB] NZBNAME=`.
  - `ExtraSuffixes` — comma-separated literal suffixes without `.nzb` (e.g. `-MyIndexer,-Foo`).
  - `LegacyD3Strip` — opt-in legacy `Clean.py`-style trailing strip (can false-positive).
  - `DetectTVEpisode` — detect and restructure TV episode patterns in obfuscated names.

### `PasswordDetector`

Queue script that checks downloaded `.rar` files for encryption/password protection **before the download finishes**.

- **Default behavior**:
  - Event: `FILE_DOWNLOADED` (see `queueEvents` in `manifest.json`).
  - Action: `mark-bad` (prints `[NZB] MARK=BAD` to cancel the NZB).
  - Caches results in `.nzbget_passworddetector.json` inside the NZB directory.
- **Requirements**: `unrar` or `7z` in `PATH` (script prefers `unrar` when `Tool=auto`).

- **Options** (`manifest.json`):
  - `Events` — comma-separated queue events.
  - `Action` — `mark-bad` | `pause` | `none` (`pause` uses NZBGet RPC `editqueue` via `NZBOP_CONTROL*`; see [editqueue API](https://nzbget.com/documentation/api/editqueue)).
  - `DryRun`, `MaxRarFiles`, `CommandTimeoutSec`, `UseCache`, `CacheFilename`, `Tool`, `CountEncryptedHeaders`.

### `NZBGetHealthMonitor`

🆕 Scheduler script that monitors NZBGet server health via the JSON-RPC API.

- **Monitors**:
  - Download speed (with configurable minimum threshold)
  - Failure rate over recent history items
  - Queue size growth
  - Free disk space on the download path
  - Server paused/standby state
  - News server connectivity
- **Streak-based alerting**: requires N consecutive bad checks before firing an alert, preventing false alarms from transient blips.
- **State persistence**: tracks alert streaks across scheduler invocations via a JSON state file.

- **Options** (`manifest.json`):
  - `NzbGetUrl`, `NzbGetUsername`, `NzbGetPassword` — connection details
  - `MinDownloadSpeedMBs` — speed threshold (0 = disabled)
  - `MaxFailureRate` — failure rate threshold (0.0–1.0)
  - `FailureHistoryCount` — number of recent history items to check
  - `MaxQueueSize` — queue size threshold (0 = disabled)
  - `MinFreeDiskGB` — free space threshold (0 = disabled)
  - `AlertConsecutiveChecks` — checks before alert fires
  - `StateFile` — path to cross-run state file
  - `ExitOnAlert` — whether to exit with error code when alerting

### `FailedDownloadClassifier`

- **Writes** (in `NZBPP_DIRECTORY` and/or `NZBPP_FINALDIR` depending on options):
  - `.nzbget_failure_classification.json`
  - `.nzbget_failure_classification.txt`
  - optional marker `FAILURE_<class>.txt`

- **Options** (`manifest.json`):
  - `ArtifactDir` — `download` | `final` | `both`
  - `CreateMarkerFile` — `yes` | `no`
  - `MaxBytesPerFile`, `MaxFiles`, `PreferSubdirs` (comma-separated subdirs scanned first)

- Failure classes include `dmca` vs `missing_articles` when DMCA/takedown hints are found in logs.

### `CharacterTranslator`

Fixes filename encoding issues, normalizes Unicode, and optionally sanitizes names.

- **Options** (`manifest.json`):
  - `RunMode`, `DryRun`, `RenameFiles`, `RenameDirs`, `TargetDir` (`directory` / `final`)
  - `Normalization` — `NFC` | `NFKC` | `NFD` | `NFKD` | `none`
  - `FixMojibake`, `Sanitize`, `SanitizeReplacement`, `CollapseRepeats`, `AsciiOnly`, `SkipIfTargetExists`
  - `AutoDetectEncoding` — use chardet for source encoding detection (optional dependency)

- **Defaults (safe)**: `FixMojibake=yes`, `Normalization=NFC`, `Sanitize=yes`, `AsciiOnly=no`.

### `UnderscoreToDot`

Replaces `_` with `.` in download paths (often HONE-style indexer naming) for better Sonarr/Radarr matching.

- **Options** (`manifest.json`):
  - `RunMode` — `success-only` | `always`
  - `DryRun`, `RenameFiles`, `RenameDirs`, `TargetDir` (`directory` / `final`)
  - `ReplaceScope` — `stem` (before last dot) | `all` (stem + extension segment)
  - `CustomReplacements` — comma-separated `OLD=NEW` pairs for arbitrary character substitutions
  - `EligibleExts` — comma-separated allowlist; empty = all files when renaming files
  - `SkipIfTargetExists` — if `no`, adds ` (1)` … before the extension when the target exists

- **Defaults**: `RunMode=success-only`, `ReplaceScope=stem`, `RenameDirs=no`, `SkipIfTargetExists=no`.

### `ReverseName`

Fixes releases where file/folder names were reversed, using conservative heuristics.

- **Options** (`manifest.json`):
  - `RunMode`, `DryRun`, `RenameFiles`, `RenameDirs`
  - `OnlyIfLooksReversed`, `RequireStrongId`, `StrongIdAllowYear`, `MinScore`
  - `EligibleExts`, `SkipIfTargetExists`

- **Robust trigger (defaults)**:
  - `RequireStrongId=yes`; strong IDs include TV (`S01E02`, `1x02`), dates (`YYYY-MM-DD` / `YYYY.MM.DD`), and optional standalone movie years with `StrongIdAllowYear=yes` (gated by `MinScore`).

### `VideoFileValidator`

🆕 Validates downloaded video files with ffprobe to detect corruption, truncation, and missing codec data.

- **Check modes**:
  - `fast`: probes first and last N seconds of each stream (catches 95%+ of corruption in seconds)
  - `full`: reads the entire file (catches everything but takes as long as the file duration)
- **Auto-install**: optionally installs ffmpeg automatically in Docker containers (`AutoInstallFfprobe=yes`). Detects apt-get (hotio/Debian) or apk (linuxserver/Alpine).
- **Failure action**: `log-only` (report but don't fail the download) or `mark-failed` (exit with error so NZBGet treats it as failed).

- **Options** (`manifest.json`):
  - `RunMode`, `DryRun`
  - `VideoExts` — extensions to check (`.mkv,.mp4,.avi,.mov,.wmv,.m4v,.ts,.m2ts`)
  - `MaxFileSizeGB` — skip files larger than this (0 = no limit)
  - `CheckMode` — `fast` | `full`
  - `FastProbeSeconds` — seconds to probe at start/end in fast mode
  - `MinDurationSeconds` — minimum valid video duration
  - `FailureAction` — `log-only` | `mark-failed`
  - `FfprobePath` — path to ffprobe (default: `ffprobe`)
  - `AutoInstallFfprobe` — auto-install ffmpeg if ffprobe is missing

- **Requirements**: `ffprobe` (from `ffmpeg` package). The linuxserver/nzbget Docker image includes it. For hotio, set `AutoInstallFfprobe=yes`.

### `PermissionsUnraidDefault`

Applies Unraid-style ownership and permissions to the processed folder (fast path: only touches paths that differ).

- **Options** (`manifest.json`):
  - `RunMode`, `DryRun`, `TargetDir` (`directory` / `final`)
  - `Owner`, `Group`, `DirMode`, `FileMode`, `IgnoreChownErrors`, `DockerAware`, `FollowSymlinks`

- **Defaults**: owner/group `nobody:users`, dirs `0775`, files `0664`, `IgnoreChownErrors=yes` (Docker-friendly when `chown` fails).

### `CleanupJunkFiles`

- **Safety**: runs on `SUCCESS` by default (`RunMode`), exits **93** when it runs, `DeleteArchives` **off** by default.
- **Samples** (default on): deletes videos under folders named `sample`/`samples`, and `*sample*` videos under `SampleMaxSizeMB` (default 250MB).
- **File age**: respects `MinAgeSeconds` to avoid touching files still being written by unpack/extraction.
- **Category rules**: supports per-category glob patterns via `CategoryGlobs`.

- **Options** (`manifest.json`):
  - `RunMode`, `DryRun`, `DeleteEmptyDirs`
  - `DeleteGlobs`, `DeletePar2`, `DeletePar2Globs`, `DeleteArchives`, `DeleteArchiveGlobs`, `NeverDeleteExts`
  - `DeleteSamples`, `SampleDirNames`, `SampleVideoExts`, `SampleMaxSizeMB`
  - `MinAgeSeconds` — minimum file age before deletion (0 = no minimum)
  - `KeepGlobs`, `KeepDirs`, `ArchiveDeleteRequiresMedia`, `MediaExts`

### `SampleRemover`

🆕 Removes sample video files from completed downloads.

- **Detection methods**:
  - Files with "sample" in the name (under size threshold)
  - Directories named `sample` or `samples` and their contents
  - Optionally: tiny video files under a minimum size (disabled by default)
- **Safe by design**: only deletes video files, respects size limits, runs on SUCCESS by default, always exits with code 93.

- **Options** (`manifest.json`):
  - `RunMode`, `DryRun`
  - `SampleMaxSizeMB` — maximum size for a sample file (default 200)
  - `DeleteSampleNamed` — delete files with "sample" in the name
  - `DeleteSampleDirs` — delete `sample`/`samples` directories and their contents
  - `DeleteContentsOfSampleDirs` — delete files inside sample directories
  - `VideoExts` — video file extensions to scan
  - `TinyVideoMaxMB` — threshold for tiny video detection
  - `DeleteTinyVideos` — also delete tiny video files not named "sample"

## 🤝 Contributing / Maintenance

- **When adding a new script or updating an existing one, update this `README.md` too** (scripts table + any new options/behavior).

---

## 🔧 Troubleshooting / FAQ

<details>
<summary><strong>My script changes don't show up in NZBGet</strong></summary>

- Make sure the extension folder is in `ScriptDir` and the `main.py` is executable (`chmod +x ExtensionName/main.py`).
- NZBGet caches the extension list while you're on the settings page. Switch **Settings → Downloads → Settings** (or restart NZBGet) to force a rescan.
- After editing extension options, click **Save changes**; otherwise `NZBPO_*` values may not be written and the script will keep using built-in defaults.
- Use **Post-process again** to test **post-processing** changes on a history item (scan/queue/scheduler behavior is exercised differently).
</details>

<details>
<summary><strong>Sonarr/Radarr import broke after cleanup</strong></summary>

- Ensure `CleanupJunkFiles` and `SampleRemover` are **last** in the post-process order (**Reorder extensions**).
- Keep `DeleteArchives=no` unless you're sure you never need RAR/ZIP volumes after unpack/import.
- Start with `DryRun=yes` to confirm what would be deleted.
</details>

<details>
<summary><strong>ReverseName renamed something incorrectly</strong></summary>

- Keep the robust defaults: `OnlyIfLooksReversed=yes`, `RequireStrongId=yes`.
- For stricter matching: increase `MinScore` to `3`.
- For movies only / TV only: toggle `StrongIdAllowYear`.
</details>

<details>
<summary><strong>VideoFileValidator reports "ffprobe not found"</strong></summary>

- The linuxserver/nzbget Docker image includes ffmpeg/ffprobe by default.
- For hotio/nzbget (Debian-based), set `AutoInstallFfprobe=yes` to auto-install ffmpeg via apt-get.
- For other images, install ffmpeg manually or set `FfprobePath` to the correct binary location.
</details>

<details>
<summary><strong>NZBGetHealthMonitor keeps alerting — how do I tune it?</strong></summary>

- Increase `AlertConsecutiveChecks` (e.g. from 2 to 5) to require more consecutive bad checks before firing.
- Adjust individual thresholds (`MinDownloadSpeedMBs`, `MaxFailureRate`, `MaxQueueSize`, `MinFreeDiskGB`) to match your environment.
- Set any threshold to 0 to disable that specific check.
- Delete the `StateFile` to reset all alert streaks.
</details>

---

## 📄 License

This project is licensed under **GPL-3.0**. See `LICENSE`.

---

## 🆘 Support

- Open a GitHub Issue with:
  - NZBGet version, OS/container info
  - the script name + relevant options you set
  - a redacted snippet of the NZBGet log around the script run

