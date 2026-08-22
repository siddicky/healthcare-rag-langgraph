"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { telemetry } from "./telemetry";

/**
 * The FIXED dispatch map. Catalog `Button` (and action-bearing cards) carry an
 * `action` string from this closed list; the chat app registers the real
 * client handlers. Unknown ids fail closed (nothing renders + telemetry);
 * known-but-unregistered ids no-op with telemetry.
 */
export const DISPATCH_ACTIONS = [
  "log_weight",
  "log_injection",
  "view_schedule",
  "change_schedule",
  "set_reminder",
  "cancel_reminder",
  "upload_document",
  "confirm",
  "decline",
] as const;

export type DispatchActionId = (typeof DISPATCH_ACTIONS)[number];

export type DispatchHandler = () => void | Promise<void>;
export type DispatchHandlers = Partial<Record<DispatchActionId, DispatchHandler>>;

const DISPATCH_ACTION_IDS: readonly string[] = DISPATCH_ACTIONS;

export function isDispatchActionId(value: unknown): value is DispatchActionId {
  return typeof value === "string" && DISPATCH_ACTION_IDS.includes(value);
}

interface DispatchContextValue {
  handlers: DispatchHandlers;
}

const DispatchContext = createContext<DispatchContextValue>({ handlers: {} });

export function DispatchProvider({
  handlers,
  children,
}: {
  handlers: DispatchHandlers;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ handlers }), [handlers]);
  return <DispatchContext.Provider value={value}>{children}</DispatchContext.Provider>;
}

/** Resolve an action id at click time: fixed map or no-op, never a crash. */
export function useDispatchAction(): (action: string) => void {
  const { handlers } = useContext(DispatchContext);
  return (action: string) => {
    if (!isDispatchActionId(action)) {
      telemetry({ kind: "unknown_dispatch", component: "dispatch", action });
      return;
    }
    const handler = handlers[action];
    if (!handler) {
      telemetry({ kind: "unregistered_dispatch", component: "dispatch", action });
      return;
    }
    void handler();
  };
}
