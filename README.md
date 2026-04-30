# NZBGet Scripts

Scripts to improve NZBGet automation and your Sonarr/Radarr workflow.

These are **NZBGet extensions** (PostProcess + Queue). Install them into NZBGet’s `ScriptDir`, make them executable, and enable/configure them in the WebUI under **Settings → Extensions**.

---

## Quick links

- [Scripts](#scripts)
- [Installation (NZBGet)](#installation-nzbget)
- [Recommended order](#recommended-order)
- [Script details](#script-details)
- [Contributing / Maintenance](#contributing--maintenance)
- [License](#license)
- [Support](#support)

---

## Badges (optional)

Repo: `https://github.com/evenwebb/nzbget-user-scripts`

- License: `https://img.shields.io/github/license/evenwebb/nzbget-user-scripts`
- Latest tag: `https://img.shields.io/github/v/tag/evenwebb/nzbget-user-scripts`
- Stars: `https://img.shields.io/github/stars/evenwebb/nzbget-user-scripts`

## Scripts

| Script | Type | Description |
|:--|:--|:--|
| **`FailedDownloadClassifier/`** | PostProcess | Classifies common failures (missing articles/DMCA-like, passworded, bad archive, disk space, etc.) and writes a small artifact into the download folder so you can make smarter retry decisions. |
| **`CleanupJunkFiles/`** | PostProcess | Deletes junk files after successful processing (e.g. `.sfv`, `.url`, `.nzb`, optional `.par2`) and can delete **sample videos** safely. Always exits SUCCESS when it runs. |
| **`ReverseName/`** | PostProcess | Detects and fixes “reversed filename” obfuscation (robust trigger to avoid false positives). |
| **`PermissionsUnraidDefault/`** | PostProcess | Applies Unraid-style default permissions/ownership (`nobody:users`, dirs `0775`, files `0664`) to the download directory. |
| **`CharacterTranslator/`** | PostProcess | Fixes filename encoding/mojibake issues, normalizes Unicode, and optionally sanitizes/ASCII-transliterates names for better matching. |
| **`PasswordDetector/`** | Queue Script | Detects password-protected RARs early during download and can cancel the NZB to save bandwidth. |

## Installation (NZBGet)

- **Copy scripts** into NZBGet `ScriptDir`.
- NZBGet v24+ (Extensions UI): use the **manifest-based folders** so options appear in the WebUI:
  - `FailedDownloadClassifier/`
  - `CleanupJunkFiles/`
  - `ReverseName/`
  - `PermissionsUnraidDefault/`
  - `CharacterTranslator/`
  - `PasswordDetector/`
- **Make them executable** (at minimum the `main.py` files):

```bash
chmod +x "FailedDownloadClassifier/main.py" "CleanupJunkFiles/main.py" "ReverseName/main.py" "PermissionsUnraidDefault/main.py" "CharacterTranslator/main.py" "PasswordDetector/main.py"
```

## Keeping scripts updated

If you install these scripts into your NZBGet `ScriptDir` and want an easy way to keep them up to date from GitHub, use:

- `nzbget-scripts-updater.sh` (downloads the repo ZIP and updates scripts in-place)

It supports **dry-run**, **backups**, and can optionally only update extensions you already have installed.

Because all settings live in NZBGet’s config (WebUI), updates do not require preserving any in-script config blocks.

- **Enable extensions** in NZBGet WebUI:
  - Go to **Settings → Extensions**
  - Enable/configure each extension (and enable per-category if you prefer)
  - **All options are configurable in the NZBGet WebUI** (declared in each extension’s `manifest.json`)
  - Order matters: use **Reorder extensions** to set the post-process run sequence

### Recommended order

- **`FailedDownloadClassifier`**: early/neutral (safe; will skip on success)
- **`CharacterTranslator`**: early (after unpack), before rename/import steps (helps matching)
- **`ReverseName`**: after unpack, before any import scripts (Sonarr/Radarr)
- **`PermissionsUnraidDefault`**: after import (or near-last if you don’t run import scripts via NZBGet)
- **`CleanupJunkFiles`**: last (after import/notify scripts)

Queue scripts:

- **`PasswordDetector`**: runs during download on `FILE_DOWNLOADED` events (not part of the post-process order)

### Suggested full setup order (using all scripts)

Queue scripts (event-driven):

1. **`PasswordDetector`** (Queue Script): runs while downloading; cancels password-protected RARs early.

Post-processing extensions (set via **Reorder extensions**, top → bottom):

1. **`FailedDownloadClassifier`**: classifies failures (skips on success).
2. **`CharacterTranslator`**: fix mojibake/Unicode/sanitize names before anything else.
3. **`ReverseName`**: fix reversed names (best before importers try to match).
4. **(Your Sonarr/Radarr import script, if you run one via NZBGet)**.
5. **`PermissionsUnraidDefault`**: apply final ownership/modes after files are in their final place.
6. **`CleanupJunkFiles`**: delete junk/samples last.

---

## Quick Start

1. Copy scripts into NZBGet `ScriptDir` and `chmod +x` them (see [Installation (NZBGet)](#installation-nzbget)).
2. In NZBGet WebUI, enable them under **Settings → Extensions**.
3. Use **Reorder extensions** to match the [Recommended order](#recommended-order).
4. First run:
   - Set `DryRun=yes` for `ReverseName` (and `CleanupJunkFiles` if you want to preview deletions),
   - then use **Post-process again** on a history item to validate behavior.
5. Optional: set up the updater:
   - Edit `nzbget-scripts-updater.sh` (`ZIP_URL`, `NZBGET_SCRIPTDIR`, `DRY_RUN`)
   - Run it on a schedule (cron) or manually after pulling changes.

## Script details

### `FailedDownloadClassifier`

- **Writes** (in `NZBPP_DIRECTORY` and/or `NZBPP_FINALDIR` depending on options):
  - `.nzbget_failure_classification.json`
  - `.nzbget_failure_classification.txt`
  - optional marker `FAILURE_<class>.txt`

- **Options** (in NZBGet):
  - `ArtifactDir` = `download` | `final` | `both`
  - `CreateMarkerFile` = `yes` | `no`
  - `MaxBytesPerFile` (bytes)
  - `MaxFiles` (count)
  - `PreferSubdirs` (comma-separated)
  - Failure classes include `dmca` vs `missing_articles` when DMCA/takedown hints are found in logs.

### `CleanupJunkFiles`

- **Safety**:
  - Runs only when `NZBPP_TOTALSTATUS=SUCCESS` by default.
  - Exits with `SUCCESS (93)` when it runs; it won’t fail the download.
  - `DeleteArchives` is **off** by default.

- **Sample deletion** (enabled by default):
  - Deletes videos inside folders named `sample`/`samples`
  - Deletes `*sample*` videos only if under `SampleMaxSizeMB` (default 250MB)

- **Options** (in NZBGet):
  - `DeleteSamples` = `yes` | `no`
  - `SampleDirNames` (comma-separated)
  - `SampleVideoExts` (comma-separated)
  - `SampleMaxSizeMB` (integer)
  - `KeepGlobs` (comma-separated globs to never delete)
  - `KeepDirs` (comma-separated dir names; anything under them is never deleted)
  - `ArchiveDeleteRequiresMedia` = `yes` | `no` (guardrail for `DeleteArchives=yes`)
  - `MediaExts` (comma-separated; used by the archive guardrail)

### `ReverseName`

Fixes releases where file/folder names were reversed.

- **Defaults (safe)**:
  - Runs on `SUCCESS` only.
  - Renames files only (folders disabled by default).
  - Uses a robust trigger to avoid renaming already-normal names.

- **Robust trigger**:
  - `RequireStrongId=yes` (default)
  - Strong IDs include:
    - TV: `S01E02`, `1x02`
    - Dates: `YYYY-MM-DD` / `YYYY.MM.DD`
    - Movies: a standalone year `1990`–`2099` **only when** other release tokens exist (controlled by `MinScore`)
  - `StrongIdAllowYear=yes` (default)

### `PermissionsUnraidDefault`

Applies Unraid-style ownership and permissions to the processed folder.

- Defaults:
  - owner/group: `nobody:users`
  - directories: `0775`
  - files: `0664`
- Docker note:
  - If the script can’t `chown`, it will log a warning but won’t fail the NZBGet job by default (`IgnoreChownErrors=yes`).
  - Uses a fast path (only changes mode/owner when they differ) for better performance on large trees.

### `CharacterTranslator`

Fixes filename encoding issues and normalizes names.

- Defaults (safe):
  - `FixMojibake=yes` (repairs common `Ã©`-style corruption)
  - `Normalization=NFC` (helps consistent matching across systems)
  - `Sanitize=yes` (replaces unsafe characters like `?` with `_`)
  - `AsciiOnly=no` (keeps non-Latin scripts unless you opt in)

### `PasswordDetector`

Queue script that checks downloaded `.rar` files for encryption/password protection **before the download finishes**.

- Default behavior:
  - Event: `FILE_DOWNLOADED`
  - Action: `mark-bad` (prints `[NZB] MARK=BAD` to cancel the NZB)
  - Caches results in `.nzbget_passworddetector.json` inside the NZB directory
- Requirements:
  - `unrar` or `7z` available in `PATH` (script will try `unrar` first by default)
- Options:
  - `Action` = `mark-bad` | `pause` | `none`
    - `pause` uses NZBGet RPC `editqueue(GroupPause, ...)` via `NZBOP_CONTROL*` env vars (see [Extension Scripts](https://nzbget.com/documentation/extension-scripts/) and [editqueue API](https://nzbget.com/documentation/api/editqueue)).


## Contributing / Maintenance

- **When adding a new script or updating an existing one, update this `README.md` too** (scripts table + any new options/behavior).

---

## Troubleshooting / FAQ

<details>
<summary><strong>My script changes don’t show up in NZBGet</strong></summary>

- Make sure the extension folder is in `ScriptDir` and the `main.py` is executable (`chmod +x ExtensionName/main.py`).
- NZBGet caches the extension list while you’re on the settings page. Switch **Settings → Downloads → Settings** (or restart NZBGet) to force a rescan.
- Use **Post-process again** to test changes on an existing history item.
</details>

<details>
<summary><strong>Sonarr/Radarr import broke after cleanup</strong></summary>

- Ensure `CleanupJunkFiles` is **last** in the post-process order (**Reorder extensions**).
- Keep `DeleteArchives=no` unless you’re sure you never need RAR/ZIP volumes after unpack/import.
- Start with `DryRun=yes` to confirm what would be deleted.
</details>

<details>
<summary><strong>ReverseName renamed something incorrectly</strong></summary>

- Keep the robust defaults: `OnlyIfLooksReversed=yes`, `RequireStrongId=yes`.
- For stricter matching: increase `MinScore` to `3`.
- For movies only / TV only: toggle `StrongIdAllowYear`.
</details>

---

## License

This project is licensed under **GPL-3.0**. See `LICENSE`.

---

## Support

- Open a GitHub Issue with:
  - NZBGet version, OS/container info
  - the script name + relevant options you set
  - a redacted snippet of the NZBGet log around the script run

