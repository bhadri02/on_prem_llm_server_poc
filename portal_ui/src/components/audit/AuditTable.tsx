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

interface AuditTableProps {
  events: AuditEvent[];
  onRequestIdClick: (requestId: string) => void;
}

const thStyle: React.CSSProperties = {
  padding: "10px 12px",
  textAlign: "left",
  fontSize: 12,
  fontWeight: 600,
  color: "#64748b",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  borderBottom: "2px solid #e2e8f0",
  background: "#f8fafc",
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "10px 12px",
  fontSize: 13,
  color: "#1e293b",
  borderBottom: "1px solid #e2e8f0",
  verticalAlign: "middle",
};

function outcomeColor(outcome: string): React.CSSProperties {
  if (outcome === "pass") {
    return { color: "#16a34a", fontWeight: 600 };
  }
  if (outcome === "block") {
    return { color: "#dc2626", fontWeight: 600 };
  }
  if (outcome === "flag") {
    return { color: "#d97706", fontWeight: 600 };
  }
  return {};
}

export default function AuditTable({ events, onRequestIdClick }: AuditTableProps) {
  if (events.length === 0) {
    return (
      <p
        style={{
          padding: "24px 0",
          color: "#64748b",
          fontSize: 14,
          textAlign: "center",
        }}
      >
        No audit records found.
      </p>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          background: "#ffffff",
          borderRadius: 8,
          overflow: "hidden",
          boxShadow: "0 1px 3px rgba(0,0,0,0.07)",
        }}
      >
        <thead>
          <tr>
            <th style={thStyle}>Timestamp (UTC)</th>
            <th style={thStyle}>Request ID</th>
            <th style={thStyle}>Layer</th>
            <th style={thStyle}>Event Type</th>
            <th style={thStyle}>User ID</th>
            <th style={thStyle}>Outcome</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Latency (ms)</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr
              key={event.audit_id}
              style={{ transition: "background 0.1s" }}
              onMouseEnter={(e) =>
                ((e.currentTarget as HTMLTableRowElement).style.background = "#f8fafc")
              }
              onMouseLeave={(e) =>
                ((e.currentTarget as HTMLTableRowElement).style.background = "")
              }
            >
              <td style={tdStyle}>
                <span style={{ fontFamily: "monospace", fontSize: 12 }}>
                  {event.timestamp_utc}
                </span>
              </td>
              <td style={tdStyle}>
                <button
                  onClick={() => onRequestIdClick(event.request_id)}
                  style={{
                    background: "none",
                    border: "none",
                    padding: 0,
                    cursor: "pointer",
                    color: "#2563eb",
                    fontFamily: "monospace",
                    fontSize: 12,
                    textDecoration: "underline",
                    textUnderlineOffset: 2,
                  }}
                  title={`View all events for request ${event.request_id}`}
                >
                  {event.request_id}
                </button>
              </td>
              <td style={tdStyle}>
                <span
                  style={{
                    display: "inline-block",
                    padding: "2px 8px",
                    borderRadius: 12,
                    fontSize: 11,
                    fontWeight: 600,
                    background: "#e0f2fe",
                    color: "#0369a1",
                  }}
                >
                  {event.layer}
                </span>
              </td>
              <td style={{ ...tdStyle, fontSize: 12, color: "#475569" }}>{event.event_type}</td>
              <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 12 }}>{event.user_id}</td>
              <td style={tdStyle}>
                <span style={outcomeColor(event.outcome)}>{event.outcome}</span>
              </td>
              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", fontSize: 12 }}>
                {event.latency_ms !== null ? event.latency_ms : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
