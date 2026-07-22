import { useState, useEffect } from "react";

interface Policy {
  id: string;
  name: string;
  category: string;
  description: string;
  isEnabled: boolean;
  pattern: RegExp;
  mask: string;
}

const INITIAL_POLICIES: Policy[] = [
  {
    id: "email",
    name: "Email Address",
    category: "Identifiers",
    description: "Detect and mask email addresses (e.g. user@domain.com)",
    isEnabled: true,
    pattern: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g,
    mask: "[REDACTED_EMAIL]",
  },
  {
    id: "ssn",
    name: "Social Security Number",
    category: "Identifiers",
    description: "Detect and mask U.S. Social Security Numbers (e.g. 000-12-3456)",
    isEnabled: true,
    pattern: /\b\d{3}-\d{2}-\d{4}\b/g,
    mask: "[REDACTED_SSN]",
  },
  {
    id: "cc",
    name: "Credit Card",
    category: "Financials",
    description: "Detect and mask credit card numbers (Visa, Mastercard, etc.)",
    isEnabled: true,
    pattern: /\b(?:\d[ -]*?){13,16}\b/g,
    mask: "[REDACTED_CARD_NUMBER]",
  },
  {
    id: "api_key",
    name: "API Secret Key",
    category: "Credentials",
    description: "Detect and mask exposed API keys and tokens (e.g. sk-proj-...)",
    isEnabled: true,
    pattern: /\b(?:sk|key|token|auth|secret)-[a-zA-Z0-9]{12,}\b/g,
    mask: "[REDACTED_API_KEY]",
  },
];

const MOCK_SAMPLE_TEXT =
  "Contact support at developer.support@gwcdata.ai. Use token auth-99211029481239 for sandbox calls. User SSN 102-39-4482 has card 4111-2222-3333-4444.";

export default function RedactionView() {
  const [policies, setPolicies] = useState<Policy[]>(INITIAL_POLICIES);
  const [inputText, setInputText] = useState(MOCK_SAMPLE_TEXT);
  const [redactedText, setRedactedText] = useState("");

  function togglePolicy(id: string) {
    setPolicies((prev) =>
      prev.map((p) => (p.id === id ? { ...p, isEnabled: !p.isEnabled } : p))
    );
  }

  // Real-time redaction logic
  useEffect(() => {
    let result = inputText;
    policies.forEach((policy) => {
      if (policy.isEnabled) {
        result = result.replace(policy.pattern, policy.mask);
      }
    });
    setRedactedText(result);
  }, [inputText, policies]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div>
        <h1>Data Redump & Redaction Policies</h1>
        <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: -16, marginBottom: 0 }}>
          Manage real-time PII redaction filters and policy rules protecting data outbound.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
        {/* Policies Config Card */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <h2 style={{ margin: 0 }}>Active Redaction Filters</h2>
          
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {policies.map((p) => (
              <div
                key={p.id}
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
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 13.5, color: "var(--text-main)" }}>{p.name}</span>
                    <span
                      className="badge"
                      style={{
                        fontSize: 10,
                        padding: "1px 6px",
                        backgroundColor: p.isEnabled ? "var(--primary-light)" : "#e5e7eb",
                        color: p.isEnabled ? "var(--primary)" : "#6b7280",
                        border: "1px solid " + (p.isEnabled ? "var(--primary-border)" : "#d1d5db"),
                      }}
                    >
                      {p.category}
                    </span>
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>{p.description}</div>
                </div>
                <button
                  onClick={() => togglePolicy(p.id)}
                  style={{
                    width: 44,
                    height: 24,
                    borderRadius: 12,
                    background: p.isEnabled ? "var(--primary)" : "#d1d5db",
                    border: "none",
                    position: "relative",
                    cursor: "pointer",
                    transition: "background-color 0.2s",
                    padding: 0,
                    flexShrink: 0,
                  }}
                  aria-label={`Toggle ${p.name} Redaction`}
                >
                  <span
                    style={{
                      position: "absolute",
                      top: 2,
                      left: p.isEnabled ? 22 : 2,
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
            ))}
          </div>
        </div>

        {/* Sandbox Tester Card */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ margin: 0 }}>Interactive Sandbox</h2>
            <button
              onClick={() => setInputText(MOCK_SAMPLE_TEXT)}
              className="btn btn-outline"
              style={{ padding: "4px 10px", fontSize: 12 }}
            >
              Reset Sample Text
            </button>
          </div>

          <div className="form-group">
            <label htmlFor="redact-input" className="form-label">
              Input Text (Raw)
            </label>
            <textarea
              id="redact-input"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              className="form-textarea"
              rows={4}
              placeholder="Type or paste text with emails, credit cards, or SSNs..."
              style={{ fontSize: 13, fontFamily: "inherit" }}
            />
          </div>

          <div className="form-group">
            <div className="form-label">Redacted Output (Simulated)</div>
            <div
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--primary-border)",
                background: "var(--primary-light)",
                fontSize: 13,
                minHeight: 90,
                color: "var(--primary-text)",
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
                fontFamily: "var(--font-mono)",
              }}
            >
              {redactedText || <span style={{ color: "var(--text-light)" }}>No input entered yet.</span>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
