/**
 * RbacView
 *
 * Access Control (RBAC) admin view — Phase 3.
 *
 * Two cards, matching the layout of the original mock:
 *  - Role Matrix (left): read-only role -> task_type permission matrix,
 *    sourced from GET /portal/roles/{role}/permissions (Section 2.4:
 *    "Roles tab — read-only in POC").
 *  - User Assignments (right): live CRUD against /portal/users/* — create,
 *    deactivate, and single-role assignment (a user can hold more than one
 *    role in the DB, but this UI keeps the existing single-select UX and
 *    replaces the full role set on change, for simplicity).
 *
 * Clicking "Keys" on a user row expands an inline panel for API key
 * management against /portal/users/{id}/keys/* — generate (raw key shown
 * once), revoke, and edit model entitlements via a checklist populated from
 * GET /portal/models.
 */

import { Fragment, useEffect, useState } from "react";
import * as portalClient from "../api/portalClient";
import { ApiError, ApiKey, ModelRecord, Role, User } from "../types";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

const TASK_TYPES: { id: string; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "code", label: "Code" },
  { id: "reasoning", label: "Reasoning" },
  { id: "summarization", label: "Summarization" },
  { id: "translation", label: "Translation" },
];

function errMessage(err: unknown): string {
  return err instanceof ApiError ? `Error ${err.status}: ${err.message}` : String(err);
}

