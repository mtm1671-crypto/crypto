const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("neuromancy", {
  getBackendUrl: () => ipcRenderer.invoke("get-backend-url"),
  getConfig: () => ipcRenderer.invoke("get-config"),
});
