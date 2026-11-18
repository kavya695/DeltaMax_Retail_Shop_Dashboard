import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const retailDir = path.resolve(frontendDir, "..");
const pythonCandidates = process.platform === "win32"
  ? [path.resolve(retailDir, ".venv", "Scripts", "python.exe"), path.resolve(retailDir, "..", ".venv", "Scripts", "python.exe")]
  : [path.resolve(retailDir, ".venv", "bin", "python"), path.resolve(retailDir, "..", ".venv", "bin", "python")];
const python = process.env.PYTHON || pythonCandidates.find(existsSync) || (process.platform === "win32" ? "python" : "python3");
const viteBin = path.join(frontendDir, "node_modules", "vite", "bin", "vite.js");
const apiOnly = process.argv.includes("--api-only");

const api = spawn(python, [path.join(retailDir, "api.py")], {
  cwd: retailDir,
  stdio: "inherit",
  shell: false,
});

const vite = apiOnly ? null : spawn(process.execPath, [viteBin, "--host", "127.0.0.1"], {
  cwd: frontendDir,
  stdio: "inherit",
  shell: false,
});

function shutdown() {
  if (!api.killed) api.kill();
  if (vite && !vite.killed) vite.kill();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
if (vite) vite.on("exit", (code) => {
  shutdown();
  process.exit(code ?? 0);
});

