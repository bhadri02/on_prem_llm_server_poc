import { useState } from "react";
import initialUsersJson from "../data/users.json";

type Role = "Admin" | "Developer" | "Auditor";

interface Permission {
  id: string;
  name: string;
  description: string;
}

const PERMISSIONS: Permission[] = [
  { id: "chat", name: "Playground Prompts", description: "Submit text prompts to models inside the Playground" },
  { id: "models", name: "Model Lifecycle Management", description: "Activate, Staging, or Retire registered model profiles" },
  { id: "audit", name: "Audit Trail Viewing", description: "View full audit trails and inspect request logs" },
  { id: "redaction", name: "Data Redaction Configuration", description: "Update masking policies for PII and API keys" },
  { id: "metrics", name: "System Metrics Access", description: "Access token consumption graphs and active user sessions" },
];

const INITIAL_MATRIX: Record<Role, Record<string, boolean>> = {
  Admin: { chat: true, models: true, audit: true, redaction: true, metrics: true },
  Developer: { chat: true, models: false, audit: false, redaction: false, metrics: true },
  Auditor: { chat: false, models: false, audit: true, redaction: true, metrics: false },
};

interface UserRecord {
  id: string;
  name: string;
  role: Role;
  department: string;
  email: string;
}

const DEPARTMENTS = [
  "Security Operations",
  "Inference Engine Dev",
  "Product Engineering",
  "Governance & Compliance",
  "Risk Management",
];

const INITIAL_USERS: UserRecord[] = initialUsersJson as UserRecord[];

