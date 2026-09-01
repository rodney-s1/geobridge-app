# GeoBridge Invoicing Suite

Electron + React + FastAPI desktop app that bridges Geotab MyAdmin device/customer
data with QuickBooks invoicing for reconciliation and billing.

## Local Development

```txt
npm install
npm run dev
```

Runs the Python backend, the Vite dev server for the React frontend, and Electron
together (see `dev`/`dev:backend`/`dev:frontend` in `package.json`).

## Building the Installer (Windows)

First, build the bundled Python runtime (only needed once, or again whenever
`backend/requirements.txt` changes):

```txt
npm run setup:python-embed
```

Then:

```txt
npm run build
```

Builds `frontend/dist` and packages a Windows NSIS installer into `release/`
(via `electron-builder`), bundling `python-embed/` as `extraResources` so the
Python backend runs standalone on the end user's machine — **without
requiring Python to be installed there at all.**

`python-embed/` is the official [python.org "embeddable
package"](https://docs.python.org/3/using/windows.html#the-embeddable-package),
a genuinely self-contained, relocatable Python runtime with `backend/requirements.txt`
installed into it. This intentionally replaced an earlier approach that bundled
a regular `.venv` — a venv's `python.exe` hardcodes the absolute path to the
*base* Python installation that created it (in its `pyvenv.cfg`), so a `.venv`
copied onto a different machine fails to launch: the app opens, the backend
never comes up on port 8001, and the UI shows "Cannot connect to backend" with
no obvious error on screen. `python-embed/` has no such dependency — it's the
same kind of distribution python.org publishes specifically for redistributing
with an application. See `scripts/setup_python_embed.js` for the full story
and implementation.

`python-embed/` is gitignored and built locally on the release machine only —
it is never committed.

## Publishing a Release (so "Check for Updates" works)

The in-app **"Check for Updates"** button (`electron-updater`) only ever finds
a new version if that version was published as an actual **GitHub Release**
with the installer attached — it has no awareness of git commits/branches on
its own. Building an installer locally with `npm run build` does **not**
publish it anywhere.

```txt
npm run release
```

or with an explicit bump:

```txt
npm run release -- minor     # or: major, patch (default), or an explicit "1.2.3"
```

This one command (`scripts/release.js`) does the whole workflow safely:

1. Refuses to run with uncommitted changes or on a branch other than `main`.
2. `git pull --ff-only origin main` — guarantees the build includes every
   commit already pushed to GitHub (this was the step that got missed for a
   past release, producing an installer whose version number didn't match
   any real GitHub Release).
3. `npm version <bump>` — bumps `package.json`, commits, and tags.
4. Rebuilds the frontend.
5. `electron-builder --publish always` — builds the Windows installer **and**
   uploads it as a GitHub Release in one step.
6. Pushes the version-bump commit + tag back to `origin/main`.

**Requirements before running it:**
- Must be run on **Windows**. `npm run release` calls
  `scripts/setup_python_embed.js` automatically to (re-)sync `python-embed/`
  with the current `backend/requirements.txt` before building, so it cannot
  be run from a Linux CI/sandbox — the embeddable Python package it downloads
  is Windows-specific (`embed-amd64`), and `pywin32`'s COM registration only
  makes sense on Windows.
- A `GH_TOKEN` environment variable with `repo` scope (the repo is private):
  ```powershell
  $env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"
  ```

If anything fails partway through, the script tells you exactly which step
failed and how to recover (retry that step manually, or undo the version
bump with `git tag -d vX.Y.Z && git reset --hard HEAD~1`).

## Project Structure

- `backend/` — FastAPI Python backend (`geotab/` package: auth, reconciliation,
  settings, invoices, S3 sync, etc.)
- `frontend/` — React + Vite SPA
- `electron/` — Electron main process (`main.js`), preload bridge
  (`preload.js`), auto-updater wiring
- `scripts/` — release automation, S3 backup script

## Data & Storage

- Runtime JSON files (QB invoice quantities, billing overrides, MyAdmin
  cache, persisted session token, etc.) live in the OS user-data directory
  (`app.getPath('userData')`), not inside the installed app bundle, so they
  survive reinstalls/updates.
- Optional S3 sync backs up a subset of these files (`ADMIN_ONLY_FILES` /
  `ALL_USER_FILES`) across machines; session tokens and local caches are
  never synced (`LOCAL_ONLY_FILES`).

## Authentication

- Logs in against the Geotab MyAdmin API. The MyAdmin session token is
  persisted to disk (never the password) so the app can silently resume a
  still-valid session on relaunch without asking the user to log in again.
- An optional **"Remember me on this device"** checkbox additionally stores
  the MyAdmin credentials, encrypted at rest via Electron's `safeStorage`
  (OS-native vault), so the app can silently re-authenticate even after the
  MyAdmin session token itself has expired.

## Deployment

- **Platform**: Windows desktop app (Electron), distributed as an NSIS
  installer via private GitHub Releases with auto-update support
- **Tech Stack**: Electron + React (Vite) + FastAPI (Python) +
  `electron-updater` / `electron-builder`
- **Status**: Actively developed; user builds and runs the installer locally
