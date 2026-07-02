import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import PlaygroundView from "./views/PlaygroundView";
import AuditView from "./views/AuditView";
import ModelView from "./views/ModelView";
import MetricsView from "./views/MetricsView";

const navStyle: React.CSSProperties = {
  display: "flex",
  gap: 24,
  padding: "12px 24px",
  background: "#1e293b",
  alignItems: "center",
};

const linkStyle = ({ isActive }: { isActive: boolean }): React.CSSProperties => ({
  color: isActive ? "#60a5fa" : "#cbd5e1",
  textDecoration: "none",
  fontWeight: isActive ? 600 : 400,
});

export default function App() {
  return (
    <div style={{ fontFamily: "system-ui, sans-serif", minHeight: "100vh", background: "#f8fafc" }}>
      <nav style={navStyle}>
        <span style={{ color: "#f1f5f9", fontWeight: 700, marginRight: 16 }}>
          LLM Platform Portal
        </span>
        <NavLink to="/playground" style={linkStyle}>
          Playground
        </NavLink>
        <NavLink to="/audit" style={linkStyle}>
          Audit
        </NavLink>
        <NavLink to="/models" style={linkStyle}>
          Models
        </NavLink>
        <NavLink to="/metrics" style={linkStyle}>
          Metrics
        </NavLink>
      </nav>
      <main style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<Navigate to="/playground" replace />} />
          <Route path="/playground" element={<PlaygroundView />} />
          <Route path="/audit" element={<AuditView />} />
          <Route path="/models" element={<ModelView />} />
          <Route path="/metrics" element={<MetricsView />} />
        </Routes>
      </main>
    </div>
  );
}