export default function RbacView() {
  // --- Top-level data ---
  const [roles, setRoles] = useState<Role[]>([]);
  const [rolePermissions, setRolePermissions] = useState<Record<string, Record<string, boolean>>>({});
  const [users, setUsers] = useState<User[]>([]);
  const [models, setModels] = useState<ModelRecord[]>([]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ status: number; message: string } | null>(null);

  const [selectedMatrixRole, setSelectedMatrixRole] = useState<string>("");
  const [search, setSearch] = useState("");

  // Editable permission matrix — Phase 5 (was read-only in Phase 3)
  const [pendingPerms, setPendingPerms] = useState<Record<string, boolean> | null>(null);
  const [savingMatrix, setSavingMatrix] = useState(false);
  const [matrixError, setMatrixError] = useState<string | null>(null);

  // --- Add-user form ---
  const [showAddForm, setShowAddForm] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newDepartment, setNewDepartment] = useState("");
  const [newRole, setNewRole] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [addUserError, setAddUserError] = useState<string | null>(null);
  const [addingUser, setAddingUser] = useState(false);

  // --- Per-row action state ---
  const [rowActionError, setRowActionError] = useState<{ userId: string; message: string } | null>(null);
  const [actioningUserId, setActioningUserId] = useState<string | null>(null);
  const [passwordToast, setPasswordToast] = useState<string | null>(null);

  // --- Key management (expandable panel) ---
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);
  const [keysByUser, setKeysByUser] = useState<Record<string, ApiKey[]>>({});
  const [keysLoading, setKeysLoading] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [newKeyEntitlements, setNewKeyEntitlements] = useState<string[]>([]);
  const [creatingKey, setCreatingKey] = useState(false);
  const [justCreatedKey, setJustCreatedKey] = useState<{ userId: string; rawKey: string } | null>(null);

  // ---------------------------------------------------------------------
  // Initial load
  // ---------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const [rolesData, usersData, modelsData] = await Promise.all([
          portalClient.getRoles(),
          portalClient.getUsers(),
          portalClient.getModels(),
        ]);
        if (cancelled) return;

        setRoles(rolesData);
        setUsers(usersData);
        setModels(modelsData.models);
        setSelectedMatrixRole((prev) => prev || rolesData[0]?.role_name || "");

        const permEntries = await Promise.all(
          rolesData.map(async (r) => {
            const perms = await portalClient.getRolePermissions(r.role_name);
            return [r.role_name, perms.permissions] as const;
          }),
        );
        if (cancelled) return;
        setRolePermissions(Object.fromEntries(permEntries));
      } catch (err) {
        if (cancelled) return;
        setLoadError({
          status: err instanceof ApiError ? err.status : 0,
          message: errMessage(err),
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  // ---------------------------------------------------------------------
  // User actions
  // ---------------------------------------------------------------------

  async function handleAddUser(e: React.FormEvent) {
    e.preventDefault();
    if (!newUsername.trim() || !newRole) return;

    setAddingUser(true);
    setAddUserError(null);
    try {
      const created = await portalClient.createUser({
        username: newUsername.trim(),
        email: newEmail.trim() || null,
        department: newDepartment.trim() || null,
        roles: [newRole],
        password: newPassword.trim() || null,
      });
      setUsers((prev) => [...prev, created]);
      setNewUsername("");
      setNewEmail("");
      setNewDepartment("");
      setNewRole(roles[0]?.role_name ?? "");
      setNewPassword("");
      setShowAddForm(false);
    } catch (err) {
      setAddUserError(errMessage(err));
    } finally {
      setAddingUser(false);
    }
  }

  async function handleResetPassword(userId: string, username: string) {
    const password = window.prompt(`Set a new login password for ${username}:`);
    if (!password || !password.trim()) return;
    setActioningUserId(userId);
    setRowActionError(null);
    try {
      await portalClient.resetUserPassword(userId, password.trim());
      setPasswordToast(`Password updated for ${username}.`);
      setTimeout(() => setPasswordToast(null), 3000);
    } catch (err) {
      setRowActionError({ userId, message: errMessage(err) });
    } finally {
      setActioningUserId(null);
    }
  }

  async function handleRoleChange(userId: string, role: string) {
    setActioningUserId(userId);
    setRowActionError(null);
    try {
      const updated = await portalClient.patchUserRoles(userId, [role]);
      setUsers((prev) => prev.map((u) => (u.user_id === userId ? updated : u)));
    } catch (err) {
      setRowActionError({ userId, message: errMessage(err) });
    } finally {
      setActioningUserId(null);
    }
  }

  async function handleDeactivate(userId: string) {
    setActioningUserId(userId);
    setRowActionError(null);
    try {
      await portalClient.deactivateUser(userId);
      setUsers((prev) =>
        prev.map((u) => (u.user_id === userId ? { ...u, status: "inactive" } : u)),
      );
    } catch (err) {
      setRowActionError({ userId, message: errMessage(err) });
    } finally {
      setActioningUserId(null);
    }
  }

  // ---------------------------------------------------------------------
  // Key management
  // ---------------------------------------------------------------------

  async function toggleKeysPanel(userId: string) {
    if (expandedUserId === userId) {
      setExpandedUserId(null);
      return;
    }
    setExpandedUserId(userId);
    setKeyError(null);
    setJustCreatedKey(null);
    setNewKeyLabel("");
    setNewKeyEntitlements([]);

    if (!keysByUser[userId]) {
      setKeysLoading(true);
      try {
        const keys = await portalClient.listApiKeys(userId);
        setKeysByUser((prev) => ({ ...prev, [userId]: keys }));
      } catch (err) {
        setKeyError(errMessage(err));
      } finally {
        setKeysLoading(false);
      }
    }
  }

  async function handleCreateKey(userId: string) {
    setCreatingKey(true);
    setKeyError(null);
    try {
      const created = await portalClient.createApiKey(userId, {
        label: newKeyLabel.trim() || undefined,
        model_entitlements: newKeyEntitlements,
      });
      setKeysByUser((prev) => ({ ...prev, [userId]: [...(prev[userId] ?? []), created] }));
      setJustCreatedKey({ userId, rawKey: created.raw_key });
      setNewKeyLabel("");
      setNewKeyEntitlements([]);
    } catch (err) {
      setKeyError(errMessage(err));
    } finally {
      setCreatingKey(false);
    }
  }

  async function handleRevokeKey(userId: string, keyId: string) {
    setKeyError(null);
    try {
      const updated = await portalClient.revokeApiKey(userId, keyId);
      setKeysByUser((prev) => ({
        ...prev,
        [userId]: (prev[userId] ?? []).map((k) => (k.key_id === keyId ? updated : k)),
      }));
    } catch (err) {
      setKeyError(errMessage(err));
    }
  }

  // ---------------------------------------------------------------------
  // Editable permission matrix
  // ---------------------------------------------------------------------

  function toggleMatrixCell(task: string) {
    const current = pendingPerms ?? rolePermissions[selectedMatrixRole] ?? {};
    setPendingPerms({ ...current, [task]: !current[task] });
  }

  function selectMatrixRole(role: string) {
    setSelectedMatrixRole(role);
    setPendingPerms(null);
    setMatrixError(null);
  }

  async function handleSaveMatrix() {
    if (!pendingPerms) return;
    setSavingMatrix(true);
    setMatrixError(null);
    try {
      const result = await portalClient.patchRolePermissions(selectedMatrixRole, pendingPerms);
      setRolePermissions((prev) => ({ ...prev, [selectedMatrixRole]: result.permissions }));
      setPendingPerms(null);
    } catch (err) {
      setMatrixError(errMessage(err));
    } finally {
      setSavingMatrix(false);
    }
  }

  function handleDiscardMatrix() {
    setPendingPerms(null);
    setMatrixError(null);
  }

  function toggleEntitlement(modelName: string) {
    setNewKeyEntitlements((prev) =>
      prev.includes(modelName) ? prev.filter((m) => m !== modelName) : [...prev, modelName],
    );
  }

  // ---------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------

  const filteredUsers = users.filter(
    (u) =>
      u.username.toLowerCase().includes(search.toLowerCase()) ||
      u.user_id.toLowerCase().includes(search.toLowerCase()) ||
      (u.department ?? "").toLowerCase().includes(search.toLowerCase()),
  );

  const activeModelNames = models.filter((m) => m.status === "active").map((m) => m.name);

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
        <h1>Access Control (RBAC)</h1>
        <LoadingSpinner label="Loading users and roles…" />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div>
        <h1>Access Control (RBAC)</h1>
        <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: -16, marginBottom: 0 }}>
          Manage users, role assignments, and per-key model entitlements.
        </p>
      </div>

      {loadError && (
        <ErrorBanner
          statusCode={loadError.status}
          message={loadError.message}
          onDismiss={() => setLoadError(null)}
        />
      )}

      {passwordToast && (
        <div
          style={{
            fontSize: 12.5,
            padding: "10px 14px",
            borderRadius: 8,
            background: "var(--accent-green-bg, #e4f8ef)",
            color: "var(--accent-green-text, #0ea968)",
            border: "1px solid rgba(14,169,104,0.25)",
          }}
        >
          {passwordToast}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
        {/* Permission Matrix card (read-only) */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Role Matrix</h2>
            <div style={{ display: "flex", background: "#f3f4f6", padding: 4, borderRadius: 8, flexWrap: "wrap" }}>
              {roles.map((r) => (
                <button
                  key={r.role_name}
                  onClick={() => selectMatrixRole(r.role_name)}
                  style={{
                    border: "none",
                    background: selectedMatrixRole === r.role_name ? "#ffffff" : "transparent",
                    color: selectedMatrixRole === r.role_name ? "var(--primary)" : "var(--text-muted)",
                    padding: "6px 14px",
                    borderRadius: 6,
                    cursor: "pointer",
                    fontWeight: 600,
                    fontSize: 12.5,
                    boxShadow: selectedMatrixRole === r.role_name ? "var(--shadow-sm)" : "none",
                    transition: "all 0.15s ease",
                    textTransform: "capitalize",
                  }}
                >
                  {r.role_name}
                </button>
              ))}
            </div>
          </div>

          {selectedMatrixRole && (
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-muted)" }}>
              {roles.find((r) => r.role_name === selectedMatrixRole)?.description}
            </p>
          )}

          {matrixError && <div style={{ fontSize: 12, color: "#ef4444" }}>{matrixError}</div>}

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {TASK_TYPES.map((task) => {
              const effective = pendingPerms ?? rolePermissions[selectedMatrixRole] ?? {};
              const allowed = !!effective[task.id];
              return (
                <div
                  key={task.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "10px 14px",
                    background: "var(--bg-main)",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--border-color)",
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 13.5, color: "var(--text-main)" }}>
                    {task.label}
                  </span>
                  <button
                    onClick={() => toggleMatrixCell(task.id)}
                    disabled={savingMatrix}
                    className={`badge ${allowed ? "badge-green" : "badge-red"}`}
                    style={{ border: "none", cursor: savingMatrix ? "not-allowed" : "pointer" }}
                    title="Click to toggle"
                  >
                    {allowed ? "Allowed" : "Denied"}
                  </button>
                </div>
              );
            })}
          </div>

          {pendingPerms && (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Unsaved changes</span>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={handleDiscardMatrix} disabled={savingMatrix} className="btn btn-secondary" style={{ padding: "6px 12px", fontSize: 12 }}>
                  Discard
                </button>
                <button onClick={handleSaveMatrix} disabled={savingMatrix} className="btn" style={{ padding: "6px 12px", fontSize: 12 }}>
                  {savingMatrix ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}

          <p style={{ fontSize: 11, color: "var(--text-light)", margin: 0, lineHeight: 1.5 }}>
            ⚠ Saving here persists to the database and updates this view immediately, but does{" "}
            <strong>not</strong> take live effect on request routing until{" "}
            <code style={{ fontFamily: "var(--font-mono)" }}>policy_matrix.yaml</code> is hand-edited and
            the Intelligent Router is restarted. See docs/FRONTEND_INTEGRATION.md.
          </p>
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
                onClick={() => {
                  setShowAddForm((v) => !v);
                  setAddUserError(null);
                  if (!newRole) setNewRole(roles[0]?.role_name ?? "");
                }}
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
              <h3 style={{ margin: 0, fontSize: 14 }}>Create New User</h3>
              {addUserError && (
                <div style={{ fontSize: 12.5, color: "#ef4444" }}>{addUserError}</div>
              )}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 12 }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <input
                    type="text"
                    placeholder="Username"
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    className="form-input"
                    style={{ padding: "8px 12px", fontSize: 12.5 }}
                    required
                  />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <input
                    type="email"
                    placeholder="Email (optional)"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    className="form-input"
                    style={{ padding: "8px 12px", fontSize: 12.5 }}
                  />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <input
                    type="text"
                    placeholder="Department (optional)"
                    value={newDepartment}
                    onChange={(e) => setNewDepartment(e.target.value)}
                    className="form-input"
                    style={{ padding: "8px 12px", fontSize: 12.5 }}
                  />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <select
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value)}
                    className="form-select"
                    style={{ padding: "8px 12px", fontSize: 12.5, width: "100%", textTransform: "capitalize" }}
                  >
                    {roles.map((r) => (
                      <option key={r.role_name} value={r.role_name}>
                        {r.role_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                  <input
                    type="password"
                    placeholder="Password (optional)"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="form-input"
                    style={{ padding: "8px 12px", fontSize: 12.5 }}
                    autoComplete="new-password"
                  />
                </div>
              </div>
              <p style={{ margin: 0, fontSize: 11, color: "var(--text-light)" }}>
                Leave password blank to create the account without login access — set one later via
                "Reset password" on the user row.
              </p>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button
                  type="button"
                  onClick={() => setShowAddForm(false)}
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px", fontSize: 12 }}
                >
                  Cancel
                </button>
                <button type="submit" className="btn" disabled={addingUser} style={{ padding: "6px 12px", fontSize: 12 }}>
                  {addingUser ? "Saving…" : "Save User"}
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
                  <th style={{ padding: "8px 10px", fontSize: 11 }}>Status</th>
                  <th style={{ padding: "8px 10px", fontSize: 11, textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", color: "var(--text-light)", padding: 24 }}>
                      No matching users found.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((u) => (
                    <Fragment key={u.user_id}>
                      <tr>
                        <td style={{ padding: "10px 10px" }}>
                          <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text-main)" }}>{u.username}</div>
                          <div style={{ fontSize: 11, color: "var(--text-light)", fontFamily: "var(--font-mono)" }}>
                            {u.user_id}
                          </div>
                        </td>
                        <td style={{ padding: "10px 10px", fontSize: 12.5, color: "var(--text-muted)" }}>
                          {u.department || "—"}
                        </td>
                        <td style={{ padding: "10px 10px" }}>
                          <select
                            value={u.roles[0] ?? ""}
                            onChange={(e) => handleRoleChange(u.user_id, e.target.value)}
                            disabled={actioningUserId === u.user_id || u.status === "inactive"}
                            className="form-select"
                            style={{
                              padding: "4px 8px",
                              fontSize: 12,
                              borderRadius: 6,
                              border: "1px solid var(--border-color)",
                              textTransform: "capitalize",
                            }}
                          >
                            {!u.roles[0] && <option value="">Unassigned</option>}
                            {roles.map((r) => (
                              <option key={r.role_name} value={r.role_name}>
                                {r.role_name}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td style={{ padding: "10px 10px" }}>
                          <span className={`badge ${u.status === "active" ? "badge-green" : "badge-red"}`}>
                            {u.status}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", textAlign: "right", whiteSpace: "nowrap" }}>
                          <button
                            onClick={() => toggleKeysPanel(u.user_id)}
                            className="btn btn-outline"
                            style={{ padding: "4px 8px", fontSize: 11, marginRight: 6 }}
                          >
                            {expandedUserId === u.user_id ? "Hide Keys" : "Keys"}
                          </button>
                          <button
                            onClick={() => handleResetPassword(u.user_id, u.username)}
                            disabled={actioningUserId === u.user_id}
                            className="btn btn-outline"
                            style={{ padding: "4px 8px", fontSize: 11, marginRight: 6 }}
                          >
                            Reset password
                          </button>
                          <button
                            onClick={() => handleDeactivate(u.user_id)}
                            disabled={actioningUserId === u.user_id || u.status === "inactive"}
                            className="btn btn-outline"
                            style={{
                              padding: "4px 8px",
                              fontSize: 11,
                              borderColor: "#f87171",
                              color: "#ef4444",
                            }}
                          >
                            {u.status === "inactive" ? "Deactivated" : "Deactivate"}
                          </button>
                        </td>
                      </tr>

                      {rowActionError?.userId === u.user_id && (
                        <tr key={`${u.user_id}-error`}>
                          <td colSpan={5} style={{ padding: "0 10px 10px", color: "#ef4444", fontSize: 12 }}>
                            {rowActionError.message}
                          </td>
                        </tr>
                      )}

                      {expandedUserId === u.user_id && (
                        <tr key={`${u.user_id}-keys`}>
                          <td colSpan={5} style={{ padding: "0 10px 16px" }}>
                            <div
                              style={{
                                background: "var(--bg-main)",
                                border: "1px solid var(--border-color)",
                                borderRadius: "var(--radius-sm)",
                                padding: 14,
                                display: "flex",
                                flexDirection: "column",
                                gap: 12,
                              }}
                            >
                              <h4 style={{ margin: 0, fontSize: 13 }}>API Keys — {u.username}</h4>

                              {keyError && <div style={{ fontSize: 12, color: "#ef4444" }}>{keyError}</div>}

                              {justCreatedKey?.userId === u.user_id && (
                                <div
                                  style={{
                                    fontSize: 12,
                                    padding: 10,
                                    borderRadius: 6,
                                    background: "#fffbeb",
                                    border: "1px solid #fde68a",
                                    color: "#92400e",
                                  }}
                                >
                                  Copy this key now — it will not be shown again:{" "}
                                  <code style={{ fontFamily: "var(--font-mono)" }}>{justCreatedKey.rawKey}</code>
                                </div>
                              )}

                              {keysLoading ? (
                                <LoadingSpinner label="Loading keys…" />
                              ) : (
                                <table className="table" style={{ border: "none" }}>
                                  <thead>
                                    <tr>
                                      <th style={{ padding: "6px 8px", fontSize: 10.5 }}>Key</th>
                                      <th style={{ padding: "6px 8px", fontSize: 10.5 }}>Status</th>
                                      <th style={{ padding: "6px 8px", fontSize: 10.5 }}>Entitlements</th>
                                      <th style={{ padding: "6px 8px", fontSize: 10.5, textAlign: "right" }}>Actions</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {(keysByUser[u.user_id] ?? []).length === 0 ? (
                                      <tr>
                                        <td colSpan={4} style={{ textAlign: "center", color: "var(--text-light)", padding: 12, fontSize: 12 }}>
                                          No API keys yet.
                                        </td>
                                      </tr>
                                    ) : (
                                      (keysByUser[u.user_id] ?? []).map((k) => (
                                        <tr key={k.key_id}>
                                          <td style={{ padding: "6px 8px", fontSize: 12 }}>
                                            <code style={{ fontFamily: "var(--font-mono)" }}>{k.key_prefix}…</code>
                                            {k.label ? ` (${k.label})` : ""}
                                          </td>
                                          <td style={{ padding: "6px 8px" }}>
                                            <span className={`badge ${k.status === "active" ? "badge-green" : "badge-red"}`}>
                                              {k.status}
                                            </span>
                                          </td>
                                          <td style={{ padding: "6px 8px", fontSize: 11.5, color: "var(--text-muted)" }}>
                                            {k.model_entitlements.length === 0
                                              ? "All models"
                                              : k.model_entitlements.join(", ")}
                                          </td>
                                          <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                            <button
                                              onClick={() => handleRevokeKey(u.user_id, k.key_id)}
                                              disabled={k.status !== "active"}
                                              className="btn btn-outline"
                                              style={{ padding: "3px 8px", fontSize: 10.5, borderColor: "#f87171", color: "#ef4444" }}
                                            >
                                              Revoke
                                            </button>
                                          </td>
                                        </tr>
                                      ))
                                    )}
                                  </tbody>
                                </table>
                              )}

                              <div
                                style={{
                                  display: "flex",
                                  flexWrap: "wrap",
                                  gap: 10,
                                  alignItems: "flex-end",
                                  paddingTop: 8,
                                  borderTop: "1px solid var(--border-color)",
                                }}
                              >
                                <div className="form-group" style={{ margin: 0 }}>
                                  <label className="form-label" style={{ fontSize: 11 }}>New key label</label>
                                  <input
                                    type="text"
                                    placeholder="e.g. dev laptop"
                                    value={newKeyLabel}
                                    onChange={(e) => setNewKeyLabel(e.target.value)}
                                    className="form-input"
                                    style={{ padding: "6px 10px", fontSize: 12 }}
                                  />
                                </div>
                                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                                    Model entitlements (none = all models)
                                  </span>
                                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                                    {activeModelNames.map((name) => (
                                      <label key={name} style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 4 }}>
                                        <input
                                          type="checkbox"
                                          checked={newKeyEntitlements.includes(name)}
                                          onChange={() => toggleEntitlement(name)}
                                        />
                                        {name}
                                      </label>
                                    ))}
                                  </div>
                                </div>
                                <button
                                  onClick={() => handleCreateKey(u.user_id)}
                                  disabled={creatingKey}
                                  className="btn"
                                  style={{ padding: "6px 14px", fontSize: 12 }}
                                >
                                  {creatingKey ? "Generating…" : "Generate Key"}
                                </button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
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
