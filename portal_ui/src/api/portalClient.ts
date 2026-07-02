/**
 * portalClient.ts
 *
 * Typed fetch wrapper for all Portal_API calls.
 * All paths are relative so the Vite dev proxy / nginx routes them correctly.
 * Non-2xx responses are converted to ApiError instances.
 */

import { ApiError, AuditEventList, ChatReq, MetricsSummary, ModelRecord, PortalConfig } from "../types";

// ---------------------------------------------------------------------------
// Internal helper
// ---------------------------------------------------------------------------

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message: string;
    try {
      const body = await res.json() as { message?: string };
      message = body.message ?? JSON.stringify(body);
    } catch {
      message = await res.text();
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Playground
// ---------------------------------------------------------------------------

/**
 * POST /portal/playground/chat
 *
 * Returns the raw upstream API Gateway response (any JSON shape).
 * Callers should extract `request_id` and the response content themselves.
 */
export async function postChat(req: ChatReq): Promise<unknown> {
  const res = await fetch("/portal/playground/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<unknown>(res);
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditQueryParams {
  from?: string;
  to?: string;
  limit?: number;
}

/**
 * GET /portal/audit/events
 * Accepts optional from/to (ISO-8601) and limit query parameters.
 */
export async function getAuditEvents(params: AuditQueryParams = {}): Promise<AuditEventList> {
  const query = new URLSearchParams();
  if (params.from !== undefined) query.set("from", params.from);
  if (params.to !== undefined) query.set("to", params.to);
  if (params.limit !== undefined) query.set("limit", String(params.limit));

  const qs = query.toString();
  const res = await fetch(`/portal/audit/events${qs ? `?${qs}` : ""}`);
  return handleResponse<AuditEventList>(res);
}

/**
 * GET /portal/audit/requests/{requestId}
 * Returns all AuditEvents associated with a single request_id.
 */
export async function getAuditRequest(requestId: string): Promise<AuditEventList> {
  const res = await fetch(`/portal/audit/requests/${encodeURIComponent(requestId)}`);
  return handleResponse<AuditEventList>(res);
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

/**
 * GET /portal/models
 */
export async function getModels(): Promise<{ models: ModelRecord[] }> {
  const res = await fetch("/portal/models");
  return handleResponse<{ models: ModelRecord[] }>(res);
}

/**
 * PATCH /portal/models/{name}/status
 * Updates the lifecycle status of a registered model.
 */
export async function patchModelStatus(
  name: string,
  status: ModelRecord["status"],
): Promise<ModelRecord> {
  const res = await fetch(`/portal/models/${encodeURIComponent(name)}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return handleResponse<ModelRecord>(res);
}

// ---------------------------------------------------------------------------
// Metrics & Config
// ---------------------------------------------------------------------------

/**
 * GET /portal/metrics/summary
 */
export async function getMetricsSummary(): Promise<MetricsSummary> {
  const res = await fetch("/portal/metrics/summary");
  return handleResponse<MetricsSummary>(res);
}

/**
 * GET /portal/config
 * Returns portal runtime configuration, including the Grafana embed URL.
 */
export async function getConfig(): Promise<PortalConfig> {
  const res = await fetch("/portal/config");
  return handleResponse<PortalConfig>(res);
}
