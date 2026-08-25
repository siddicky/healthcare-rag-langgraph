import { spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

const E2E_DIR = __dirname;
const REPO_ROOT = path.resolve(E2E_DIR, "..", "..");
const TMP_DIR = path.join(E2E_DIR, ".tmp");
const RUNFILE = path.join(TMP_DIR, "run.json");
const PIDFILE = path.join(TMP_DIR, "server.pid");
const SERVER_LOG = path.join(TMP_DIR, "server-stdout.log");
const BOOT_TIMEOUT_MS = 600_000;

interface Runfile {
  ready: boolean;
  dep_url: string;
  server_url: string;
  frontend_url: string;
}

function waitUntilReady(timeoutMs: number): Runfile {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const run = JSON.parse(readFileSync(RUNFILE, "utf8")) as Runfile;
      if (run.ready === true) return run;
    } catch {
      // not written yet
    }
    if (Date.now() > deadline) {
      throw new Error(`e2e stack did not become ready within ${timeoutMs}ms`);
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 500);
  }
}

export default async function globalSetup(): Promise<void> {
  mkdirSync(TMP_DIR, { recursive: true });
  rmSync(RUNFILE, { force: true });
  const log = writeFileSync.bind(null, SERVER_LOG);
  log("");

  const child: ChildProcess = spawn(
    path.join(REPO_ROOT, ".venv", "bin", "python"),
    [path.join(E2E_DIR, "server.py"), "--runfile", RUNFILE],
    { cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"] },
  );
  let buffered = "";
  child.stdout?.on("data", (chunk: Buffer) => {
    buffered += chunk.toString();
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    buffered += chunk.toString();
  });
  const exited = new Promise<number | null>((resolve) => {
    child.on("exit", (code) => resolve(code));
  });

  try {
    const run = waitUntilReady(BOOT_TIMEOUT_MS);
    writeFileSync(PIDFILE, String(child.pid));
    process.env.COACH_E2E_RUNFILE = RUNFILE;
    process.env.COACH_E2E_BASE_URL = run.frontend_url;
    console.log(`e2e stack ready: ${run.frontend_url}`);
  } catch (error) {
    child.kill("SIGTERM");
    await exited;
    writeFileSync(SERVER_LOG, buffered);
    throw new Error(
      `e2e stack failed to boot: ${String(error)}\nserver output:\n${buffered}`,
    );
  }
}
