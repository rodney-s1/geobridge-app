const { app, BrowserWindow, ipcMain, dialog, safeStorage } = require('electron')
const path = require('path')
const fs = require('fs')
const { spawn } = require('child_process')
const { autoUpdater } = require('electron-updater')

let mainWindow
let backendProcess

// isDev: true only when explicitly requested via APP_ENV=development
// Running `npm start` (unpackaged) should still load the built dist/
const isDev = process.env.APP_ENV === 'development'

// ---------------------------------------------------------------------------
// Update-blocking guard
//
// When an update has been downloaded and is ready to install, we set this flag.
// The backend's /api/customers endpoint honours a query param ?update_pending=1
// which the renderer injects when this flag is true — but more importantly,
// the renderer itself disables the "Sync from MyAdmin" (force_refresh) button
// and shows a prominent banner so the user installs the update first.
//
// This prevents a MyAdmin sync from starting mid-install and leaving the
// app / data in an inconsistent state.
// ---------------------------------------------------------------------------
let updateReadyToInstall = false
let pendingUpdateVersion  = null   // e.g. "1.2.3" shown in the UI

// ---------------------------------------------------------------------------
// Auto-updater configuration
// ---------------------------------------------------------------------------
const log = require('electron-log')
autoUpdater.autoDownload        = false   // Always ask first — never silently download
autoUpdater.autoInstallOnAppQuit = false  // We control install ourselves via IPC
autoUpdater.allowDowngrade      = false
autoUpdater.logger              = log
autoUpdater.logger.transports.file.level = 'info'

// ---------------------------------------------------------------------------
// GitHub token loader
//
// Called after app.whenReady() so app.getPath('userData') is available.
// Reads %APPDATA%\geobridge-app\github_token.json for private-repo access.
// Sets autoUpdater.requestHeaders once at startup — applies to all checks.
//
// Format: { "token": "ghp_xxxxxxxxxxxxxxxxxxxx" }
// ---------------------------------------------------------------------------
function applyGithubToken() {
  try {
    const tokenFile = path.join(app.getPath('userData'), 'github_token.json')
    log.info('[updater] Looking for token file at:', tokenFile)
    if (fs.existsSync(tokenFile)) {
      const raw = fs.readFileSync(tokenFile, 'utf8')
      log.info('[updater] Token file contents length:', raw.length)
      const data = JSON.parse(raw)
      if (data && data.token && typeof data.token === 'string' && data.token.length > 4) {
        // GHToken is the officially supported electron-updater property for
        // authenticating against private GitHub repositories. It is passed as
        // a Bearer token on every request made by the GitHubProvider.
        autoUpdater.addAuthHeader(`token ${data.token}`)
        log.info('[updater] GitHub token applied via addAuthHeader — private repo access enabled.')
        return
      } else {
        log.warn('[updater] Token file found but token field is missing or invalid.')
      }
    } else {
      log.warn('[updater] No github_token.json found at:', tokenFile)
    }
  } catch (e) {
    log.error('[updater] Failed to load github_token.json:', e.message)
  }
}

// ---------------------------------------------------------------------------
// "Remember me" encrypted credential storage
//
// Optional companion to the backend's session.json persistence (auth.py).
// session.json survives restarts as long as the MyAdmin session token is
// still valid (~1 week per Geotab docs). Once it expires, the app would
// normally have to show the Login screen again — UNLESS the user opted
// into "Remember me", in which case we can silently re-authenticate using
// the MyAdmin username/password stored here.
//
// Credentials are encrypted at rest using Electron's safeStorage API, which
// delegates to the OS-native credential vault (Windows DPAPI / macOS
// Keychain / Linux libsecret) — GeoBridge's own code never sees or handles
// a raw encryption key. The encrypted blob is written to a JSON file in
// app.getPath('userData'), mirroring the existing github_token.json pattern
// used by applyGithubToken() above.
//
// File format: { "encrypted": "<base64 ciphertext>" }
// Decrypted JSON payload: { "username": "...", "password": "...", "accountId": "..." }
// ---------------------------------------------------------------------------
function credentialsFilePath() {
  return path.join(app.getPath('userData'), 'remembered_credentials.json')
}

function saveRememberedCredentials(username, password, accountId) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error('OS-level encryption is not available on this device')
  }
  const payload = JSON.stringify({ username, password, accountId: accountId || null })
  const encryptedBuffer = safeStorage.encryptString(payload)
  const fileContents = JSON.stringify({ encrypted: encryptedBuffer.toString('base64') })
  fs.writeFileSync(credentialsFilePath(), fileContents, 'utf8')
}

