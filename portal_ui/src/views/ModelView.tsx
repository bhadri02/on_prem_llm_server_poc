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

import { useEffect, useRef, useState } from "react";
import type { ModelRecord } from "../types";
import { ApiError } from "../types";
import { getModels, patchModelApiKey, patchModelStatus, registerModel } from "../api/portalClient";
import ModelTable from "../components/models/ModelTable";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";
import { INITIAL_DUMMY_MODELS } from "../data/models";


const REQUESTABLE_MODELS = [
  { name: "llama-3.1-80b", label: "llama-3.1-80b (Meta)" },
  { name: "gpt-oss-120b", label: "gpt-oss-120b (OpenAI)" },
  { name: "gpt-oss-20b", label: "gpt-oss-20b (OpenAI)" },
  { name: "mistral-large-2", label: "mistral-large-2 (Mistral)" },
  { name: "gemma-2-27b", label: "gemma-2-27b (Google)" },
  { name: "qwen-2.5-72b", label: "qwen-2.5-72b (Alibaba)" },
];

export default function ModelView() {
  const [apiModels, setApiModels] = useState<ModelRecord[]>([]);
  const [dummyModels, setDummyModels] = useState<ModelRecord[]>(INITIAL_DUMMY_MODELS);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<{ status: number; message: string } | null>(
    null,
  );
  const [actionError, setActionError] = useState<{ name: string; message: string } | null>(
    null,
  );
  const [actioning, setActioning] = useState<string | null>(null);

  // Request access modal state
  const [showRequestModal, setShowRequestModal] = useState(false);
  const [requestModelName, setRequestModelName] = useState("");
  const [requestUserName, setRequestUserName] = useState("");
  const [requestUserEmail, setRequestUserEmail] = useState("");
  const [requestReason, setRequestReason] = useState("");
  const [customModelName, setCustomModelName] = useState("");
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [requestDropdownOpen, setRequestDropdownOpen] = useState(false);
  const requestDropdownRef = useRef<HTMLDivElement>(null);

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

  // Close request dropdown on outside click
  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      if (requestDropdownRef.current && !requestDropdownRef.current.contains(e.target as Node)) {
        setRequestDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, []);

  const isOpenSource = (m: ModelRecord) => {
    const name = m.name.toLowerCase();
    const backend = m.backend.toLowerCase();
    if (name.includes("gpt") || name.includes("claude") || name.includes("gemini")) {
      return false;
    }
    if (backend === "openai" || backend === "anthropic") {
      return false;
    }
    if (backend === "google" && !name.includes("gemma")) {
      return false;
    }
    return true;
  };

  // Real registered models (apiModels) are always shown regardless of backend —
  // including cloud/Anthropic ones registered via the modal below. The
  // open-source filter only applies to the simulated "request access" catalog
  // (dummyModels), which predates real cloud-model registration.
  const models = [...apiModels, ...dummyModels.filter(isOpenSource)];

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

    const isDummy = dummyModels.some((m) => m.name === name);
    if (isDummy) {
      setTimeout(() => {
        setDummyModels((prev) =>
          prev.map((m) => (m.name === name ? { ...m, status: newStatus } : m))
        );
        setActioning(null);
      }, 200);
      return;
    }

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

  function handleRequestSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!requestModelName || !requestUserName || !requestUserEmail || !requestReason) return;

    let newModel: ModelRecord;
    let modelNameForToast = requestModelName;
    if (requestModelName === "llama-3.1-80b") {
      newModel = { name: "llama-3.1-80b", version: "3.1.0", backend: "meta", tasks: ["reasoning"], status: "pending", size: "80B", contextWindow: "128k" };
    } else if (requestModelName === "gpt-oss-120b") {
      newModel = { name: "gpt-oss-120b", version: "1.0.0", backend: "openai", tasks: ["chat"], status: "pending", size: "120B", contextWindow: "128k" };
    } else if (requestModelName === "gpt-oss-20b") {
      newModel = { name: "gpt-oss-20b", version: "1.0.0", backend: "openai", tasks: ["chat"], status: "pending", size: "20B", contextWindow: "128k" };
    } else if (requestModelName === "mistral-large-2") {
      newModel = { name: "mistral-large-2", version: "2.0.0", backend: "mistral", tasks: ["code"], status: "pending", size: "123B", contextWindow: "128k" };
    } else if (requestModelName === "gemma-2-27b") {
      newModel = { name: "gemma-2-27b", version: "2.0.0", backend: "google", tasks: ["summarization"], status: "pending", size: "27B", contextWindow: "8k" };
    } else if (requestModelName === "qwen-2.5-72b") {
      newModel = { name: "qwen-2.5-72b", version: "2.5.0", backend: "alibaba", tasks: ["chat"], status: "pending", size: "72B", contextWindow: "128k" };
    } else {
      const nameToUse = customModelName.trim() || "requested-custom-model";
      newModel = { name: nameToUse, version: "1.0.0", backend: "custom", tasks: ["chat"], status: "pending", size: "8B", contextWindow: "8k" };
      modelNameForToast = nameToUse;
    }

    setDummyModels((prev) => {
      const exists = prev.some((m) => m.name === newModel.name);
      if (exists) {
        return prev.map((m) => (m.name === newModel.name ? { ...m, status: "pending" } : m));
      } else {
        return [...prev, newModel];
      }
    });

    setToastMessage(`Request for access to ${modelNameForToast} submitted successfully!`);
    setShowRequestModal(false);

    setRequestModelName("");
    setRequestUserName("");
    setRequestUserEmail("");
    setRequestReason("");
    setCustomModelName("");

    setTimeout(() => {
      setToastMessage(null);
    }, 4000);
  }

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
          <button
            onClick={() => setShowRequestModal(true)}
            className="btn"
            style={{ padding: "10px 20px", fontSize: 14, fontWeight: 600 }}
          >
            Request Model Access
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

      {/* Request access modal */}
      {showRequestModal && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0, 0, 0, 0.4)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            zIndex: 1000,
          }}
          onClick={() => setShowRequestModal(false)}
        >
          <div
            className="card"
            style={{
              width: "100%",
              maxWidth: 480,
              padding: 24,
              backgroundColor: "#ffffff",
              boxShadow: "0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1)",
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ margin: 0 }}>Request Model Access</h2>
              <button
                onClick={() => setShowRequestModal(false)}
                style={{
                  background: "none",
                  border: "none",
                  fontSize: 20,
                  cursor: "pointer",
                  color: "var(--text-light)",
                }}
              >
                ✕
              </button>
            </div>
            
            <form onSubmit={handleRequestSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div className="form-group">
                <label className="form-label">Select Model</label>
                <div ref={requestDropdownRef} style={{ position: "relative", width: "100%" }}>
                  {/* Trigger button */}
                  <button
                    type="button"
                    onClick={() => setRequestDropdownOpen((o) => !o)}
                    style={{
                      width: "100%",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 8,
                      padding: "10px 12px 10px 14px",
                      borderRadius: 6,
                      border: `1.5px solid ${requestDropdownOpen ? "var(--primary)" : "var(--border-color)"}`,
                      background: requestDropdownOpen ? "#fdfcff" : "#ffffff",
                      color: requestModelName ? "var(--text-main)" : "var(--text-light)",
                      fontSize: 14,
                      fontFamily: "inherit",
                      fontWeight: requestModelName ? 500 : 400,
                      cursor: "pointer",
                      boxShadow: requestDropdownOpen ? "0 0 0 3px rgba(124,58,237,0.12)" : "none",
                      transition: "border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease",
                      outline: "none",
                    }}
                  >
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {requestModelName
                        ? (REQUESTABLE_MODELS.find((m) => m.name === requestModelName)?.label ?? requestModelName)
                        : "-- Choose a Model --"}
                    </span>
                    <svg
                      width="16" height="16" viewBox="0 0 24 24" fill="none"
                      stroke="var(--primary)" strokeWidth="2.2"
                      strokeLinecap="round" strokeLinejoin="round"
                      style={{
                        flexShrink: 0,
                        transform: requestDropdownOpen ? "rotate(180deg)" : "rotate(0deg)",
                        transition: "transform 0.22s ease",
                      }}
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </button>

                  {/* Smooth animated panel */}
                  <ul
                    role="listbox"
                    style={{
                      position: "absolute",
                      top: "calc(100% + 6px)",
                      left: 0,
                      right: 0,
                      zIndex: 300,
                      margin: 0,
                      padding: "4px 0",
                      listStyle: "none",
                      background: "#ffffff",
                      border: "1.5px solid var(--primary-border)",
                      borderRadius: 8,
                      boxShadow: "0 8px 24px -4px rgba(124,58,237,0.14), 0 2px 8px -2px rgba(124,58,237,0.08)",
                      overflow: "hidden",
                      opacity: requestDropdownOpen ? 1 : 0,
                      transform: requestDropdownOpen ? "translateY(0)" : "translateY(-8px)",
                      pointerEvents: requestDropdownOpen ? "auto" : "none",
                      transition: "opacity 0.18s ease, transform 0.18s ease",
                    }}
                  >
                    {/* Placeholder option */}
                    <li
                      role="option"
                      aria-selected={requestModelName === ""}
                      onClick={() => { setRequestModelName(""); setRequestDropdownOpen(false); }}
                      style={{
                        padding: "10px 14px",
                        fontSize: 13.5,
                        color: "var(--text-light)",
                        cursor: "pointer",
                        transition: "background 0.15s ease",
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "#faf8ff"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                    >
                      -- Choose a Model --
                    </li>

                    {/* Filtered requestable models */}
                    {REQUESTABLE_MODELS.filter(
                      (reqModel) => !models.some((m) => m.name === reqModel.name && m.status === "active")
                    ).map((reqModel) => (
                      <li
                        key={reqModel.name}
                        role="option"
                        aria-selected={requestModelName === reqModel.name}
                        onClick={() => { setRequestModelName(reqModel.name); setRequestDropdownOpen(false); }}
                        style={{
                          padding: "10px 14px",
                          fontSize: 13.5,
                          fontWeight: requestModelName === reqModel.name ? 600 : 400,
                          color: requestModelName === reqModel.name ? "var(--primary)" : "var(--text-main)",
                          background: requestModelName === reqModel.name ? "var(--primary-light)" : "transparent",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          gap: 8,
                          transition: "background 0.15s ease, color 0.15s ease",
                        }}
                        onMouseEnter={(e) => {
                          if (requestModelName !== reqModel.name)
                            (e.currentTarget as HTMLElement).style.background = "#faf8ff";
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLElement).style.background =
                            requestModelName === reqModel.name ? "var(--primary-light)" : "transparent";
                        }}
                      >
                        <span>{reqModel.label}</span>
                        {requestModelName === reqModel.name && (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                            stroke="var(--primary)" strokeWidth="2.5"
                            strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        )}
                      </li>
                    ))}

                    {/* Other option */}
                    <li
                      role="option"
                      aria-selected={requestModelName === "other"}
                      onClick={() => { setRequestModelName("other"); setRequestDropdownOpen(false); }}
                      style={{
                        padding: "10px 14px",
                        fontSize: 13.5,
                        fontWeight: requestModelName === "other" ? 600 : 400,
                        color: requestModelName === "other" ? "var(--primary)" : "var(--text-muted)",
                        background: requestModelName === "other" ? "var(--primary-light)" : "transparent",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 8,
                        borderTop: "1px solid var(--border-color)",
                        transition: "background 0.15s ease, color 0.15s ease",
                      }}
                      onMouseEnter={(e) => {
                        if (requestModelName !== "other")
                          (e.currentTarget as HTMLElement).style.background = "#faf8ff";
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLElement).style.background =
                          requestModelName === "other" ? "var(--primary-light)" : "transparent";
                      }}
                    >
                      <span>Other Open Source Model</span>
                      {requestModelName === "other" && (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                          stroke="var(--primary)" strokeWidth="2.5"
                          strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      )}
                    </li>
                  </ul>
                </div>
              </div>

              {requestModelName === "other" && (
                <div className="form-group">
                  <label className="form-label">Custom Model Name</label>
                  <input
                    type="text"
                    placeholder="Enter custom model name (e.g. llama3-8b)"
                    value={customModelName}
                    onChange={(e) => setCustomModelName(e.target.value)}
                    className="form-input"
                    style={{ width: "100%", padding: "10px 12px" }}
                    required
                  />
                </div>
              )}

              <div className="form-group">
                <label className="form-label">Full Name</label>
                <input
                  type="text"
                  placeholder="Your name"
                  value={requestUserName}
                  onChange={(e) => setRequestUserName(e.target.value)}
                  className="form-input"
                  style={{ width: "100%", padding: "10px 12px" }}
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">Email Address</label>
                <input
                  type="email"
                  placeholder="your.email@gwcdata.ai"
                  value={requestUserEmail}
                  onChange={(e) => setRequestUserEmail(e.target.value)}
                  className="form-input"
                  style={{ width: "100%", padding: "10px 12px" }}
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">Business Justification</label>
                <textarea
                  placeholder="Explain why you need access to this model..."
                  value={requestReason}
                  onChange={(e) => setRequestReason(e.target.value)}
                  className="form-textarea"
                  rows={3}
                  style={{ width: "100%", padding: "10px 12px", fontFamily: "inherit" }}
                  required
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 8 }}>
                <button
                  type="button"
                  onClick={() => setShowRequestModal(false)}
                  className="btn btn-secondary"
                  style={{ padding: "10px 16px" }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn"
                  style={{ padding: "10px 20px" }}
                >
                  Submit Request
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
