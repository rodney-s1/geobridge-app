const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const path = require('path')
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
autoUpdater.autoDownload        = false   // Always ask first — never silently download
autoUpdater.autoInstallOnAppQuit = false  // We control install ourselves via IPC
autoUpdater.allowDowngrade      = false
autoUpdater.logger              = require('electron-log')
autoUpdater.logger.transports.file.level = 'info'

// ---------------------------------------------------------------------------
// Python backend launcher
// ---------------------------------------------------------------------------
function startPythonBackend() {
  // When packaged by electron-builder, app code lives inside app.asar which
  // is a read-only virtual filesystem.  __dirname resolves inside the asar,
  // so we CANNOT use it to find extraResources (like .venv or backend/).
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
  // .venv is in extraResources → lands at resources/.venv when packaged.
  // backend/ is in files       → lands at resources/app.asar/backend/ when
  //                              packaged, but asar is readable for JS files.
  //                              Python needs a real filesystem path though,
  //                              so backend/ is also added to extraResources.

  const isPacked = app.isPackaged

  // Python executable --------------------------------------------------------
  let pythonCmd
  if (process.platform === 'win32') {
    pythonCmd = isPacked
      ? path.join(process.resourcesPath, '.venv', 'Scripts', 'python.exe')
      : path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe')
  } else {
    pythonCmd = isPacked
      ? path.join(process.resourcesPath, '.venv', 'bin', 'python3')
      : 'python3'
  }

  // Backend script -----------------------------------------------------------
  const backendPath = isPacked
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, '..', 'backend')

  const runScript = path.join(backendPath, 'run_backend.py')

  console.log('[backend] pythonCmd:', pythonCmd)
  console.log('[backend] runScript:', runScript)
  console.log('[backend] cwd:      ', backendPath)

  backendProcess = spawn(pythonCmd, [runScript], {
    cwd: backendPath,
    env: { ...process.env }
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
      setTimeout(() => autoUpdater.checkForUpdates().catch(e =>
        console.error('[updater] background check failed:', e)
      ), 4000)
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
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(() => {
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