function loadRememberedCredentials() {
  const file = credentialsFilePath()
  if (!fs.existsSync(file)) return null
  if (!safeStorage.isEncryptionAvailable()) return null
  try {
    const raw = fs.readFileSync(file, 'utf8')
    const { encrypted } = JSON.parse(raw)
    if (!encrypted) return null
    const decrypted = safeStorage.decryptString(Buffer.from(encrypted, 'base64'))
    return JSON.parse(decrypted)
  } catch (e) {
    log.error('[credentials] Failed to decrypt remembered credentials:', e.message)
    return null
  }
}

function clearRememberedCredentials() {
  const file = credentialsFilePath()
  if (fs.existsSync(file)) {
    fs.unlinkSync(file)
  }
}

// ---------------------------------------------------------------------------
// Python backend launcher
// ---------------------------------------------------------------------------
function startPythonBackend() {
  // When packaged by electron-builder, app code lives inside app.asar which
  // is a read-only virtual filesystem.  __dirname resolves inside the asar,
  // so we CANNOT use it to find extraResources (like python-embed/ or backend/).
  //
  // electron-builder places extraResources at:
  //   <install_dir>/resources/<name>          (packaged)
  //
  // process.resourcesPath always points to that resources/ folder whether
  // the app is packaged or running unpackaged from the project directory.
  //
  // Unpackaged (npm start / dev):  process.resourcesPath = <project>/
  // Packaged:                       process.resourcesPath = <install>/resources/
  //
  // python-embed/ is in extraResources → lands at resources/python-embed/ when
  //                              packaged (a self-contained Python runtime,
  //                              see scripts/setup_python_embed.js).
  // backend/ is in files       → lands at resources/app.asar/backend/ when
  //                              packaged, but asar is readable for JS files.
  //                              Python needs a real filesystem path though,
  //                              so backend/ is also added to extraResources.

  const isPacked = app.isPackaged

  // Python executable --------------------------------------------------------
  //
  // PACKAGED (isPacked): uses python-embed/ — the official python.org
  // "embeddable package" bundled as extraResources (see package.json and
  // scripts/setup_python_embed.js). This is a genuinely relocatable,
  // self-contained Python with no baked-in path to a base install, unlike
  // a regular venv — which is what used to be bundled here (.venv) and
  // would fail to launch on any machine other than the one that created it
  // (pyvenv.cfg hardcodes the original machine's Python install path).
  // Symptom of that old bug: app opens, backend never comes up on 8001,
  // UI shows "Cannot connect to backend" — with nothing useful in the
  // renderer's console because the failure is entirely in the child
  // process electron-log captures separately (see [Python Backend Error]).
  //
  // UNPACKAGED (dev mode / npm start): still uses the project's own .venv,
  // since that only ever runs on the developer's own machine — the
  // relocation problem doesn't apply there.
  let pythonCmd
  if (process.platform === 'win32') {
    pythonCmd = isPacked
      ? path.join(process.resourcesPath, 'python-embed', 'python.exe')
      : path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe')
  } else {
    // macOS/Linux dev fallback only — packaged builds are Windows-only
    // (see package.json "build.win"), so isPacked is never true here.
    pythonCmd = 'python3'
  }

  // Backend script -----------------------------------------------------------
  const backendPath = isPacked
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, '..', 'backend')

  const runScript = path.join(backendPath, 'run_backend.py')

  console.log('[backend] pythonCmd:', pythonCmd)
  console.log('[backend] runScript:', runScript)
  console.log('[backend] cwd:      ', backendPath)

  // Pass the user-data directory so Python stores mutable JSON files there
  // (survives reinstalls because the installer never touches %APPDATA%\GeoBridge\).
  const userDataDir = app.getPath('userData')
  console.log('[backend] userDataDir:', userDataDir)

  backendProcess = spawn(pythonCmd, [runScript], {
    cwd: backendPath,
    env: { ...process.env, GEOBRIDGE_DATA_DIR: userDataDir }
  })

  backendProcess.stdout.on('data', (data) => {
    console.log(`[Python Backend]: ${data}`)
  })

  backendProcess.stderr.on('data', (data) => {
    console.error(`[Python Backend Error]: ${data}`)
  })

  backendProcess.on('close', (code) => {
    console.log(`[Python Backend] exited with code ${code}`)
  })
}

// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    },
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#0f172a',
    show: false,
    title: 'GeoBridge'
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../frontend/dist/index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    // Kick off a silent background check ~4 s after the window appears so the
    // app is fully loaded before any update UI appears.
    if (!isDev) {
      setTimeout(() => {
        autoUpdater.checkForUpdates().catch(e =>
          log.error('[updater] background check failed:', e)
        )
      }, 4000)
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ---------------------------------------------------------------------------
// Auto-updater event handlers
// ---------------------------------------------------------------------------

// Fired when a new version is found on GitHub Releases
autoUpdater.on('update-available', (info) => {
  console.log('[updater] update available:', info.version)
  if (mainWindow) {
    mainWindow.webContents.send('update-available', {
      version:     info.version,
      releaseNotes: info.releaseNotes || null,
      releaseDate:  info.releaseDate  || null,
    })
  }
})

// Fired when the current version is already the latest
autoUpdater.on('update-not-available', (info) => {
  console.log('[updater] up to date, version:', info.version)
  if (mainWindow) {
    mainWindow.webContents.send('update-not-available', { version: info.version })
  }
})

// Download progress — forward percentage to renderer for the progress bar
autoUpdater.on('download-progress', (progress) => {
  if (mainWindow) {
    mainWindow.webContents.send('update-download-progress', {
      percent:          Math.round(progress.percent),
      transferred:      progress.transferred,
      total:            progress.total,
      bytesPerSecond:   progress.bytesPerSecond,
    })
  }
})

// Download complete — update is staged, ready to install
autoUpdater.on('update-downloaded', (info) => {
  console.log('[updater] download complete:', info.version)
  updateReadyToInstall = true
  pendingUpdateVersion = info.version
  if (mainWindow) {
    mainWindow.webContents.send('update-downloaded', { version: info.version })
  }
})

// Any error during check or download
autoUpdater.on('error', (err) => {
  console.error('[updater] error:', err)
  if (mainWindow) {
    mainWindow.webContents.send('update-error', {
      message: err?.message || String(err)
    })
  }
})

// ---------------------------------------------------------------------------
// IPC handlers — called from the renderer via window.updaterAPI.*
// ---------------------------------------------------------------------------

// Renderer asks: is there an update pending install right now?
ipcMain.handle('updater:get-status', () => ({
  updateReadyToInstall,
  pendingUpdateVersion,
}))

// Renderer requests a fresh check
ipcMain.handle('updater:check', async () => {
  try {
    const result = await autoUpdater.checkForUpdates()
    return { ok: true, version: result?.updateInfo?.version || null }
  } catch (e) {
    return { ok: false, error: e?.message || String(e) }
  }
})

// Renderer requests download of the staged update
ipcMain.handle('updater:download', async () => {
  try {
    await autoUpdater.downloadUpdate()
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e?.message || String(e) }
  }
})

// Renderer requests install-and-relaunch
// We kill the Python backend first so it doesn't get orphaned
ipcMain.handle('updater:install', () => {
  console.log('[updater] install requested — killing backend and relaunching')
  if (backendProcess) {
    backendProcess.kill()
    backendProcess = null
  }
  autoUpdater.quitAndInstall(/* isSilent */ false, /* isForceRunAfter */ true)
})

// ---------------------------------------------------------------------------
// Existing IPC handlers
// ---------------------------------------------------------------------------
ipcMain.handle('get-app-version', () => app.getVersion())

// Renderer can ask "is an update blocking sync right now?"
ipcMain.handle('update-blocking-sync', () => updateReadyToInstall)

// ---------------------------------------------------------------------------
// "Remember me" credential IPC handlers — called via window.credentialsAPI.*
// ---------------------------------------------------------------------------
ipcMain.handle('credentials:save', (_event, { username, password, accountId }) => {
  try {
    saveRememberedCredentials(username, password, accountId)
    return { ok: true }
  } catch (e) {
    log.error('[credentials] save failed:', e.message)
    return { ok: false, error: e.message }
  }
})

ipcMain.handle('credentials:load', () => {
  try {
    const credentials = loadRememberedCredentials()
    return { ok: true, credentials }
  } catch (e) {
    log.error('[credentials] load failed:', e.message)
    return { ok: false, credentials: null, error: e.message }
  }
})

ipcMain.handle('credentials:clear', () => {
  try {
    clearRememberedCredentials()
    return { ok: true }
  } catch (e) {
    log.error('[credentials] clear failed:', e.message)
    return { ok: false, error: e.message }
  }
})

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(() => {
  // Apply GitHub token immediately so all update checks are authenticated
  applyGithubToken()

  if (!isDev) {
    startPythonBackend()
  }
  // In dev the backend is already running; give it a moment then open the window.
  // In prod we need ~2s for Python to start up before the window loads.
  const delay = isDev ? 500 : 2000
  setTimeout(createWindow, delay)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (backendProcess) {
    backendProcess.kill()
  }
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
