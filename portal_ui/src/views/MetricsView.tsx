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
        <h1>Metrics</h1>
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

  // Treat the default Docker hostname as "not configured for local dev"
  const isLocalDefault =
    state.grafanaUrl.includes("grafana:3000") ||
    state.grafanaUrl.includes("localhost:3000") ||
    state.grafanaUrl.includes("127.0.0.1:3000");

  return (
    <div>
      <h1>Metrics</h1>

      {iframeError || isLocalDefault ? (
        // Fallback replaces the iframe area (Req 9.5, 12.5)
        <div
          role="status"
          aria-live="polite"
          className="metrics-fallback"
          data-testid="grafana-fallback"
        >
          <span className="metrics-fallback-icon">📊</span>
          <p style={{ margin: 0, fontWeight: 700, fontSize: 18, color: "var(--primary-text)" }}>
            Grafana dashboard not available in local mode
          </p>
          <p style={{ margin: "14px 0 0", color: "var(--text-muted)", fontSize: 14.5, maxWidth: 520, lineHeight: 1.5 }}>
            The Metrics tab embeds a Grafana dashboard which requires the full
            Docker/K8s stack. In local dev mode only Prometheus and Grafana
            running via <code>docker-compose</code> would populate this view.
          </p>
          <p style={{ margin: "16px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Expected URL: <code style={{ fontFamily: "var(--font-mono)", background: "#ffffff", padding: "3px 8px", borderRadius: 4, border: "1px solid var(--primary-border)" }}>{state.grafanaUrl}</code>
          </p>
        </div>
      ) : (
        <iframe
          src={iframeSrc}
          title="LLM Platform Grafana Dashboard"
          width="100%"
          style={{
            minHeight: 600,
            border: "1px solid var(--border-color)",
            borderRadius: "var(--radius-lg)",
            display: "block",
            boxShadow: "var(--shadow-md)",
          }}
          frameBorder={0}
          onError={() => setIframeError(true)}
          data-testid="grafana-iframe"
        />
      )}
    </div>
  );
}
