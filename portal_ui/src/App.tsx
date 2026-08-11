import { useEffect, useState } from "react";
import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import ChatView from "./views/ChatView";
import PlaygroundView from "./views/PlaygroundView";
import AuditView from "./views/AuditView";
import GovernanceView from "./views/GovernanceView";
import ModelView from "./views/ModelView";
import RbacView from "./views/RbacView";
import KeysView from "./views/KeysView";
import RedactionView from "./views/RedactionView";
import ServerDetailsView from "./views/ServerDetailsView";
import LoginView from "./views/LoginView";
import * as portalClient from "./api/portalClient";
import { Identity } from "./types";
import logo from "./assets/logo.svg";

export default function App() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;
    portalClient
      .getMe()
      .then((me) => {
        if (!cancelled) setIdentity(me);
      })
      .catch(() => {
        /* not logged in — LoginView will be shown */
      })
      .finally(() => {
        if (!cancelled) setCheckingSession(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLogout() {
    await portalClient.logout();
    setIdentity(null);
  }

  if (checkingSession) {
    return null;
  }

  if (!identity) {
    return <LoginView onLogin={setIdentity} />;
  }

  const isAdmin = identity.roles.includes("admin");

  return (
    <div className="app-container">
      <nav className="navbar" style={{ flexWrap: "wrap", height: "auto", minHeight: 64, padding: "8px 24px", gap: "12px 24px" }}>
        <div className="navbar-brand">
          <img
            src={logo}
            alt="GWC Logo"
            style={{ height: 28, width: "auto", display: "block" }}
          />
          <span>GWC Private AI</span>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <NavLink
            to="/chat"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Chat
          </NavLink>
          <NavLink
            to="/playground"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Playground
          </NavLink>
          {isAdmin && (
            <NavLink
              to="/audit"
              className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
            >
              Audit
            </NavLink>
          )}
          {isAdmin && (
            <NavLink
              to="/governance"
              className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
            >
              Governance
            </NavLink>
          )}
          <NavLink
            to="/models"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Models
          </NavLink>
          <NavLink
            to="/rbac"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Access Control (RBAC)
          </NavLink>
          {isAdmin && (
            <NavLink
              to="/keys"
              className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
            >
              API Keys
            </NavLink>
          )}
          <NavLink
            to="/redaction"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Data Redump
          </NavLink>
          <NavLink
            to="/server-details"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Server Details
          </NavLink>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
          <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
            {identity.username} <span style={{ opacity: 0.6 }}>({identity.roles.join(", ") || "no role"})</span>
          </span>
          <button onClick={handleLogout} className="btn btn-secondary" style={{ padding: "6px 12px", fontSize: 12.5 }}>
            Sign out
          </button>
        </div>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatView />} />
          <Route path="/playground" element={<PlaygroundView />} />
          <Route path="/audit" element={<AuditView />} />
          <Route path="/governance" element={<GovernanceView />} />
          <Route path="/models" element={<ModelView />} />
          <Route path="/rbac" element={<RbacView />} />
          <Route path="/keys" element={<KeysView />} />
          <Route path="/redaction" element={<RedactionView />} />
          <Route path="/server-details" element={<ServerDetailsView />} />
        </Routes>
      </main>
    </div>
  );
}
