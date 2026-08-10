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

// Import local brand logo assets
import metaLogo from "../../assets/meta.svg";
import mistralLogo from "../../assets/mistral.svg";
import googleLogo from "../../assets/google.svg";
import qwenLogo from "../../assets/qwen.svg";
import ollamaLogo from "../../assets/ollama.svg";

interface ModelTableProps {
  models: ModelRecord[];
  loading: boolean;
  onAction: (name: string, action: "activate" | "retire") => void;
  actionError: { name: string; message: string } | null;
  onDismissError: () => void;
  /** Called for cloud-backed models (backend !== "ollama") to set/update the provider API key. */
  onSetApiKey?: (name: string) => void;
}

function getCompanyLogo(backend: string) {
  const b = backend.toLowerCase();
  let src = "";
  let alt = "";

  if (b.includes("meta")) {
    src = metaLogo;
    alt = "Meta";
  } else if (b.includes("mistral")) {
    src = mistralLogo;
    alt = "Mistral";
  } else if (b.includes("google")) {
    src = googleLogo;
    alt = "Google";
  } else if (b.includes("alibaba") || b.includes("qwen")) {
    src = qwenLogo;
    alt = "Alibaba / Qwen";
  } else if (b.includes("ollama")) {
    src = ollamaLogo;
    alt = "Ollama";
  } else {
    return null;
  }

  return (
    <img
      src={src}
      alt={`${alt} logo`}
      style={{
        width: 18,
        height: 18,
        marginRight: 10,
        display: "inline-block",
        verticalAlign: "middle",
        flexShrink: 0,
      }}
    />
  );
}

export default function ModelTable({
  models,
  loading,
  onAction,
  actionError,
  onDismissError,
  onSetApiKey,
}: ModelTableProps) {
  return (
    <div className="table-container" style={{ border: "none", borderRadius: 0, boxShadow: "none" }}>
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Version</th>
            <th>Provider</th>
            <th>Size</th>
            <th>Context</th>
            <th>Tasks</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {models.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                style={{
                  textAlign: "center",
                  color: "var(--text-muted)",
                  padding: "32px 16px",
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
                  <td>
                    <div style={{ display: "flex", alignItems: "center" }}>
                      {getCompanyLogo(model.backend)}
                      <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{model.name}</span>
                    </div>
                  </td>
                  <td style={{ color: "var(--text-muted)" }}>{model.version}</td>
                  <td style={{ color: "var(--text-muted)", textTransform: "capitalize" }}>{model.backend}</td>
                  <td style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 12.5 }}>{model.size || "8B"}</td>
                  <td style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 12.5 }}>{model.contextWindow || "8k"}</td>
                  <td style={{ color: "var(--text-muted)" }}>
                    {model.tasks.join(", ")}
                  </td>
                  <td>
                    <StatusBadge status={model.status} />
                  </td>
                  <td>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                        {/* Activate Toggle switch (shown for retired and staging) */}
                        {(model.status === "retired" || model.status === "staging") && (
                          <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            {model.status === "staging" && <span style={{ fontSize: 10.5, color: "var(--text-light)" }}>Activate:</span>}
                            <button
                              disabled={loading}
                              onClick={() => onAction(model.name, "activate")}
                              aria-label="Activate"
                              style={{
                                background: "none",
                                border: "none",
                                cursor: loading ? "not-allowed" : "pointer",
                                padding: 0,
                                display: "inline-flex",
                                alignItems: "center",
                              }}
                            >
                              <span style={{ display: "none" }}>Activate</span>
                              <div
                                style={{
                                  width: 40,
                                  height: 20,
                                  borderRadius: 10,
                                  backgroundColor: "#d1d5db",
                                  position: "relative",
                                  transition: "background-color 0.2s",
                                }}
                              >
                                <span
                                  style={{
                                    position: "absolute",
                                    top: 2,
                                    left: 2,
                                    width: 16,
                                    height: 16,
                                    borderRadius: "50%",
                                    backgroundColor: "#ffffff",
                                    boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                                    transition: "left 0.2s",
                                  }}
                                />
                              </div>
                            </button>
                          </div>
                        )}

                        {/* Retire Toggle switch (shown for active and staging) */}
                        {(model.status === "active" || model.status === "staging") && (
                          <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            {model.status === "staging" && <span style={{ fontSize: 10.5, color: "var(--text-light)" }}>Retire:</span>}
                            <button
                              disabled={loading}
                              onClick={() => onAction(model.name, "retire")}
                              aria-label="Retire"
                              style={{
                                background: "none",
                                border: "none",
                                cursor: loading ? "not-allowed" : "pointer",
                                padding: 0,
                                display: "inline-flex",
                                alignItems: "center",
                              }}
                            >
                              <span style={{ display: "none" }}>Retire</span>
                              <div
                                style={{
                                  width: 40,
                                  height: 20,
                                  borderRadius: 10,
                                  backgroundColor: "var(--primary)",
                                  position: "relative",
                                  transition: "background-color 0.2s",
                                }}
                              >
                                <span
                                  style={{
                                    position: "absolute",
                                    top: 2,
                                    left: 22,
                                    width: 16,
                                    height: 16,
                                    borderRadius: "50%",
                                    backgroundColor: "#ffffff",
                                    boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                                    transition: "left 0.2s",
                                  }}
                                />
                              </div>
                            </button>
                          </div>
                        )}

                        {/* Cloud-backend provider API key control */}
                        {model.backend.toLowerCase() !== "ollama" && onSetApiKey && (
                          <button
                            disabled={loading}
                            onClick={() => onSetApiKey(model.name)}
                            className="btn btn-outline"
                            style={{ padding: "3px 8px", fontSize: 10.5 }}
                          >
                            🔑 {model.api_key_set ? "Update key" : "Set key"}
                          </button>
                        )}
                      </div>

                      {/* Inline per-row action error */}
                      {rowError && (
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            backgroundColor: "var(--accent-red-bg)",
                            border: "1px solid var(--accent-red-border)",
                            borderRadius: 6,
                            padding: "6px 10px",
                            fontSize: 11.5,
                            color: "var(--accent-red-text)",
                            marginTop: 4,
                          }}
                        >
                          <span style={{ flex: 1, fontWeight: 500 }}>{rowError.message}</span>
                          <button
                            onClick={onDismissError}
                            aria-label="Dismiss error"
                            style={{
                              background: "none",
                              border: "none",
                              cursor: "pointer",
                              fontSize: 12,
                              color: "var(--accent-red-text)",
                              padding: 0,
                              lineHeight: 1,
                              fontWeight: "bold",
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
    </div>
  );
}
