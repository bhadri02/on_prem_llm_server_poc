import { useState } from "react";

interface ActiveUser {
  id: string;
  name: string;
  email: string;
  department: string;
  requests: number;
  avgLatency: number;
  lastActive: string;
  status: "online" | "idle";
}

const INITIAL_ACTIVE_USERS: ActiveUser[] = [
  { id: "user_01", name: "Adrian Carter", email: "adrian.carter@gwcdata.ai", department: "Security Operations", requests: 1420, avgLatency: 420, lastActive: "Just Now", status: "online" },
  { id: "user_02", name: "Bianca Vance", email: "bianca.vance@gwcdata.ai", department: "Inference Engine Dev", requests: 2890, avgLatency: 530, lastActive: "2 min ago", status: "online" },
  { id: "user_04", name: "Daniel Kovic", email: "daniel.kovic@gwcdata.ai", department: "Product Engineering", requests: 940, avgLatency: 380, lastActive: "15 min ago", status: "online" },
  { id: "user_03", name: "Chloe Dupont", email: "chloe.dupont@gwcdata.ai", department: "Governance & Compliance", requests: 350, avgLatency: 610, lastActive: "40 min ago", status: "idle" },
  { id: "user_05", name: "Emma Larsson", email: "emma.larsson@gwcdata.ai", department: "Risk Management", requests: 120, avgLatency: 640, lastActive: "1 hour ago", status: "idle" },
];

interface DeptMetric {
  name: string;
  requests: number;
  sharePct: number;
}

const DEPT_METRICS: DeptMetric[] = [
  { name: "Inference Engine Dev", requests: 2890, sharePct: 51 },
  { name: "Security Operations", requests: 1420, sharePct: 25 },
  { name: "Product Engineering", requests: 940, sharePct: 16 },
  { name: "Governance & Compliance", requests: 350, sharePct: 6 },
  { name: "Risk Management", requests: 120, sharePct: 2 },
];

export default function ActiveUsersView() {
  const [activeUsers, setActiveUsers] = useState<ActiveUser[]>(INITIAL_ACTIVE_USERS);
  const [search, setSearch] = useState("");

  const filteredUsers = activeUsers.filter(
    (u) =>
      u.name.toLowerCase().includes(search.toLowerCase()) ||
      u.email.toLowerCase().includes(search.toLowerCase()) ||
      u.department.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div>
        <h1>User Analytics & Activity</h1>
        <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: -16, marginBottom: 0 }}>
          Real-time diagnostics mapping active developer sessions and prompt distributions.
        </p>
      </div>

      {/* Metrics Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 20 }}>
        {/* Card 1 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Active Developers
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--primary)" }}>5</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>Sessions in last 24h</div>
        </div>

        {/* Card 2 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Total Requests Routed
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text-main)" }}>5,720</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>Aggregated requests count</div>
        </div>

        {/* Card 3 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Active Departments
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text-main)" }}>5</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>Across all portal nodes</div>
        </div>

        {/* Card 4 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Avg System Latency
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--accent-green-text)" }}>472 ms</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>Inference response delay</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
        {/* Active Sessions List */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20, flex: 2 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
            <h2 style={{ margin: 0 }}>Active User Sessions</h2>
            <input
              type="text"
              placeholder="Search active users..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="form-input"
              style={{ width: "100%", maxWidth: 220, padding: "8px 12px", fontSize: 13 }}
            />
          </div>

          <div style={{ overflowX: "auto" }}>
            <table className="table" style={{ border: "none" }}>
              <thead>
                <tr>
                  <th>Developer</th>
                  <th>Department</th>
                  <th style={{ textAlign: "right" }}>Total Requests</th>
                  <th style={{ textAlign: "right" }}>Avg Latency</th>
                  <th>Last Request</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", color: "var(--text-light)", padding: 24 }}>
                      No active sessions found.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span
                            style={{
                              width: 8,
                              height: 8,
                              borderRadius: "50%",
                              backgroundColor: u.status === "online" ? "var(--accent-green-text)" : "var(--accent-yellow-text)",
                            }}
                            title={u.status === "online" ? "Online" : "Idle"}
                          />
                          <div>
                            <div style={{ fontWeight: 600, fontSize: 13.5, color: "var(--text-main)" }}>{u.name}</div>
                            <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{u.email}</div>
                          </div>
                        </div>
                      </td>
                      <td style={{ fontSize: 13, color: "var(--text-muted)" }}>{u.department}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 13 }}>{u.requests}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 13 }}>{u.avgLatency} ms</td>
                      <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{u.lastActive}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Share by Department */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <h2 style={{ margin: 0 }}>Request Distribution</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {DEPT_METRICS.map((dm) => (
              <div key={dm.name} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
                  <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{dm.name}</span>
                  <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>
                    {dm.sharePct}% <span style={{ fontSize: 11, color: "var(--text-light)" }}>({dm.requests} reqs)</span>
                  </span>
                </div>
                {/* Visual bar split */}
                <div style={{ height: 8, width: "100%", background: "#f3f4f6", borderRadius: 4, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${dm.sharePct}%`,
                      height: "100%",
                      background: "var(--primary)",
                      borderRadius: 4,
                      transition: "width 0.4s ease-out",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
