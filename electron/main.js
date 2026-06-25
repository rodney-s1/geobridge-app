const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('path')
const { spawn } = require('child_process')

let mainWindow
let backendProcess

// isDev: true only when explicitly requested via APP_ENV=development
// Running `npm start` (unpackaged) should still load the built dist/
const isDev = process.env.APP_ENV === 'development'

function startPythonBackend() {
  const backendPath = path.join(__dirname, '../backend')
  const pythonCmd = process.platform === 'win32'
  ? path.join(__dirname, '../.venv/Scripts/python.exe')
  : 'python3'

  const runScript = path.join(backendPath, 'run_backend.py')
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
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

app.whenReady().then(() => {
  if (!isDev) {
    // In dev mode (APP_ENV=development), concurrently already started the backend.
    // In all other cases (npm start or packaged), Electron owns the backend process.
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

ipcMain.handle('get-app-version', () => {
  return app.getVersion()
})
