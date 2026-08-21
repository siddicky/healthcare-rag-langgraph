/**
 * Client-local thread titles (localStorage) — the server never stores a
 * display title for member threads.
 */

const STORAGE_KEY = "nymble:thread-titles";

function readMap(): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return {};
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "string") out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

function writeMap(titles: Record<string, string>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(titles));
  } catch {
    // storage unavailable (private mode) — titles stay session-local
  }
}

export function getThreadTitle(threadId: string): string | null {
  return readMap()[threadId] ?? null;
}

export function setThreadTitle(threadId: string, title: string): void {
  if (title.trim() === "") return;
  const titles = readMap();
  if (titles[threadId] === title.trim()) return;
  titles[threadId] = title.trim();
  writeMap(titles);
}

export function deriveTitle(question: string): string {
  const trimmed = question.trim();
  return trimmed.length <= 32 ? trimmed : `${trimmed.slice(0, 32)}…`;
}

export function clearThreadTitles(threadIds: readonly string[]): void {
  const titles = readMap();
  for (const id of threadIds) delete titles[id];
  writeMap(titles);
}
