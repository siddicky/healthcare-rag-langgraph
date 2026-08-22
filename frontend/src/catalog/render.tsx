"use client";

import { useEffect, useMemo } from "react";
import { JSONUIProvider, Renderer } from "@json-render/react";
import type { UIElement } from "@json-render/core";
import { createHydrator } from "./hydrate";
import type { DataRef } from "./dataRef";
import { isDataRef } from "./dataRef";
import type { DataEnvelope } from "./envelopes";
import { isDispatchActionId } from "./dispatch";
import { DispatchProvider, type DispatchHandlers } from "./dispatch";
import { registry } from "./registry";
import {
  CATALOG_COMPONENT_NAMES,
  ComposedNodeSchema,
  ConcreteSchemas,
  WireSchemas,
  type CatalogComponentName,
} from "./schemas";
import { telemetry, type TelemetryEvent } from "./telemetry";
import { sparseToWeekStrip } from "./weekstrip";

/**
 * The fail-closed catalog render pipeline:
 *
 *   raw compose_ui tree
 *     -> per-node zod WIRE validation (fact props must be __ref objects;
 *        literals rejected; unknown components rejected)
 *     -> dispatch-id check against the FIXED map (unknown -> nothing + telemetry)
 *     -> HYDRATION against the turn's DATA envelopes (scope + block + RFC 6901
 *        pointer; cross-turn refs rejected)
 *     -> CONCRETE zod re-validation of hydrated values
 *     -> json-render flat spec -> <Renderer/>
 *
 * Any failing node renders nothing (its subtree included); siblings survive.
 */

export interface CatalogResolution {
  /** Root element keys that survived, in tree order. */
  roots: string[];
  elements: Record<string, UIElement>;
  events: TelemetryEvent[];
}

function isCatalogComponentName(value: unknown): value is CatalogComponentName {
  return typeof value === "string" && (CATALOG_COMPONENT_NAMES as readonly string[]).includes(value);
}

/** Props on each component that must come from the fixed dispatch map. */
function dispatchIdCarriers(
  component: CatalogComponentName,
  props: Record<string, unknown>,
): { prop: string; action: unknown }[] {
  switch (component) {
    case "Button":
      return [{ prop: "action", action: props.action }];
    case "MiniCalendar":
      return props.onDateSelectAction === undefined ? [] : [{ prop: "onDateSelectAction", action: props.onDateSelectAction }];
    case "ActionCard": {
      const carriers: { prop: string; action: unknown }[] = [];
      for (const key of ["primaryAction", "secondaryAction"] as const) {
        const action = props[key];
        if (typeof action === "object" && action !== null && "action" in action) {
          carriers.push({ prop: `${key}.action`, action: (action as Record<string, unknown>).action });
        }
      }
      return carriers;
    }
    default:
      return [];
  }
}

/** Post-hydration transforms that turn envelope data into renderable props. */
function transformHydrated(
  component: CatalogComponentName,
  props: Record<string, unknown>,
): Record<string, unknown> {
  if (component === "InjectionTracker" && Array.isArray(props.days)) {
    return { ...props, days: sparseToWeekStrip(props.days) };
  }
  return props;
}

