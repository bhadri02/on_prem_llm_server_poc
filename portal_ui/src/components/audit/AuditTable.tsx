/**
 * AuditTable
 *
 * Renders a table of AuditEvent records.
 * The request_id column is a clickable button that calls onRequestIdClick.
 * When the events array is empty renders an empty-state message instead.
 * latency_ms shows "—" when null.
 *
 * Requirements: 4.1, 4.8
 */

import type { AuditEvent } from "../../types";

/** Formats an ISO UTC string into a neat local date + time display */
function formatTimestamp(iso: string): { date: string; time: string } {
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  const time = d.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    timeZoneName: "short",
  });
  return { date, time };
}

interface AuditTableProps {
  events: AuditEvent[];
  onRequestIdClick: (requestId: string) => void;
}

function outcomeBadgeClass(outcome: string): string {
  if (outcome === "pass") {
    return "badge badge-green";
  }
  if (outcome === "block") {
    return "badge badge-red";
  }
  if (outcome === "flag") {
    return "badge badge-yellow";
  }
  return "badge badge-gray";
}

export default function AuditTable({ events, onRequestIdClick }: AuditTableProps) {
  if (events.length === 0) {
    return (
      <p
        style={{
          padding: "32px 0",
          color: "var(--text-muted)",
          fontSize: 14.5,
          textAlign: "center",
          margin: 0,
        }}
      >
        No audit records found.
      </p>
    );
  }

  return (
    <div className="table-container" style={{ border: "none", borderRadius: 0, boxShadow: "none" }}>
      <table className="table">
        <thead>
          <tr>
            <th>Timestamp (UTC)</th>
            <th>Request ID</th>
            <th>Layer</th>
            <th>Event Type</th>
            <th>User ID</th>
            <th>Outcome</th>
            <th style={{ textAlign: "right" }}>Latency (ms)</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.audit_id}>
              <td>
                <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-main)", fontWeight: 600 }}>
                    {formatTimestamp(event.timestamp_utc).date}
                  </span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
                    {formatTimestamp(event.timestamp_utc).time}
                  </span>
                </span>
              </td>
              <td>
                <button
                  onClick={() => onRequestIdClick(event.request_id)}
                  style={{
                    background: "none",
                    border: "none",
                    padding: 0,
                    cursor: "pointer",
                    color: "var(--primary)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 12.5,
                    textDecoration: "underline",
                    textUnderlineOffset: 2,
                    fontWeight: 500,
                  }}
                  title={`View all events for request ${event.request_id}`}
                >
                  {event.request_id}
                </button>
              </td>
              <td>
                <span className="badge badge-violet">
                  {event.layer}
                </span>
              </td>
              <td style={{ fontSize: 13, color: "var(--text-muted)" }}>{event.event_type}</td>
              <td style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-muted)" }}>{event.user_id}</td>
              <td>
                <span className={outcomeBadgeClass(event.outcome)}>{event.outcome}</span>
              </td>
              <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12.5, color: "var(--text-muted)" }}>
                {event.latency_ms !== null ? event.latency_ms : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
