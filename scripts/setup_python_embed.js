#!/usr/bin/env node
/**
 * setup_python_embed.js — Build a portable, self-contained Python runtime
 * for the packaged GeoBridge installer, replacing the old `.venv` bundling
 * approach.
 *
 * WHY THIS EXISTS
 * ────────────────
 * A regular virtualenv (`python -m venv .venv`) is NOT relocatable: its
 * `Scripts\python.exe` is a thin launcher whose `pyvenv.cfg` hardcodes the
 * absolute path to the *base* Python installation that created it (e.g.
 * `home = C:\Users\Rodney\AppData\Local\Programs\Python\Python312`). When
 * electron-builder copies `.venv` into an installer's `extraResources` and
 * a user installs it on a DIFFERENT machine, that `python.exe` tries to
 * find the original machine's base Python install — which doesn't exist —
 * and fails to launch. Symptom: the Electron app opens, the backend never
 * comes up on port 8001, and the UI shows "Cannot connect to backend."
 *
 * The official python.org "embeddable package" is a different, genuinely
 * self-contained distribution purpose-built for exactly this redistribution
 * scenario — no base-install path baked in anywhere. This script downloads
 * it, enables site-packages (disabled by default in the embeddable build),
 * bootstraps pip, and installs backend/requirements.txt into it. The result
 * (`python-embed/`) is what gets bundled as extraResources instead of
 * `.venv` going forward — see package.json's "build.extraResources" and
 * electron/main.js's startPythonBackend().
 *
 * USAGE (Windows only — run from the project root)
 * ──────────────────────────────────────────────────
 *   npm run setup:python-embed
 *
 * Safe to re-run any time (e.g. after adding a new backend dependency to
 * requirements.txt) — it skips the download/extract step if python-embed/
 * already has a working interpreter, but always re-runs `pip install -r
 * requirements.txt` so new/updated dependencies get picked up.
 *
 * To force a full re-download (e.g. to bump the Python version), delete
 * the python-embed/ folder first, or pass --force.
 */

const { execSync } = require('child_process')
const fs = require('fs')
const path = require('path')

const ROOT = path.join(__dirname, '..')
const TARGET_DIR = path.join(ROOT, 'python-embed')

// Bump this if you need a newer Python. Must be a version that has an
// "embed-amd64" zip published at https://www.python.org/ftp/python/<ver>/
// (all CPython 3.9+ releases do). 3.12.7 is a safe, stable default with
// broad pre-built wheel availability for this project's dependencies
// (fastapi, pydantic v2/pydantic-core, boto3, pywin32).
const PYTHON_VERSION = process.env.PYTHON_EMBED_VERSION || '3.12.7'
const FORCE = process.argv.includes('--force')

function fail(message) {
  console.error(`\n[setup-python-embed] ERROR: ${message}\n`)
  process.exit(1)
}

function log(message) {
  console.log(`[setup-python-embed] ${message}`)
}

function run(cmd, opts = {}) {
  console.log(`\n$ ${cmd}`)
  execSync(cmd, { cwd: ROOT, stdio: 'inherit', ...opts })
}

// Synchronous download via PowerShell's Invoke-WebRequest — avoids Node
// async/Promise complexity in what is otherwise a plain top-to-bottom script,
// and Invoke-WebRequest is guaranteed present on every target Windows box.
function downloadFileSync(url, destPath) {
  run(
    `powershell -NoProfile -Command "$ProgressPreference = 'SilentlyContinue'; ` +
    `Invoke-WebRequest -Uri '${url}' -OutFile '${destPath}'"`
  )
  if (!fs.existsSync(destPath)) {
    fail(`Download did not produce a file at ${destPath} (url: ${url})`)
  }
}

// ── 0. Windows only ──────────────────────────────────────────────────────
if (process.platform !== 'win32') {
  fail(
    'This script must be run on Windows.\n' +
    '  The embeddable Python distribution built here is Windows-specific\n' +
    '  (embed-amd64) and is only ever used for the Windows NSIS installer.'
  )
}

// ── 1. Download + extract the embeddable distribution (skip if present) ──
const pythonExe = path.join(TARGET_DIR, 'python.exe')
const needsDownload = FORCE || !fs.existsSync(pythonExe)

