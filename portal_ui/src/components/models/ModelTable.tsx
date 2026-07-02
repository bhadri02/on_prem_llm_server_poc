/**
 * ModelTable
 *
 * Renders the registered model list as a table.
 *
 * Columns: Name, Version, Backend, Tasks, Status, Actions
 *
 * Button rules:
 *   active  → [Retire] only
 *   retired → [Activate] only
 *   staging → [Retire] + [Activate]
 *
 * All action buttons are disabled while `loading` is true.
 * When `actionError` is set and its `name` matches a row's model name,
 * a small inline error note is shown beneath that row's buttons.
 *
 * Requirements: 6.1, 6.2, 6.5, 6.6
 */

import type { ModelRecord } from "../../types";
import StatusBadge from "./StatusBadge";

interface ModelTableProps {
  models: ModelRecord[];
  loading: boolean;
  onAction: (name: string, action: "activate" | "retire") => void;
  actionError: { name: string; message: string } | null;
  onDismissError: () => void;
}

const CELL_STYLE: React.CSSProperties = {
  padding: "10px 14px",
  borderBottom: "1px solid #f1f5f9",
  verticalAlign: "top",
};

const HEADER_STYLE: React.CSSProperties = {
  ...CELL_STYLE,
  background: "#f8fafc",
  fontWeight: 600,
  color: "#475569",
  fontSize: 13,
  borderBottom: "1px solid #e5e7eb",
  textAlign: "left",
};

export default function ModelTable({
  models,
  loading,
  onAction,
  actionError,
  onDismissError,
}: ModelTableProps) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={HEADER_STYLE}>Name</th>
          <th style={HEADER_STYLE}>Version</th>
          <th style={HEADER_STYLE}>Backend</th>
          <th style={HEADER_STYLE}>Tasks</th>
          <th style={HEADER_STYLE}>Status</th>
          <th style={HEADER_STYLE}>Actions</th>
        </tr>
      </thead>
      <tbody>
        {models.length === 0 ? (
          <tr>
            <td
              colSpan={6}
              style={{
                ...CELL_STYLE,
                textAlign: "center",
                color: "#94a3b8",
                padding: "24px 14px",
              }}
            >
              No models found.
            </td>
          </tr>
        ) : (
          models.map((model) => {
            const rowError =
              actionError && actionError.name === model.name ? actionError : null;

            return (
              <tr key={model.name}>
                <td style={CELL_STYLE}>
                  <span style={{ fontWeight: 600, color: "#1e293b" }}>{model.name}</span>
                </td>
                <td style={{ ...CELL_STYLE, color: "#475569" }}>{model.version}</td>
                <td style={{ ...CELL_STYLE, color: "#475569" }}>{model.backend}</td>
                <td style={{ ...CELL_STYLE, color: "#475569" }}>
                  {model.tasks.join(", ")}
                </td>
                <td style={CELL_STYLE}>
                  <StatusBadge status={model.status} />
                </td>
                <td style={CELL_STYLE}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <div style={{ display: "flex", gap: 6 }}>
                      {/* [Activate] — shown for retired and staging */}
                      {(model.status === "retired" || model.status === "staging") && (
                        <button
                          disabled={loading}
                          onClick={() => onAction(model.name, "activate")}
                          style={{
                            cursor: loading ? "not-allowed" : "pointer",
                            border: "none",
                            padding: "4px 12px",
                            borderRadius: 4,
                            fontSize: 12,
                            background: loading ? "#bbf7d0" : "#22c55e",
                            color: "#fff",
                            opacity: loading ? 0.6 : 1,
                          }}
                        >
                          Activate
                        </button>
                      )}

                      {/* [Retire] — shown for active and staging */}
                      {(model.status === "active" || model.status === "staging") && (
                        <button
                          disabled={loading}
                          onClick={() => onAction(model.name, "retire")}
                          style={{
                            cursor: loading ? "not-allowed" : "pointer",
                            border: "none",
                            padding: "4px 12px",
                            borderRadius: 4,
                            fontSize: 12,
                            background: loading ? "#cbd5e1" : "#94a3b8",
                            color: "#fff",
                            opacity: loading ? 0.6 : 1,
                          }}
                        >
                          Retire
                        </button>
                      )}
                    </div>

                    {/* Inline per-row action error */}
                    {rowError && (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          background: "#fee2e2",
                          border: "1px solid #fca5a5",
                          borderRadius: 4,
                          padding: "3px 8px",
                          fontSize: 11,
                          color: "#b91c1c",
                          marginTop: 2,
                        }}
                      >
                        <span style={{ flex: 1 }}>{rowError.message}</span>
                        <button
                          onClick={onDismissError}
                          aria-label="Dismiss error"
                          style={{
                            background: "none",
                            border: "none",
                            cursor: "pointer",
                            fontSize: 12,
                            color: "#b91c1c",
                            padding: 0,
                            lineHeight: 1,
                          }}
                        >
                          ✕
                        </button>
                      </div>
                    )}
                  </div>
                </td>
              </tr>
            );
          })
        )}
      </tbody>
    </table>
  );
}
