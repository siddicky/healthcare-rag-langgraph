import { readFileSync } from "node:fs";
import path from "node:path";

const PIDFILE = path.join(__dirname, ".tmp", "server.pid");

export default async function globalTeardown(): Promise<void> {
  let pid: number;
  try {
    pid = Number(readFileSync(PIDFILE, "utf8").trim());
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
      return;
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250);
  }
  try {
    process.kill(pid, "SIGKILL");
  } catch {
    // already gone
  }
}
