/**
 * LoginView
 *
 * Phase 6 — real password login, shown when no valid session cookie exists.
 * On success, calls onLogin(identity) so App.tsx can drop the gate.
 */

import { useState } from "react";
import * as portalClient from "../api/portalClient";
import { ApiError, Identity } from "../types";

export default function LoginView({ onLogin }: { onLogin: (identity: Identity) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const identity = await portalClient.login(username.trim(), password);
      onLogin(identity);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-main)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="card"
        style={{ width: "100%", maxWidth: 360, padding: 32, display: "flex", flexDirection: "column", gap: 16 }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 20 }}>GWC Private AI</h1>
          <p style={{ color: "var(--text-muted)", fontSize: 13.5, marginTop: 4, marginBottom: 0 }}>
            Sign in to continue
          </p>
        </div>

        {error && <div style={{ fontSize: 12.5, color: "#ef4444" }}>{error}</div>}

        <div className="form-group" style={{ margin: 0 }}>
          <label className="form-label">Username</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="form-input"
            style={{ width: "100%", padding: "10px 12px" }}
            autoFocus
            required
          />
        </div>

        <div className="form-group" style={{ margin: 0 }}>
          <label className="form-label">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="form-input"
            style={{ width: "100%", padding: "10px 12px" }}
            required
          />
        </div>

        <button type="submit" disabled={loading} className="btn btn-primary" style={{ padding: "10px 16px", justifyContent: "center" }}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
