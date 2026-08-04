const { contextBridge, ipcRenderer } = require('electron')

// ---------------------------------------------------------------------------
// electronAPI — general app utilities
// ---------------------------------------------------------------------------
contextBridge.exposeInMainWorld('electronAPI', {
  getAppVersion:      () => ipcRenderer.invoke('get-app-version'),
  platform:           process.platform,
  // Returns true when a downloaded update is waiting to be installed.
  // The Customers page uses this to block the MyAdmin force-sync button.
  isUpdatePending:    () => ipcRenderer.invoke('update-blocking-sync'),
})

// ---------------------------------------------------------------------------
// backendAPI — base URL for the Python FastAPI backend
// ---------------------------------------------------------------------------
contextBridge.exposeInMainWorld('backendAPI', {
  baseURL: 'http://127.0.0.1:8001'
})

// ---------------------------------------------------------------------------
// updaterAPI — all update lifecycle calls and push-event listeners
//
// Usage from React:
//   window.updaterAPI.checkForUpdates()
//   window.updaterAPI.downloadUpdate()
//   window.updaterAPI.installUpdate()
//   window.updaterAPI.getStatus()       // { updateReadyToInstall, pendingUpdateVersion }
//
//   window.updaterAPI.onUpdateAvailable(cb)     // cb({ version, releaseNotes, releaseDate })
//   window.updaterAPI.onUpdateNotAvailable(cb)  // cb({ version })
//   window.updaterAPI.onDownloadProgress(cb)    // cb({ percent, transferred, total, bytesPerSecond })
//   window.updaterAPI.onUpdateDownloaded(cb)    // cb({ version })
//   window.updaterAPI.onUpdateError(cb)         // cb({ message })
//   window.updaterAPI.removeAllListeners()      // call on component unmount
// ---------------------------------------------------------------------------
contextBridge.exposeInMainWorld('updaterAPI', {
  // ── Invoke (request → response) ─────────────────────────────────────────
  getStatus:        () => ipcRenderer.invoke('updater:get-status'),
  checkForUpdates:  () => ipcRenderer.invoke('updater:check'),
  downloadUpdate:   () => ipcRenderer.invoke('updater:download'),
  installUpdate:    () => ipcRenderer.invoke('updater:install'),

  // ── Push events from main (subscribe) ───────────────────────────────────
  onUpdateAvailable:    (cb) => ipcRenderer.on('update-available',        (_e, d) => cb(d)),
  onUpdateNotAvailable: (cb) => ipcRenderer.on('update-not-available',    (_e, d) => cb(d)),
  onDownloadProgress:   (cb) => ipcRenderer.on('update-download-progress',(_e, d) => cb(d)),
  onUpdateDownloaded:   (cb) => ipcRenderer.on('update-downloaded',       (_e, d) => cb(d)),
  onUpdateError:        (cb) => ipcRenderer.on('update-error',            (_e, d) => cb(d)),

  // ── Cleanup (call in useEffect return) ──────────────────────────────────
  removeAllListeners: () => {
    ipcRenderer.removeAllListeners('update-available')
    ipcRenderer.removeAllListeners('update-not-available')
    ipcRenderer.removeAllListeners('update-download-progress')
    ipcRenderer.removeAllListeners('update-downloaded')
    ipcRenderer.removeAllListeners('update-error')
  },
})
