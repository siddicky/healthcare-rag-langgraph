import "@testing-library/jest-dom/vitest";

// jsdom 30 delegates window.localStorage/sessionStorage to Node's
// experimental webstorage, which stays undefined unless node runs with
// --localstorage-file. Install an in-memory fallback so component tests
// always have a working Storage regardless of how node was launched.
for (const name of ["localStorage", "sessionStorage"] as const) {
  if (typeof window === "undefined") break;
  const holder = window as unknown as Record<string, unknown>;
  if (holder[name]) continue;
  Reflect.deleteProperty(window, name);
  const backing = new Map<string, string>();
  const storage: Storage = {
    get length(): number {
      return backing.size;
    },
    clear: (): void => {
      backing.clear();
    },
    getItem: (key: string): string | null =>
      backing.has(key) ? (backing.get(key) as string) : null,
    key: (index: number): string | null =>
      Array.from(backing.keys())[index] ?? null,
    removeItem: (key: string): void => {
      backing.delete(key);
    },
    setItem: (key: string, value: string): void => {
      backing.set(key, String(value));
    },
  };
  Object.defineProperty(window, name, {
    value: storage,
    configurable: true,
    writable: false,
  });
}
