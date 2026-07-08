const { contextBridge, ipcRenderer } = require('electron')

// Expose safe APIs to the React frontend
contextBridge.exposeInMainWorld('electronAPI', {
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  platform: process.platform
})

// Expose backend URL to React
contextBridge.exposeInMainWorld('backendAPI', {
  baseURL: 'http://127.0.0.1:8001'
})
