const { app, BrowserWindow, ipcMain, Tray, Menu } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

let mainWindow;
let tray;
let backendProcess;

const BACKEND_PORT = 8000;
const FRONTEND_PORT = 3000;
const isDev = !app.isPackaged;

function startBackend() {
  const pythonCmd = process.platform === "win32" ? "python" : "python3";
  backendProcess = spawn(pythonCmd, ["-m", "neuromancy.server.app"], {
    cwd: path.join(__dirname, ".."),
    env: { ...process.env, NEUROMANCY_PORT: String(BACKEND_PORT) },
    stdio: ["ignore", "pipe", "pipe"],
  });

  backendProcess.stdout.on("data", (data) => {
    console.log(`[backend] ${data}`);
  });

  backendProcess.stderr.on("data", (data) => {
    console.error(`[backend] ${data}`);
  });

  backendProcess.on("exit", (code) => {
    console.log(`Backend exited with code ${code}`);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: "Neuromancy",
    icon: path.join(__dirname, "..", "frontend", "public", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const url = isDev
    ? `http://localhost:${FRONTEND_PORT}`
    : `file://${path.join(__dirname, "..", "frontend", "out", "index.html")}`;

  mainWindow.loadURL(url);

  if (isDev) {
    mainWindow.webContents.openDevTools({ mode: "bottom" });
  }

  mainWindow.on("close", (e) => {
    if (tray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createTray() {
  tray = new Tray(path.join(__dirname, "..", "frontend", "public", "icon.png"));
  tray.setToolTip("Neuromancy");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Show", click: () => mainWindow.show() },
      { type: "separator" },
      {
        label: "Quit",
        click: () => {
          tray.destroy();
          tray = null;
          app.quit();
        },
      },
    ])
  );
  tray.on("double-click", () => mainWindow.show());
}

app.whenReady().then(() => {
  startBackend();
  createWindow();
  // createTray(); // Uncomment when icon.png exists
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});

// IPC handlers
ipcMain.handle("get-backend-url", () => `ws://localhost:${BACKEND_PORT}`);
ipcMain.handle("get-config", () => ({
  backendPort: BACKEND_PORT,
  frontendPort: FRONTEND_PORT,
  isDev,
}));
