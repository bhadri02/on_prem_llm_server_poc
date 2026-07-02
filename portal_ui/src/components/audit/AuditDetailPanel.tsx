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

interface AuditDetailPanelProps {
  requestId: string;
  onClose: () => void;
}

const fieldLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: "#64748b",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: 2,
};

const fieldValue: React.CSSProperties = {
  fontSize: 13,
  color: "#1e293b",
  fontFamily: "monospace",
  wordBreak: "break-all",
};

function EventCard({ event }: { event: AuditEvent }) {
  return (
    <div
      style={{
        background: "#f8fafc",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: 16,
        marginBottom: 12,
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <span
          style={{
            display: "inline-block",
            padding: "2px 10px",
            borderRadius: 12,
            fontSize: 11,
            fontWeight: 700,
            background: "#e0f2fe",
            color: "#0369a1",
          }}
        >
          {event.layer}
        </span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color:
              event.outcome === "pass"
                ? "#16a34a"
                : event.outcome === "block"
                ? "#dc2626"
                : "#d97706",
          }}
        >
          {event.outcome.toUpperCase()}
        </span>
        <span style={{ fontSize: 11, color: "#94a3b8", fontFamily: "monospace" }}>
          {event.timestamp_utc}
        </span>
      </div>

      {/* Detail grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
          gap: "10px 16px",
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
          <div style={fieldLabel}>Latency (ms)</div>
          <div style={fieldValue}>{event.latency_ms !== null ? event.latency_ms : "—"}</div>
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
            <div style={{ ...fieldValue, color: "#dc2626" }}>{event.error_code}</div>
          </div>
        )}
      </div>

      {/* Arrays */}
      {event.pii_actions.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={fieldLabel}>PII Actions</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
            {event.pii_actions.map((a, i) => (
              <span
                key={i}
                style={{
                  padding: "2px 8px",
                  background: "#fef3c7",
                  color: "#92400e",
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 500,
                }}
              >
                {a}
              </span>
            ))}
          </div>
        </div>
      )}
      {event.policy_decisions.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={fieldLabel}>Policy Decisions</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
            {event.policy_decisions.map((d, i) => (
              <span
                key={i}
                style={{
                  padding: "2px 8px",
                  background: "#f3f4f6",
                  color: "#374151",
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 500,
                }}
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
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15, 23, 42, 0.5)",
          zIndex: 100,
        }}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Audit detail for request ${requestId}`}
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(680px, 100vw)",
          background: "#ffffff",
          zIndex: 101,
          display: "flex",
          flexDirection: "column",
          boxShadow: "-4px 0 24px rgba(0,0,0,0.15)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 20px",
            borderBottom: "1px solid #e2e8f0",
            background: "#1e293b",
            gap: 12,
            flexShrink: 0,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 2 }}>
              Audit Detail — Request ID
            </div>
            <div
              style={{
                fontFamily: "monospace",
                fontSize: 13,
                color: "#60a5fa",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
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
              border: "1px solid #475569",
              borderRadius: 6,
              cursor: "pointer",
              color: "#cbd5e1",
              fontSize: 18,
              lineHeight: 1,
              padding: "4px 10px",
              flexShrink: 0,
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
          {error && (
            <ErrorBanner
              statusCode={error.status}
              message={error.message}
              onDismiss={() => setError(null)}
            />
          )}

          {loading && <LoadingSpinner label="Loading request events…" />}

          {!loading && !error && events.length === 0 && (
            <p style={{ color: "#64748b", fontSize: 14, textAlign: "center", paddingTop: 24 }}>
              No records found for this request.
            </p>
          )}

          {!loading && events.length > 0 && (
            <>
              <p style={{ fontSize: 12, color: "#64748b", marginBottom: 12 }}>
                {events.length} event{events.length !== 1 ? "s" : ""} across{" "}
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