export default function RbacView() {
  const [selectedRole, setSelectedRole] = useState<Role>("Admin");
  const [matrix, setMatrix] = useState<Record<Role, Record<string, boolean>>>(INITIAL_MATRIX);
  const [users, setUsers] = useState<UserRecord[]>(INITIAL_USERS);
  const [search, setSearch] = useState("");

  // New user form state
  const [newUserName, setNewUserName] = useState("");
  const [newUserDept, setNewUserDept] = useState(DEPARTMENTS[0]);
  const [newUserRole, setNewUserRole] = useState<Role>("Developer");
  const [showAddForm, setShowAddForm] = useState(false);

  function togglePermission(role: Role, permId: string) {
    setMatrix((prev) => ({
      ...prev,
      [role]: {
        ...prev[role],
        [permId]: !prev[role][permId],
      },
    }));
  }

  function handleRoleChange(userId: string, newRole: Role) {
    setUsers((prev) =>
      prev.map((u) => (u.id === userId ? { ...u, role: newRole } : u))
    );
  }

  function handleAddUser(e: React.FormEvent) {
    e.preventDefault();
    if (!newUserName.trim() || !newUserDept.trim()) return;

    const newId = `user_${Math.floor(Math.random() * 900 + 100)}`;
    const newEmail = `${newUserName.trim().toLowerCase().replace(/\s+/g, ".")}@gwcdata.ai`;

    const newUser: UserRecord = {
      id: newId,
      name: newUserName.trim(),
      role: newUserRole,
      department: newUserDept.trim(),
      email: newEmail,
    };

    setUsers((prev) => [...prev, newUser]);
    setNewUserName("");
    setNewUserDept(DEPARTMENTS[0]);
    setNewUserRole("Developer");
    setShowAddForm(false);
  }

  function handleDeleteUser(userId: string) {
    setUsers((prev) => prev.filter((u) => u.id !== userId));
  }

  const filteredUsers = users.filter(
    (u) =>
      u.name.toLowerCase().includes(search.toLowerCase()) ||
      u.id.toLowerCase().includes(search.toLowerCase()) ||
      u.department.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div>
        <h1>Access Control (RBAC)</h1>
        <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: -16, marginBottom: 0 }}>
          Manage role-based privileges and assign team access control permissions.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
        {/* Permission Matrix card */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ margin: 0 }}>Role Matrix</h2>
            <div style={{ display: "flex", background: "#f3f4f6", padding: 4, borderRadius: 8 }}>
              {(["Admin", "Developer", "Auditor"] as Role[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setSelectedRole(r)}
                  style={{
                    border: "none",
                    background: selectedRole === r ? "#ffffff" : "transparent",
                    color: selectedRole === r ? "var(--primary)" : "var(--text-muted)",
                    padding: "6px 14px",
                    borderRadius: 6,
                    cursor: "pointer",
                    fontWeight: 600,
                    fontSize: 12.5,
                    boxShadow: selectedRole === r ? "var(--shadow-sm)" : "none",
                    transition: "all 0.15s ease",
                  }}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {PERMISSIONS.map((perm) => {
              const isEnabled = matrix[selectedRole][perm.id];
              return (
                <div
                  key={perm.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "12px 14px",
                    background: "var(--bg-main)",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--border-color)",
                  }}
                >
                  <div style={{ paddingRight: 16 }}>
                    <div style={{ fontWeight: 600, fontSize: 13.5, color: "var(--text-main)" }}>{perm.name}</div>
                    <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>{perm.description}</div>
                  </div>
                  <button
                    onClick={() => togglePermission(selectedRole, perm.id)}
                    style={{
                      width: 44,
                      height: 24,
                      borderRadius: 12,
                      background: isEnabled ? "var(--primary)" : "#d1d5db",
                      border: "none",
                      position: "relative",
                      cursor: "pointer",
                      transition: "background-color 0.2s",
                      padding: 0,
                    }}
                    aria-label={`Toggle ${perm.name} for ${selectedRole}`}
                  >
                    <span
                      style={{
                        position: "absolute",
                        top: 2,
                        left: isEnabled ? 22 : 2,
                        width: 20,
                        height: 20,
                        borderRadius: "50%",
                        background: "#ffffff",
                        boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                        transition: "left 0.2s",
                      }}
                    />
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* User assignment card */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
            <h2 style={{ margin: 0 }}>User Assignments</h2>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="text"
                placeholder="Search users..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="form-input"
                style={{ width: "100%", maxWidth: 150, padding: "8px 12px", fontSize: 13 }}
              />
              <button
                onClick={() => setShowAddForm(!showAddForm)}
                className="btn"
                style={{ padding: "8px 14px", fontSize: 12.5 }}
              >
                {showAddForm ? "Cancel" : "Add User"}
              </button>
            </div>
          </div>

          {showAddForm && (
            <form
              onSubmit={handleAddUser}
              style={{
                background: "var(--bg-main)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--radius-sm)",
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              <h3 style={{ margin: 0, fontSize: 14 }}>Create New User Profile</h3>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 12 }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <input
                    type="text"
                    placeholder="Full Name"
                    value={newUserName}
                    onChange={(e) => setNewUserName(e.target.value)}
                    className="form-input"
                    style={{ padding: "8px 12px", fontSize: 12.5 }}
                    required
                  />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <select
                    value={newUserDept}
                    onChange={(e) => setNewUserDept(e.target.value)}
                    className="form-select"
                    style={{ padding: "8px 12px", fontSize: 12.5, width: "100%" }}
                  >
                    {DEPARTMENTS.map((dept) => (
                      <option key={dept} value={dept}>
                        {dept}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <select
                    value={newUserRole}
                    onChange={(e) => setNewUserRole(e.target.value as Role)}
                    className="form-select"
                    style={{ padding: "8px 12px", fontSize: 12.5, width: "100%" }}
                  >
                    <option value="Admin">Admin</option>
                    <option value="Developer">Developer</option>
                    <option value="Auditor">Auditor</option>
                  </select>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button
                  type="button"
                  onClick={() => setShowAddForm(false)}
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px", fontSize: 12 }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn"
                  style={{ padding: "6px 12px", fontSize: 12 }}
                >
                  Save User
                </button>
              </div>
            </form>
          )}

          <div style={{ overflowX: "auto" }}>
            <table className="table" style={{ border: "none" }}>
              <thead>
                <tr>
                  <th style={{ padding: "8px 10px", fontSize: 11 }}>User</th>
                  <th style={{ padding: "8px 10px", fontSize: 11 }}>Department</th>
                  <th style={{ padding: "8px 10px", fontSize: 11 }}>Role</th>
                  <th style={{ padding: "8px 10px", fontSize: 11, textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan={4} style={{ textAlign: "center", color: "var(--text-light)", padding: 24 }}>
                      No matching users found.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((u) => (
                    <tr key={u.id}>
                      <td style={{ padding: "10px 10px" }}>
                        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text-main)" }}>{u.name}</div>
                        <div style={{ fontSize: 11, color: "var(--text-light)", fontFamily: "var(--font-mono)" }}>
                          {u.id}
                        </div>
                      </td>
                      <td style={{ padding: "10px 10px", fontSize: 12.5, color: "var(--text-muted)" }}>
                        {u.department}
                      </td>
                      <td style={{ padding: "10px 10px" }}>
                        <select
                          value={u.role}
                          onChange={(e) => handleRoleChange(u.id, e.target.value as Role)}
                          className="form-select"
                          style={{
                            padding: "4px 8px",
                            fontSize: 12,
                            borderRadius: 6,
                            border: "1px solid var(--border-color)",
                          }}
                        >
                          <option value="Admin">Admin</option>
                          <option value="Developer">Developer</option>
                          <option value="Auditor">Auditor</option>
                        </select>
                      </td>
                      <td style={{ padding: "10px 10px", textAlign: "right" }}>
                        <button
                          onClick={() => handleDeleteUser(u.id)}
                          className="btn btn-outline"
                          style={{
                            padding: "4px 8px",
                            fontSize: 11,
                            borderColor: "#f87171",
                            color: "#ef4444",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = "#fee2e2";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = "transparent";
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
