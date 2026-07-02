/**
 * MetricsView
 *
 * Embeds the Grafana POC overview dashboard in an <iframe>.
 * The Grafana base URL is fetched at runtime from GET /portal/config so it is
 * never hardcoded in the frontend bundle (Req 9.4).
 *
 * Behaviour:
 *  - Shows a LoadingSpinner while the config is being fetched.
 *  - Shows an ErrorBanner (dismissible) if the config fetch fails.
 *  - Once the URL is available, renders the iframe at full width / min 600px
 *    height (Req 9.1, 9.2).
 *  - If the iframe fires an error event, replaces the iframe area with a
 *    static fallback message without affecting other views (Req 9.5, 12.5).
 */

import { useEffect, useState } from "react";
import { getConfig } from "../api/portalClient";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";
import { ApiError } from "../types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type LoadState =
  | { phase: "loading" }
  | { phase: "error"; statusCode: number; message: string }
  | { phase: "ready"; grafanaUrl: string };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MetricsView() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  // tracks whether the iframe itself fired an error after the URL loaded
  const [iframeError, setIframeError] = useState(false);

  // Fetch Grafana URL from Portal_API config on mount
  useEffect(() => {
    let cancelled = false;

    getConfig()
      .then((cfg) => {
        if (!cancelled) {
          setState({ phase: "ready", grafanaUrl: cfg.grafana_url });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setState({ phase: "error", statusCode: err.status, message: err.message });
          } else {
            setState({
              phase: "error",
              statusCode: 0,
              message: err instanceof Error ? err.message : "Failed to load portal config.",
            });
          }
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // --- Loading ---
  if (state.phase === "loading") {
    return <LoadingSpinner label="Loading Metrics…" />;
  }

  // --- Config fetch error ---
  if (state.phase === "error") {
    return (
      <div>
        <h2 style={headingStyle}>Metrics</h2>
        <ErrorBanner
          statusCode={state.statusCode}
          message={state.message}
          onDismiss={() =>
            setState({ phase: "error", statusCode: state.statusCode, message: state.message })
          }
        />
      </div>
    );
  }

  // --- Ready: render iframe or fallback ---
  const iframeSrc = `${state.grafanaUrl}/d/poc-overview/llm-platform-poc?orgId=1&kiosk`;

  return (
    <div>
      <h2 style={headingStyle}>Metrics</h2>

      {iframeError ? (
        // Fallback replaces the iframe area (Req 9.5, 12.5)
        <div
          role="status"
          aria-live="polite"
          style={fallbackStyle}
          data-testid="grafana-fallback"
        >
          <span style={{ fontSize: 32, marginBottom: 12 }}>📊</span>
          <p style={{ margin: 0, fontWeight: 600 }}>
            Grafana dashboard is currently unavailable.
          </p>
          <p style={{ margin: "8px 0 0", color: "#6b7280", fontSize: 14 }}>
            The dashboard at <code>{state.grafanaUrl}</code> could not be loaded. Check
            that Grafana is running and the URL is correct.
          </p>
        </div>
      ) : (
        <iframe
          src={iframeSrc}
          title="LLM Platform Grafana Dashboard"
          width="100%"
          style={iframeStyle}
          frameBorder={0}
          onError={() => setIframeError(true)}
          data-testid="grafana-iframe"
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const headingStyle: React.CSSProperties = {
  marginTop: 0,
  marginBottom: 16,
  fontSize: 20,
  fontWeight: 600,
  color: "#1e293b",
};

const iframeStyle: React.CSSProperties = {
  minHeight: 600,
  border: "none",
  borderRadius: 6,
  display: "block",
};

const fallbackStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  minHeight: 600,
  border: "2px dashed #d1d5db",
  borderRadius: 6,
  padding: 32,
  textAlign: "center",
  color: "#374151",
  background: "#f9fafb",
};
