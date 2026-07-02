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
import { getModels, patchModelStatus } from "../api/portalClient";
import ModelTable from "../components/models/ModelTable";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

export default function ModelView() {
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<{ status: number; message: string } | null>(
    null,
  );
  const [actionError, setActionError] = useState<{ name: string; message: string } | null>(
    null,
  );
  const [actioning, setActioning] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // Fetch helpers
  // ---------------------------------------------------------------------------

  function fetchModels(opts?: { silent?: boolean }): Promise<void> {
    if (!opts?.silent) setLoading(true);
    setFetchError(null);

    return getModels()
      .then((data) => {
        setModels(data.models);
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
          setModels(data.models);
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

  // ---------------------------------------------------------------------------
  // Derived state
  // ---------------------------------------------------------------------------

  // Buttons are disabled during initial load OR while a PATCH is in flight
  const buttonsDisabled = loading || actioning !== null;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto" }}>
      <h1
        style={{
          fontSize: 22,
          fontWeight: 700,
          color: "#1e293b",
          marginBottom: 20,
          marginTop: 0,
        }}
      >
        Model Registry
      </h1>

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
          style={{
            background: "#fff",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            padding: "32px 20px",
            textAlign: "center",
            color: "#94a3b8",
          }}
        >
          Could not load model list. Dismiss the error above and try refreshing.
        </div>
      ) : models.length === 0 ? (
        /* Empty-state: fetch succeeded but no models returned */
        <div
          style={{
            background: "#ffffff",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            padding: "32px 20px",
            textAlign: "center",
            color: "#94a3b8",
          }}
        >
          No models are currently registered.
        </div>
      ) : (
        /* Normal state: model table */
        <div
          style={{
            background: "#ffffff",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            padding: "16px 20px",
          }}
        >
          <ModelTable
            models={models}
            loading={buttonsDisabled}
            onAction={handleAction}
            actionError={actionError}
            onDismissError={() => setActionError(null)}
          />
        </div>
      )}
    </div>
  );
}
