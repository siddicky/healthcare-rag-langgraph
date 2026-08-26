import { existsSync, readFileSync, rmSync } from "node:fs";
import path from "node:path";

const PIDFILE = path.join(__dirname, ".tmp", "server.pid");
const RESTART_PIDFILE = path.join(__dirname, ".tmp", "frontend-restart.pid");

function stop(pidfile: string): void {
  let pid: number;
  try {
    pid = Number(readFileSync(pidfile, "utf8").trim());
  } catch {
    return;
  }
  if (!Number.isInteger(pid) || pid <= 0) return;
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return;
  }
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      process.kill(pid, 0);
    } catch {
      rmSync(pidfile, { force: true });
      return;
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250);
  }
  try {
    process.kill(pid, "SIGKILL");
  } catch {
    // already gone
  }
  rmSync(pidfile, { force: true });
}

export default async function globalTeardown(): Promise<void> {
  // A restart scenario may have replaced the orchestrated `next start` with
  // its own process; it is NOT a child of server.py, so kill it explicitly.
  if (existsSync(RESTART_PIDFILE)) stop(RESTART_PIDFILE);
  stop(PIDFILE);
}
