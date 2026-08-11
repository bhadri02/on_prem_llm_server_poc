/**
 * GovernanceView
 *
 * AI governance / security / usage dashboard, sourced from
 * GET /portal/governance/summary (admin_portal → audit_store's real audit
 * trail) — blocked-request / injection / PII / policy-denial counts and
 * token usage, all from live pipeline enforcement, not demo data.
 */

import { useEffect, useState } from "react";
import { GovernanceSummary } from "../types";
import { ApiError } from "../types";
import { getGovernanceSummary } from "../api/portalClient";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

type TimeRange = "24h" | "7d" | "30d" | "all";

const RANGE_LABELS: Record<TimeRange, string> = {
  "24h": "Last 24 Hours",
  "7d": "Last 7 Days",
  "30d": "Last 30 Days",
  all: "All Time",
};

function rangeToFrom(range: TimeRange): string | undefined {
  if (range === "all") return undefined;
  const hours = range === "24h" ? 24 : range === "7d" ? 24 * 7 : 24 * 30;
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

function formatNumber(num: number): string {
  if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
  if (num >= 1000) return (num / 1000).toFixed(1) + "k";
  return num.toString();
}

const REASON_LABELS: Record<string, string> = {
  injection_detected: "Prompt Injection",
  content_safety_violation: "Content Safety Violation",
  policy_denied: "Role/Task Policy Denied",
  model_not_entitled: "Model Not Entitled",
};

function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

export default function GovernanceView() {
  const [range, setRange] = useState<TimeRange>("7d");
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const from = rangeToFrom(range);
    getGovernanceSummary(from ? { from } : {})
      .then((data) => {
        if (!cancelled) {
          setSummary(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError({ status: err.status, message: err.message });
          } else {
            setError({ status: 0, message: String(err) });
          }
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [range]);

  const passCount = summary?.by_outcome["pass"] ?? 0;
  const blockCount = summary?.requests_blocked_total ?? 0;
  const errorCount = summary?.by_outcome["error"] ?? 0;
  const totalOutcomes = passCount + blockCount + errorCount;

  const reasonEntries = summary
    ? Object.entries(summary.blocked_by_reason).sort((a, b) => b[1] - a[1])
    : [];
  const maxReasonCount = Math.max(1, ...reasonEntries.map(([, count]) => count));

  const modelEntries = summary
    ? Object.entries(summary.model_usage).sort((a, b) => b[1] - a[1])
    : [];
  const maxModelCount = Math.max(1, ...modelEntries.map(([, count]) => count));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16 }}>
        <div>
          <h1>AI Governance &amp; Security</h1>
          <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: -16, marginBottom: 0 }}>
            Blocked requests, guardrail triggers, PII detections, and token usage — computed
            directly from the audit trail.
          </p>
        </div>

        <div style={{ display: "flex", background: "#ffffff", padding: 4, borderRadius: 8, border: "1px solid var(--border-color)", boxShadow: "var(--shadow-sm)" }}>
          {(Object.keys(RANGE_LABELS) as TimeRange[]).map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              style={{
                border: "none",
                background: range === r ? "var(--primary-light)" : "transparent",
                color: range === r ? "var(--primary)" : "var(--text-muted)",
                padding: "6px 14px",
                borderRadius: 6,
                cursor: "pointer",
                fontWeight: 600,
                fontSize: 12.5,
                transition: "all 0.15s ease",
              }}
            >
              {RANGE_LABELS[r]}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <ErrorBanner statusCode={error.status} message={error.message} onDismiss={() => setError(null)} />
      )}

      {loading ? (
        <LoadingSpinner label="Loading governance summary…" />
      ) : summary ? (
        <>
          {/* Top-line metrics */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 20 }}>
            <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Total Requests
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text-main)" }}>{formatNumber(totalOutcomes)}</div>
              <div style={{ fontSize: 12, color: "var(--text-light)" }}>
                {passCount} passed · {errorCount} errored
              </div>
            </div>

            <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Requests Blocked
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, color: "var(--danger, #dc2626)" }}>{formatNumber(blockCount)}</div>
              <div style={{ fontSize: 12, color: "var(--text-light)" }}>
                {totalOutcomes > 0 ? ((blockCount / totalOutcomes) * 100).toFixed(1) : "0"}% of all requests
              </div>
            </div>

            <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Prompt Injection Flags
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, color: "var(--danger, #dc2626)" }}>{formatNumber(summary.injection_flagged_total)}</div>
              <div style={{ fontSize: 12, color: "var(--text-light)" }}>Blocked before reaching the model</div>
            </div>

            <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                PII Entities Masked
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text-main)" }}>{formatNumber(summary.pii_detections_total)}</div>
              <div style={{ fontSize: 12, color: "var(--text-light)" }}>Across request and response content</div>
            </div>

            <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Total Tokens
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, color: "var(--primary)" }}>{formatNumber(summary.token_usage.total_tokens)}</div>
              <div style={{ fontSize: 12, color: "var(--text-light)" }}>
                {formatNumber(summary.token_usage.prompt_tokens)} prompt · {formatNumber(summary.token_usage.completion_tokens)} completion
              </div>
            </div>
          </div>

          {/* Charts grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
            {/* Blocked-by-reason breakdown */}
            <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              <h2 style={{ margin: 0 }}>Guardrail Trigger Reasons</h2>
              {reasonEntries.length === 0 ? (
                <p style={{ color: "var(--text-muted)", fontSize: 13.5 }}>No blocked requests in this window.</p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                  {reasonEntries.map(([reason, count]) => {
                    const pct = (count / maxReasonCount) * 100;
                    return (
                      <div key={reason} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
                          <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{reasonLabel(reason)}</span>
                          <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{count}</span>
                        </div>
                        <div style={{ height: 12, width: "100%", background: "#f3f4f6", borderRadius: 6, overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${pct}%`,
                              height: "100%",
                              background: "var(--danger, #dc2626)",
                              transition: "width 0.4s ease-out",
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Per-model usage */}
            <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              <h2 style={{ margin: 0 }}>Requests Served by Model</h2>
              {modelEntries.length === 0 ? (
                <p style={{ color: "var(--text-muted)", fontSize: 13.5 }}>No successfully served requests in this window.</p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                  {modelEntries.map(([model, count]) => {
                    const pct = (count / maxModelCount) * 100;
                    return (
                      <div key={model} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
                          <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{model}</span>
                          <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{count}</span>
                        </div>
                        <div style={{ height: 12, width: "100%", background: "#f3f4f6", borderRadius: 6, overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${pct}%`,
                              height: "100%",
                              background: "var(--primary)",
                              transition: "width 0.4s ease-out",
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Layer breakdown */}
          <div className="card" style={{ padding: 20 }}>
            <h2 style={{ margin: "0 0 16px 0" }}>Events by Layer</h2>
            <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
              {Object.entries(summary.by_layer).map(([layer, count]) => (
                <div key={layer} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    {layer}
                  </span>
                  <span style={{ fontSize: 20, fontWeight: 700, color: "var(--text-main)" }}>{count}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
