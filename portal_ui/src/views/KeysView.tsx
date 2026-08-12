/**
 * KeysView
 *
 * Admin-wide API key listing (Phase 5 — GET /portal/keys/), spanning every
 * user rather than being scoped to one (that per-user view already exists
 * inline in RbacView). Read-only here, including each key's own rate limit
 * (requests/min — there is no platform-wide fallback, every key carries a
 * concrete value); revoke/entitlement/rate-limit edits still go through the
 * per-user panel in Access Control, which has the user_id this listing's
 * own rows are joined against.
 */

import { useEffect, useState } from "react";
import * as portalClient from "../api/portalClient";
import { ApiError, ApiKeyWithOwner } from "../types";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

export default function KeysView() {
  const [keys, setKeys] = useState<ApiKeyWithOwner[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    portalClient
      .getAllKeys()
      .then((data) => {
        if (!cancelled) setKeys(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError({
          status: err instanceof ApiError ? err.status : 0,
          message: err instanceof ApiError ? err.message : String(err),
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = keys.filter(
    (k) =>
      k.owner_username.toLowerCase().includes(search.toLowerCase()) ||
      k.key_prefix.toLowerCase().includes(search.toLowerCase()) ||
      (k.label ?? "").toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 style={{ margin: 0 }}>API Keys</h1>
          <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: 4, marginBottom: 0 }}>
            Every API key across all users, with model entitlements and rate limits. Revoke or edit
            entitlements/rate limits from the Access Control tab's per-user key panel.
          </p>
        </div>
        <input
          type="text"
          placeholder="Search by owner, prefix, or label..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="form-input"
          style={{ width: 240, padding: "8px 12px", fontSize: 13 }}
        />
      </div>

      {error && <ErrorBanner statusCode={error.status} message={error.message} onDismiss={() => setError(null)} />}

      {loading ? (
        <LoadingSpinner label="Loading keys…" />
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ overflowX: "auto" }}>
            <table className="table" style={{ border: "none" }}>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Owner</th>
                  <th>Entitled models</th>
                  <th>Rate limit</th>
                  <th>Expires</th>
                  <th>Status</th>
                  <th>Last used</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ textAlign: "center", color: "var(--text-light)", padding: 32 }}>
                      No keys found.
                    </td>
                  </tr>
                ) : (
                  filtered.map((k) => (
                    <tr key={k.key_id}>
                      <td>
                        <code style={{ fontFamily: "var(--font-mono)" }}>{k.key_prefix}…</code>
                        {k.label ? ` (${k.label})` : ""}
                      </td>
                      <td style={{ fontWeight: 600 }}>{k.owner_username}</td>
                      <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                        {k.model_entitlements.length === 0 ? "All models" : k.model_entitlements.join(", ")}
                      </td>
                      <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{k.rate_limit_rpm} req/min</td>
                      <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                        {k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never"}
                      </td>
                      <td>
                        <span className={`badge ${k.status === "active" ? "badge-green" : "badge-red"}`}>
                          {k.status}
                        </span>
                      </td>
                      <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                        {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "Never"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
