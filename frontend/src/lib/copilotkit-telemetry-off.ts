/**
 * CopilotKit telemetry kill switch. $0 CopilotKit spend is a hard plan
 * guardrail: the v2 runtime fires an `oss.runtime.instance_created` telemetry
 * event (sampled) when the handler is constructed unless one of these env
 * flags is already set — the telemetry client reads them ONCE, at module load,
 * so this module MUST be imported before any `@copilotkit/*` import.
 */
process.env.COPILOTKIT_TELEMETRY_DISABLED ??= "true";
process.env.DO_NOT_TRACK ??= "1";

export {};
