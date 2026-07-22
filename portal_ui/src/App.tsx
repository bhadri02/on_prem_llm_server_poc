import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import PlaygroundView from "./views/PlaygroundView";
import AuditView from "./views/AuditView";
import ModelView from "./views/ModelView";
import MetricsView from "./views/MetricsView";
import RbacView from "./views/RbacView";
import RedactionView from "./views/RedactionView";
import TokenMetricsView from "./views/TokenMetricsView";
import ActiveUsersView from "./views/ActiveUsersView";
import ServerDetailsView from "./views/ServerDetailsView";
import logo from "./assets/logo.svg";

export default function App() {
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
            to="/playground"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Playground
          </NavLink>
          <NavLink
            to="/audit"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Audit
          </NavLink>
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
          <NavLink
            to="/redaction"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Data Redump
          </NavLink>
          <NavLink
            to="/token-metrics"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Token Consumption
          </NavLink>
          <NavLink
            to="/active-users"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Active Users
          </NavLink>
          <NavLink
            to="/server-details"
            className={({ isActive }) => `navbar-link${isActive ? " active" : ""}`}
          >
            Server Details
          </NavLink>
        </div>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Navigate to="/playground" replace />} />
          <Route path="/playground" element={<PlaygroundView />} />
          <Route path="/audit" element={<AuditView />} />
          <Route path="/models" element={<ModelView />} />
          <Route path="/rbac" element={<RbacView />} />
          <Route path="/redaction" element={<RedactionView />} />
          <Route path="/token-metrics" element={<TokenMetricsView />} />
          <Route path="/active-users" element={<ActiveUsersView />} />
          <Route path="/server-details" element={<ServerDetailsView />} />
        </Routes>
      </main>
    </div>
  );
}