export function resolveCatalogTree(
  tree: unknown,
  envelopes: readonly DataEnvelope[],
  turnScopeId: string,
): CatalogResolution {
  const hydrator = createHydrator(turnScopeId, envelopes);
  const elements: Record<string, UIElement> = {};
  const events: TelemetryEvent[] = [];
  let counter = 0;

  const roots: string[] = [];

  function issuePaths(issues: { path: (string | number | symbol)[] }[] | undefined): string[] {
    return (issues ?? []).map((i) => i.path.map(String).join("."));
  }

  function buildNode(node: unknown): string | null {
    if (typeof node !== "object" || node === null) {
      events.push({ kind: "wire_rejection", component: "(node)", issues: ["node is not an object"] });
      return null;
    }
    const shape = ComposedNodeSchema.safeParse(node);
    if (!shape.success) {
      const raw = node as Record<string, unknown>;
      if (!isCatalogComponentName(raw.component)) {
        events.push({
          kind: "unknown_component",
          component: typeof raw.component === "string" ? raw.component : "(missing)",
        });
      } else {
        events.push({ kind: "wire_rejection", component: raw.component, issues: issuePaths(shape.error.issues) });
      }
      return null;
    }
    const component = shape.data.component;
    const rawProps: Record<string, unknown> = shape.data.props ?? {};

    const wire = WireSchemas[component].safeParse(rawProps);
    if (!wire.success) {
      events.push({ kind: "wire_rejection", component, issues: issuePaths(wire.error.issues) });
      return null;
    }

    for (const carrier of dispatchIdCarriers(component, wire.data as Record<string, unknown>)) {
      if (!isDispatchActionId(carrier.action)) {
        events.push({
          kind: "unknown_dispatch",
          component,
          action: typeof carrier.action === "string" ? carrier.action : "(missing)",
        });
        return null;
      }
    }

    const hydrated: Record<string, unknown> = {};
    for (const [prop, value] of Object.entries(wire.data as Record<string, unknown>)) {
      if (!isDataRef(value)) {
        hydrated[prop] = value;
        continue;
      }
      const ref: DataRef = value;
      const resolution = hydrator.resolve(ref);
      if (!resolution.ok) {
        events.push(
          resolution.reason === "cross_turn"
            ? { kind: "cross_turn_ref", component, prop, ref }
            : { kind: "unresolved_ref", component, prop, ref },
        );
        return null;
      }
      hydrated[prop] = resolution.value;
    }

    const transformed = transformHydrated(component, hydrated);
    const concrete = ConcreteSchemas[component].safeParse(transformed);
    if (!concrete.success) {
      events.push({ kind: "hydrate_rejection", component, issues: issuePaths(concrete.error.issues) });
      return null;
    }

    const key = `n${counter}`;
    counter += 1;

    const childKeys: string[] = [];
    for (const child of shape.data.children ?? []) {
      const childKey = buildNode(child);
      if (childKey !== null) childKeys.push(childKey);
    }

    elements[key] = {
      type: component,
      props: concrete.data as Record<string, unknown>,
      ...(childKeys.length > 0 ? { children: childKeys } : {}),
    };
    return key;
  }

  const nodeList: unknown[] = Array.isArray(tree) ? tree : [tree];
  for (const node of nodeList) {
    const rootKey = buildNode(node);
    if (rootKey !== null) roots.push(rootKey);
  }

  return { roots, elements, events };
}

export interface CatalogTreeProps {
  /** The raw compose_ui tree (single node or array of nodes) from the AI tool call. */
  tree: unknown;
  /** The turn's tool DATA envelopes (all turns are fine — scope filtering happens inside). */
  envelopes: readonly DataEnvelope[];
  /** The current turn's scope id; refs outside it cannot hydrate. */
  turnScopeId: string;
  /** Client handlers for the fixed dispatch map. */
  handlers?: DispatchHandlers;
}

export function CatalogTree({ tree, envelopes, turnScopeId, handlers = {} }: CatalogTreeProps) {
  const resolution = useMemo(
    () => resolveCatalogTree(tree, envelopes, turnScopeId),
    [tree, envelopes, turnScopeId],
  );

  useEffect(() => {
    for (const event of resolution.events) telemetry(event);
  }, [resolution]);

  if (resolution.roots.length === 0) return null;

  return (
    <DispatchProvider handlers={handlers}>
      <JSONUIProvider registry={registry}>
        {resolution.roots.map((root) => (
          <Renderer key={root} spec={{ root, elements: resolution.elements }} registry={registry} />
        ))}
      </JSONUIProvider>
    </DispatchProvider>
  );
}
