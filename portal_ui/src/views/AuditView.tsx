/**
 * AuditView
 *
 * Main Audit Viewer page.
 *
 * - On mount fetches the last 24 h of events (limit 50).
 * - Reads ?request_id= URL param to pre-open the detail panel (Req 2.7 / 4.3).
 * - Re-fetches whenever any filter changes.
 * - Displays LoadingSpinner while fetching, ErrorBanner on error.
 * - Renders AuditFilters → AuditTable → AuditDetailPanel (conditional).
 *
 * Requirements: 4.1–4.8
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { AuditEvent } from "../types";
import { ApiError } from "../types";
import { getAuditEvents } from "../api/portalClient";
import AuditFilters from "../components/audit/AuditFilters";
import AuditTable from "../components/audit/AuditTable";
import AuditDetailPanel from "../components/audit/AuditDetailPanel";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

/** Format a Date to the value expected by datetime-local inputs: "YYYY-MM-DDTHH:mm" */
function toDatetimeLocal(date: Date): string {
  return date.toISOString().slice(0, 16);
}

const DEFAULT_FROM = toDatetimeLocal(new Date(Date.now() - 24 * 60 * 60 * 1000));
const DEFAULT_TO = toDatetimeLocal(new Date());

export default function AuditView() {
  const [searchParams] = useSearchParams();

  // Filter state
  const [from, setFrom] = useState(DEFAULT_FROM);
  const [to, setTo] = useState(DEFAULT_TO);
  const [layer, setLayer] = useState("");
  const [outcome, setOutcome] = useState("");

  // Data state
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);

  // Detail panel state — pre-populate from URL param on first render
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(
    () => searchParams.get("request_id"),
  );

  // Fetch whenever filters change
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const params: { from?: string; to?: string; limit: number } = { limit: 50 };
    if (from) params.from = from;
    if (to) params.to = to;

    getAuditEvents(params)
      .then((data) => {
        if (!cancelled) {
          // Client-side layer / outcome filtering (API doesn't expose those params)
          let filtered = data.events;
          if (layer) filtered = filtered.filter((e) => e.layer === layer);
          if (outcome) filtered = filtered.filter((e) => e.outcome === outcome);
          setEvents(filtered);
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
  }, [from, to, layer, outcome]);

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
        Audit Viewer
      </h1>

      {/* Error banner */}
      {error && (
        <ErrorBanner
          statusCode={error.status}
          message={error.message}
          onDismiss={() => setError(null)}
        />
      )}

      {/* Filters */}
      <AuditFilters
        from={from}
        to={to}
        layer={layer}
        outcome={outcome}
        onFromChange={setFrom}
        onToChange={setTo}
        onLayerChange={setLayer}
        onOutcomeChange={setOutcome}
      />

      {/* Results */}
      {loading ? (
        <LoadingSpinner label="Loading audit events…" />
      ) : (
        <AuditTable
          events={events}
          onRequestIdClick={(id) => setSelectedRequestId(id)}
        />
      )}

      {/* Detail panel overlay */}
      {selectedRequestId && (
        <AuditDetailPanel
          requestId={selectedRequestId}
          onClose={() => setSelectedRequestId(null)}
        />
      )}
    </div>
  );
}
