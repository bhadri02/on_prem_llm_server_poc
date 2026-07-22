/**
 * AuditDetailPanel
 *
 * Overlay panel that fetches and displays all AuditEvent records for a given
 * request_id. Accessible modal dialog with aria-modal + role="dialog".
 *
 * Requirements: 4.5
 */

import { useEffect, useState } from "react";
import type { AuditEvent } from "../../types";
import { ApiError } from "../../types";
import { getAuditRequest } from "../../api/portalClient";
import ErrorBanner from "../ErrorBanner";
import LoadingSpinner from "../LoadingSpinner";

/** Formats an ISO UTC string into a neat local date + time display */
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true, timeZoneName: "short" });
  return `${date}  ·  ${time}`;
}

interface AuditDetailPanelProps {
  requestId: string;
  onClose: () => void;
}

const fieldLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: 4,
};

const fieldValue: React.CSSProperties = {
  fontSize: 13,
  color: "var(--text-main)",
  fontFamily: "var(--font-mono)",
  wordBreak: "break-all",
};

function outcomeBadgeClass(outcome: string): string {
  if (outcome === "pass") return "badge badge-green";
  if (outcome === "block") return "badge badge-red";
  if (outcome === "flag") return "badge badge-yellow";
  return "badge badge-gray";
}

function EventCard({ event }: { event: AuditEvent }) {
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius-md)",
        padding: 20,
        marginBottom: 16,
        boxShadow: "var(--shadow-sm)",
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <span className="badge badge-violet">
          {event.layer}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className={outcomeBadgeClass(event.outcome)}>
            {event.outcome.toUpperCase()}
          </span>
          <span style={{ fontSize: 11, color: "var(--text-light)", fontFamily: "var(--font-mono)" }}>
            {formatTimestamp(event.timestamp_utc)}
          </span>
        </div>
      </div>

      {/* Detail grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
          gap: "12px 16px",
        }}
      >
        <div>
          <div style={fieldLabel}>Event Type</div>
          <div style={fieldValue}>{event.event_type}</div>
        </div>
        <div>
          <div style={fieldLabel}>User ID</div>
          <div style={fieldValue}>{event.user_id}</div>
        </div>
        {event.department && (
          <div>
            <div style={fieldLabel}>Department</div>
            <div style={fieldValue}>{event.department}</div>
          </div>
        )}
        {event.model_used && (
          <div>
            <div style={fieldLabel}>Model</div>
            <div style={fieldValue}>{event.model_used}</div>
          </div>
        )}
        <div>
          <div style={fieldLabel}>Latency</div>
          <div style={fieldValue}>{event.latency_ms !== null ? `${event.latency_ms} ms` : "—"}</div>
        </div>
        {event.prompt_tokens !== null && (
          <div>
            <div style={fieldLabel}>Prompt Tokens</div>
            <div style={fieldValue}>{event.prompt_tokens}</div>
          </div>
        )}
        {event.completion_tokens !== null && (
          <div>
            <div style={fieldLabel}>Completion Tokens</div>
            <div style={fieldValue}>{event.completion_tokens}</div>
          </div>
        )}
        {event.error_code && (
          <div>
            <div style={fieldLabel}>Error Code</div>
            <div style={{ ...fieldValue, color: "var(--accent-red-text)", fontWeight: 600 }}>{event.error_code}</div>
          </div>
        )}
      </div>

      {/* Arrays */}
      {event.pii_actions.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={fieldLabel}>PII Actions</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
            {event.pii_actions.map((a, i) => (
              <span
                key={i}
                className="badge badge-yellow"
              >
                {a}
              </span>
            ))}
          </div>
        </div>
      )}
      {event.policy_decisions.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={fieldLabel}>Policy Decisions</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
            {event.policy_decisions.map((d, i) => (
              <span
                key={i}
                className="badge badge-gray"
              >
                {d}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function AuditDetailPanel({ requestId, onClose }: AuditDetailPanelProps) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEvents([]);

    getAuditRequest(requestId)
      .then((data) => {
        if (!cancelled) {
          setEvents(data.events);
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
  }, [requestId]);

  // Close on Escape key
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        className="sliding-drawer-backdrop"
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Audit detail for request ${requestId}`}
        className="sliding-drawer"
      >
        {/* Header */}
        <div className="drawer-header">
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
              Request Audit Detail
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                color: "var(--primary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontWeight: 500,
              }}
              title={requestId}
            >
              {requestId}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close detail panel"
            style={{
              background: "none",
              border: "1px solid var(--border-color)",
              borderRadius: "50%",
              cursor: "pointer",
              color: "var(--text-muted)",
              fontSize: 14,
              lineHeight: 1,
              width: 32,
              height: 32,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = "var(--primary-light)";
              e.currentTarget.style.color = "var(--primary)";
              e.currentTarget.style.borderColor = "var(--primary-border)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = "transparent";
              e.currentTarget.style.color = "var(--text-muted)";
              e.currentTarget.style.borderColor = "var(--border-color)";
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="drawer-body">
          {error && (
            <ErrorBanner
              statusCode={error.status}
              message={error.message}
              onDismiss={() => setError(null)}
            />
          )}

          {loading && <LoadingSpinner label="Loading request events…" />}

          {!loading && !error && events.length === 0 && (
            <p style={{ color: "var(--text-muted)", fontSize: 14, textAlign: "center", paddingTop: 32 }}>
              No records found for this request.
            </p>
          )}

          {!loading && events.length > 0 && (
            <>
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16, fontWeight: 500 }}>
                Found {events.length} event{events.length !== 1 ? "s" : ""} across{" "}
                {new Set(events.map((e) => e.layer)).size} layer
                {new Set(events.map((e) => e.layer)).size !== 1 ? "s" : ""}
              </p>
              {events.map((event) => (
                <EventCard key={event.audit_id} event={event} />
              ))}
            </>
          )}
        </div>
      </div>
    </>
  );
}
