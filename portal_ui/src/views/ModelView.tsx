/**
 * ModelView
 *
 * Model Viewer page — lists registered models and allows lifecycle actions.
 *
 * Behaviour:
 *  - Fetches model list on mount; shows LoadingSpinner while loading.
 *  - Shows ErrorBanner on top-level fetch failure (dismissible).
 *  - Shows empty-state message when list is empty after a successful fetch.
 *  - [Activate] / [Retire] buttons call PATCH /portal/models/{name}/status.
 *    On success the model list is re-fetched immediately (Req 7.4).
 *    On error an inline per-row error note is shown; the list is unchanged.
 *  - All action buttons are disabled during initial load OR while any PATCH
 *    is in flight.
 *
 * Requirements: 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 7.4
 */

import { useEffect, useState } from "react";
import type { ModelRecord } from "../types";
import { ApiError } from "../types";
import { getModels, patchModelApiKey, patchModelStatus, registerModel } from "../api/portalClient";
import ModelTable from "../components/models/ModelTable";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

export default function ModelView() {
  const [apiModels, setApiModels] = useState<ModelRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<{ status: number; message: string } | null>(
    null,
  );
  const [actionError, setActionError] = useState<{ name: string; message: string } | null>(
    null,
  );
  const [actioning, setActioning] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Register-model modal state (Phase 5 — real backend registration)
  const [showRegisterModal, setShowRegisterModal] = useState(false);
  const [regName, setRegName] = useState("");
  const [regVersion, setRegVersion] = useState("1.0");
  const [regBackend, setRegBackend] = useState("ollama");
  const [regEndpoint, setRegEndpoint] = useState("");
  const [regTasks, setRegTasks] = useState<string[]>(["chat"]);
  const [regApiKey, setRegApiKey] = useState("");
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [apiKeyActionError, setApiKeyActionError] = useState<string | null>(null);

  const ALL_TASKS = ["chat", "code", "reasoning", "summarization", "translation"];

  function toggleRegTask(task: string) {
    setRegTasks((prev) => (prev.includes(task) ? prev.filter((t) => t !== task) : [...prev, task]));
  }

  async function handleRegisterSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!regName.trim() || !regEndpoint.trim() || regTasks.length === 0) return;
    if (regBackend !== "ollama" && !regApiKey.trim()) {
      setRegisterError("A provider API key is required for non-Ollama backends.");
      return;
    }
    setRegistering(true);
    setRegisterError(null);
    try {
      await registerModel({
        name: regName.trim(),
        version: regVersion.trim() || "1.0",
        backend: regBackend,
        endpoint: regEndpoint.trim(),
        tasks: regTasks,
        status: "staging",
        api_key: regBackend !== "ollama" ? regApiKey.trim() : undefined,
      });
      setShowRegisterModal(false);
      setRegName(""); setRegVersion("1.0"); setRegBackend("ollama");
      setRegEndpoint(""); setRegTasks(["chat"]); setRegApiKey("");
      setToastMessage(
        `${regName.trim()} registered in staging. Note: it will not actually be routable until model_matrix.yaml is updated and the Router is restarted — see docs/FRONTEND_INTEGRATION.md.`,
      );
      setTimeout(() => setToastMessage(null), 6000);
      fetchModels({ silent: true });
    } catch (err) {
      setRegisterError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setRegistering(false);
    }
  }

  function handleSetApiKey(name: string) {
    const key = window.prompt(`Provider API key for ${name} (stored server-side, never shown again):`);
    if (!key || !key.trim()) return;
    setApiKeyActionError(null);
    patchModelApiKey(name, key.trim())
      .then(() => {
        setToastMessage(`API key saved for ${name}.`);
        setTimeout(() => setToastMessage(null), 3000);
        fetchModels({ silent: true });
      })
      .catch((err: unknown) => {
        setApiKeyActionError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
      });
  }

  const models = apiModels;

  // ---------------------------------------------------------------------------
  // Fetch helpers
  // ---------------------------------------------------------------------------

  function fetchModels(opts?: { silent?: boolean }): Promise<void> {
    if (!opts?.silent) setLoading(true);
    setFetchError(null);

    return getModels()
      .then((data) => {
        setApiModels(data.models);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setFetchError({ status: err.status, message: err.message });
        } else {
          setFetchError({ status: 0, message: String(err) });
        }
        setLoading(false);
      });
  }

  // Initial fetch on mount
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFetchError(null);

    getModels()
      .then((data) => {
        if (!cancelled) {
          setApiModels(data.models);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setFetchError({ status: err.status, message: err.message });
          } else {
            setFetchError({ status: 0, message: String(err) });
          }
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Action handler
  // ---------------------------------------------------------------------------

  function handleAction(name: string, action: "activate" | "retire") {
    const newStatus: ModelRecord["status"] = action === "activate" ? "active" : "retired";
    setActioning(name);
    setActionError(null);

    patchModelStatus(name, newStatus)
      .then(() => {
        // Re-fetch immediately after a successful PATCH (Req 7.4)
        return fetchModels({ silent: true });
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError ? err.message : String(err);
        setActionError({ name, message });
      })
      .finally(() => {
        setActioning(null);
      });
  }

  const buttonsDisabled = loading || actioning !== null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <h1 style={{ margin: 0 }}>Model Registry</h1>
            <span
              style={{
                backgroundColor: "rgba(16, 185, 129, 0.12)",
                color: "var(--accent-green-text)",
                fontSize: 12,
                fontWeight: 600,
                padding: "4px 10px",
                borderRadius: 12,
                border: "1px solid rgba(16, 185, 129, 0.2)",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  backgroundColor: "var(--accent-green-text)",
                  display: "inline-block",
                }}
              />
              Running in On-Prem Server
            </span>
          </div>
          <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: 4, marginBottom: 0 }}>
            List of registered and active models. Turn the status toggle ON/OFF to activate or retire.
          </p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button
            onClick={() => setShowRegisterModal(true)}
            className="btn btn-primary"
            style={{ padding: "10px 20px", fontSize: 14, fontWeight: 600 }}
          >
            Register Model
          </button>
        </div>
      </div>

      {apiKeyActionError && (
        <ErrorBanner statusCode={0} message={apiKeyActionError} onDismiss={() => setApiKeyActionError(null)} />
      )}

      {/* Top-level fetch error */}
      {fetchError && (
        <ErrorBanner
          statusCode={fetchError.status}
          message={fetchError.message}
          onDismiss={() => setFetchError(null)}
        />
      )}

      {/* Loading state — initial fetch */}
      {loading ? (
        <LoadingSpinner label="Loading models…" />
      ) : fetchError && models.length === 0 ? (
        /* Error-state: fetch failed and we have no data to show */
        <div
          className="card"
          style={{
            textAlign: "center",
            color: "var(--text-muted)",
            padding: "48px 24px",
          }}
        >
          Could not load model list. Dismiss the error above and try refreshing.
        </div>
      ) : models.length === 0 ? (
        /* Empty-state: fetch succeeded but no models returned */
        <div
          className="card"
          style={{
            textAlign: "center",
            color: "var(--text-muted)",
            padding: "48px 24px",
          }}
        >
          No models are currently registered.
        </div>
      ) : (
        /* Normal state: model table */
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <ModelTable
            models={models}
            loading={buttonsDisabled}
            onAction={handleAction}
            actionError={actionError}
            onDismissError={() => setActionError(null)}
            onSetApiKey={handleSetApiKey}
          />
        </div>
      )}

      {/* Register model modal (Phase 5 — real backend registration) */}
      {showRegisterModal && (
        <div
          style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: "rgba(0, 0, 0, 0.4)", display: "flex",
            justifyContent: "center", alignItems: "center", zIndex: 1000,
          }}
          onClick={() => setShowRegisterModal(false)}
        >
          <div
            className="card"
            style={{
              width: "100%", maxWidth: 480, padding: 24, backgroundColor: "#ffffff",
              boxShadow: "0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1)",
              display: "flex", flexDirection: "column", gap: 16, maxHeight: "90vh", overflowY: "auto",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ margin: 0 }}>Register Model</h2>
              <button
                onClick={() => setShowRegisterModal(false)}
                style={{ background: "none", border: "none", fontSize: 20, cursor: "pointer", color: "var(--text-light)" }}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleRegisterSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {registerError && <div style={{ fontSize: 12.5, color: "#ef4444" }}>{registerError}</div>}

              <div className="form-group">
                <label className="form-label">Model name</label>
                <input
                  type="text" placeholder="e.g. claude-sonnet-5" value={regName}
                  onChange={(e) => setRegName(e.target.value)} className="form-input"
                  style={{ width: "100%", padding: "10px 12px" }} required
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Version</label>
                  <input
                    type="text" value={regVersion} onChange={(e) => setRegVersion(e.target.value)}
                    className="form-input" style={{ width: "100%", padding: "10px 12px" }}
                  />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Backend</label>
                  <select
                    value={regBackend} onChange={(e) => setRegBackend(e.target.value)}
                    className="form-select" style={{ width: "100%", padding: "10px 12px" }}
                  >
                    <option value="ollama">ollama (on-prem)</option>
                    <option value="anthropic">anthropic (cloud)</option>
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Endpoint</label>
                <input
                  type="text"
                  placeholder={regBackend === "ollama" ? "http://localhost:11434" : "https://api.anthropic.com"}
                  value={regEndpoint} onChange={(e) => setRegEndpoint(e.target.value)}
                  className="form-input" style={{ width: "100%", padding: "10px 12px" }} required
                />
              </div>

              {regBackend !== "ollama" && (
                <div className="form-group">
                  <label className="form-label">Provider API key</label>
                  <input
                    type="password" placeholder="sk-ant-api03-…" value={regApiKey}
                    onChange={(e) => setRegApiKey(e.target.value)} className="form-input"
                    style={{ width: "100%", padding: "10px 12px" }}
                  />
                  <p style={{ fontSize: 11.5, color: "var(--text-light)", margin: "6px 0 0" }}>
                    Stored server-side, never shown again.
                  </p>
                </div>
              )}

              <div className="form-group">
                <label className="form-label">Supported tasks</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {ALL_TASKS.map((t) => (
                    <label key={t} style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 5 }}>
                      <input type="checkbox" checked={regTasks.includes(t)} onChange={() => toggleRegTask(t)} />
                      {t}
                    </label>
                  ))}
                </div>
              </div>

              <p style={{ fontSize: 11.5, color: "var(--text-light)", margin: 0 }}>
                Note: registering here does not make the model routable by itself —
                <code style={{ fontFamily: "var(--font-mono)" }}> model_matrix.yaml</code> must also be
                updated and the Router restarted. See docs/FRONTEND_INTEGRATION.md.
              </p>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 4 }}>
                <button
                  type="button" onClick={() => setShowRegisterModal(false)}
                  className="btn btn-secondary" style={{ padding: "10px 16px" }}
                >
                  Cancel
                </button>
                <button type="submit" className="btn" disabled={registering} style={{ padding: "10px 20px" }}>
                  {registering ? "Registering…" : "Register model"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Success Toast */}
      {toastMessage && (
        <div
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            backgroundColor: "var(--accent-green-text)",
            color: "#ffffff",
            padding: "14px 20px",
            borderRadius: 8,
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            zIndex: 1000,
            fontSize: 14.5,
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <span>✓</span>
          <span>{toastMessage}</span>
          <button
            onClick={() => setToastMessage(null)}
            style={{
              background: "none",
              border: "none",
              color: "#ffffff",
              cursor: "pointer",
              fontWeight: "bold",
              fontSize: 14,
              padding: 0,
            }}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