if (needsDownload) {
  if (FORCE && fs.existsSync(TARGET_DIR)) {
    log('--force passed: removing existing python-embed/ ...')
    fs.rmSync(TARGET_DIR, { recursive: true, force: true })
  }

  const zipUrl = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`
  const zipPath = path.join(ROOT, `python-embed-${PYTHON_VERSION}.zip`)

  log(`Downloading Python ${PYTHON_VERSION} embeddable package...`)
  log(zipUrl)
  downloadFileSync(zipUrl, zipPath)

  log(`Extracting to ${TARGET_DIR} ...`)
  fs.mkdirSync(TARGET_DIR, { recursive: true })
  run(
    `powershell -NoProfile -Command "Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${TARGET_DIR}' -Force"`
  )
  fs.unlinkSync(zipPath)

  if (!fs.existsSync(pythonExe)) {
    fail(`Extraction finished but ${pythonExe} was not found — the zip layout may have changed.`)
  }
} else {
  log(`python-embed/ already set up (found ${pythonExe}) — skipping download. Use --force to redo it.`)
}

// ── 2. Enable site-packages (always re-checked, not just on fresh download) ─
// The embeddable distribution ships with a `python3XX._pth` file that
// disables `import site` by default AND (unlike a normal install) has no
// unpacked Lib\ directory on disk for Python's prefix-detection to find a
// "landmark" file in — the stdlib lives zipped inside python3XX.zip. That
// means uncommenting "import site" alone is not enough: Python still can't
// reliably locate Lib\site-packages, which is exactly why pip installs fine
// ("Successfully installed pip-...") but the VERY NEXT invocation of that
// same interpreter says "No module named pip" — pip got written to disk,
// but the interpreter can't find its own site-packages to import it back
// from. Fix: explicitly add "Lib\site-packages" as its own line in the
// ._pth file (Python reads ._pth files as an explicit sys.path list), and
// pre-create that folder so get-pip.py has somewhere to install into.
//
// This block is idempotent and runs on every invocation (not gated behind
// needsDownload) so a partially-set-up python-embed/ left behind by an
// earlier failed run gets healed automatically on the next `npm run
// setup:python-embed` — no --force / manual delete needed.
const pthFiles = fs.readdirSync(TARGET_DIR).filter(f => /^python\d+\._pth$/.test(f))
if (pthFiles.length === 0) {
  fail(`Could not find a python3XX._pth file in ${TARGET_DIR} to enable site-packages.`)
}
const pthPath = path.join(TARGET_DIR, pthFiles[0])
let pthContents = fs.readFileSync(pthPath, 'utf8')

if (/^\s*#\s*import site\s*$/m.test(pthContents)) {
  pthContents = pthContents.replace(/^\s*#\s*import site\s*$/m, 'import site')
} else if (!/^\s*import site\s*$/m.test(pthContents)) {
  fail(`${pthFiles[0]} doesn't contain the expected "#import site" line — layout may have changed:\n${pthContents}`)
}

if (!/^\s*Lib\\site-packages\s*$/m.test(pthContents)) {
  // Insert right before the "import site" line so it's picked up as a
  // sys.path entry regardless of where python.exe considers itself rooted.
  pthContents = pthContents.replace(/^\s*import site\s*$/m, 'Lib\\site-packages\nimport site')
}
fs.writeFileSync(pthPath, pthContents, 'utf8')
log(`Enabled site-packages (Lib\\site-packages + import site) in ${pthFiles[0]}`)

const sitePackagesDir = path.join(TARGET_DIR, 'Lib', 'site-packages')
fs.mkdirSync(sitePackagesDir, { recursive: true })

// ── 3. Bootstrap pip (skip if already importable) ---------------------------
let pipAlreadyWorks = false
try {
  execSync(`"${pythonExe}" -m pip --version`, { cwd: ROOT, stdio: 'pipe' })
  pipAlreadyWorks = true
  log('pip already importable in python-embed/ — skipping bootstrap.')
} catch (e) {
  // pip not importable yet (fresh install, or healing a broken previous run) — bootstrap it below.
}

if (!pipAlreadyWorks) {
  const getPipPath = path.join(ROOT, 'get-pip.py')
  log('Downloading get-pip.py ...')
  downloadFileSync('https://bootstrap.pypa.io/get-pip.py', getPipPath)
  log('Installing pip into the embeddable interpreter...')
  run(`"${pythonExe}" "${getPipPath}" --no-warn-script-location`)
  fs.unlinkSync(getPipPath)

  // Verify pip is actually importable now — catches the "Successfully
  // installed pip-..." / "No module named pip" failure mode immediately
  // instead of letting it surface later as a confusing pip-install error.
  try {
    execSync(`"${pythonExe}" -m pip --version`, { cwd: ROOT, stdio: 'pipe' })
  } catch (e) {
    fail(
      'pip was installed but is still not importable by python-embed/python.exe.\n' +
      `  Check that ${pthFiles[0]} contains a "Lib\\\\site-packages" line and that\n` +
      `  ${sitePackagesDir} actually contains a pip/ folder after installation.`
    )
  }
}

// ── 4. Install backend dependencies ----------------------------------------
const requirementsPath = path.join(ROOT, 'backend', 'requirements.txt')
log('Installing backend/requirements.txt into python-embed/ ...')
run(`"${pythonExe}" -m pip install --no-warn-script-location -r "${requirementsPath}"`)

// ── 5. pywin32 post-install (registers COM DLLs needed for QBFC) -----------
// pywin32 normally needs a one-time post-install step to register its COM
// support DLLs (pythoncom3XX.dll etc.) with the Windows registry. Look for
// pywin32_postinstall.py under the embeddable interpreter's OWN Scripts
// directory (not the global one) and run it against THIS interpreter, so
// the registration matches the DLLs actually shipped inside python-embed/.
try {
  const scriptsDir = path.join(TARGET_DIR, 'Scripts')
  const postinstallPath = path.join(scriptsDir, 'pywin32_postinstall.py')
  if (fs.existsSync(postinstallPath)) {
    log('Running pywin32 post-install (registers COM DLLs)...')
    run(`"${pythonExe}" "${postinstallPath}" -install`)
  } else {
    log('pywin32_postinstall.py not found under python-embed/Scripts — skipping. ' +
        'If QBFC/COM calls fail on an end-user machine with a pythoncom DLL error, ' +
        'this is the first thing to check.')
  }
} catch (e) {
  log(`pywin32 post-install step failed (non-fatal, continuing): ${e.message}`)
}

log('')
log('Done. python-embed/ is ready and will be bundled as extraResources by')
log('electron-builder (see package.json). Verify with:')
log(`  "${pythonExe}" -c "import fastapi, uvicorn, boto3; print('OK')"`)
console.log('')
