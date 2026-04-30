# NZBGet Scripts

Scripts to improve NZBGet automation and your Sonarr/Radarr workflow.

These are **NZBGet post-processing (PP) scripts**. Install them into NZBGet’s `ScriptDir`, make them executable, and enable them via **Settings → EXTENSION SCRIPTS** (globally or per-category).

---

## Quick links

- [Scripts](#scripts)
- [Installation (NZBGet)](#installation-nzbget)
- [Recommended ScriptOrder](#recommended-scriptorder)
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
| **`FailedDownloadClassifier.py`** | PostProcess | Classifies common failures (missing articles/DMCA-like, passworded, bad archive, disk space, etc.) and writes a small artifact into the download folder so you can make smarter retry decisions. |
| **`CleanupJunkFiles.py`** | PostProcess | Deletes junk files after successful processing (e.g. `.sfv`, `.url`, `.nzb`, optional `.par2`) and can delete **sample videos** safely. Always exits SUCCESS when it runs. |
| **`ReverseName.py`** | PostProcess | Detects and fixes “reversed filename” obfuscation (robust trigger to avoid false positives). |
| **`PermissionsUnraidDefault.py`** | PostProcess | Applies Unraid-style default permissions/ownership (`nobody:users`, dirs `0775`, files `0664`) to the download directory. |
| **`CharacterTranslator.py`** | PostProcess | Fixes filename encoding/mojibake issues, normalizes Unicode, and optionally sanitizes/ASCII-transliterates names for better matching. |
| **`PasswordDetector.py`** | Queue Script | Detects password-protected RARs early during download and can cancel the NZB to save bandwidth. |

## Installation (NZBGet)

- **Copy scripts** into NZBGet `ScriptDir`.
- **Make them executable**:

```bash
chmod +x "FailedDownloadClassifier.py" "CleanupJunkFiles.py" "ReverseName.py" "PermissionsUnraidDefault.py" "CharacterTranslator.py" "PasswordDetector.py"
```

## Keeping scripts updated

If you install these scripts into your NZBGet `ScriptDir` and want an easy way to keep them up to date from GitHub, use:

- `nzbget-scripts-updater.sh` (downloads the repo ZIP and updates scripts in-place)

It supports **dry-run**, **backups**, and can optionally only update scripts you already have installed.

To preserve your local script default changes across updates, edit only the block marked:

- `# EDIT FOR YOUR SETUP`
- `# END EDIT FOR YOUR SETUP`

The updater also **updates itself** (it will replace its own file with the latest upstream version while preserving your local `EDIT FOR YOUR SETUP` block).

- **Enable scripts** in NZBGet WebUI:
  - Go to **Settings → EXTENSION SCRIPTS**
  - Add scripts to **Extensions** (global or per-category)
  - Order matters: set **ScriptOrder** so scripts run in the right sequence

### Recommended ScriptOrder

- **`CharacterTranslator.py`**: early (after unpack), before rename/import steps (helps matching)
- **`ReverseName.py`**: after unpack, before any import scripts (Sonarr/Radarr)
- **`PermissionsUnraidDefault.py`**: after import (or last if you don’t run import scripts via NZBGet)
- **`FailedDownloadClassifier.py`**: late (after unpack/par outcomes are known)
- **`CleanupJunkFiles.py`**: last (after import/notify scripts)

Queue scripts:

- **`PasswordDetector.py`**: runs during download on `FILE_DOWNLOADED` events (not part of ScriptOrder)

### Suggested full setup order (using all scripts)

Queue scripts (event-driven):

1. **`PasswordDetector.py`** (Queue Script): runs while downloading; cancels password-protected RARs early.

Post-processing scripts (set in NZBGet `ScriptOrder`, top → bottom):

1. **`CharacterTranslator.py`**: fix mojibake/Unicode/sanitize names before anything else.
2. **`ReverseName.py`**: fix reversed names (best before importers try to match).
3. **(Your Sonarr/Radarr import script, if you run one via NZBGet)**.
4. **`PermissionsUnraidDefault.py`**: apply final ownership/modes after files are in their final place.
5. **`FailedDownloadClassifier.py`**: classify failures (helps decide retry vs stop).
6. **`CleanupJunkFiles.py`**: delete junk/samples last.

---

## Quick Start

1. Copy scripts into NZBGet `ScriptDir` and `chmod +x` them (see [Installation (NZBGet)](#installation-nzbget)).
2. In NZBGet WebUI, enable them under **Settings → EXTENSION SCRIPTS → Extensions**.
3. Set **ScriptOrder** to match the [Recommended ScriptOrder](#recommended-scriptorder).
4. First run:
   - Set `DryRun=yes` for `ReverseName.py` (and `CleanupJunkFiles.py` if you want to preview deletions),
   - then use **Post-process again** on a history item to validate behavior.
5. Optional: set up the updater:
   - Edit `nzbget-scripts-updater.sh` (`ZIP_URL`, `NZBGET_SCRIPTDIR`, `DRY_RUN`)
   - Run it on a schedule (cron) or manually after pulling changes.

## Script details

### `FailedDownloadClassifier.py`

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

### `CleanupJunkFiles.py`

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

### `ReverseName.py`

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

### `PermissionsUnraidDefault.py`

Applies Unraid-style ownership and permissions to the processed folder.

- Defaults:
  - owner/group: `nobody:users`
  - directories: `0775`
  - files: `0664`
- Docker note:
  - If the script can’t `chown`, it will log a warning but won’t fail the NZBGet job by default (`IgnoreChownErrors=yes`).
  - Uses a fast path (only changes mode/owner when they differ) for better performance on large trees.

### `CharacterTranslator.py`

Fixes filename encoding issues and normalizes names.

- Defaults (safe):
  - `FixMojibake=yes` (repairs common `Ã©`-style corruption)
  - `Normalization=NFC` (helps consistent matching across systems)
  - `Sanitize=yes` (replaces unsafe characters like `?` with `_`)
  - `AsciiOnly=no` (keeps non-Latin scripts unless you opt in)

### `PasswordDetector.py`

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

- Make sure the file is in `ScriptDir` and is executable (`chmod +x script.py`).
- Refresh the NZBGet WebUI after changes.
- Use **Post-process again** to test changes on an existing history item.
</details>

<details>
<summary><strong>Sonarr/Radarr import broke after cleanup</strong></summary>

- Ensure `CleanupJunkFiles.py` is **last** in `ScriptOrder`.
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

