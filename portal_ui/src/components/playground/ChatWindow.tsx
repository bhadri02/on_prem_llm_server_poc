/**
 * ChatWindow
 *
 * Message composition textarea + Send button, response display, request_id
 * label, and a "View Audit Trail" button that surfaces after a successful
 * response.
 *
 * Accessibility:
 *   - Textarea has an aria-label
 *   - Send button has an accessible label that reflects loading state
 *   - Response region is role="region" with aria-label
 *   - request_id is presented as readable text (not an aria-hidden element)
 *
 * Requirements: 2.3, 2.5, 2.6, 2.7
 */

import { useState } from "react";

interface ChatResponse {
  content: string;
  requestId: string;
}

interface ChatWindowProps {
  disabled: boolean;
  isLoading: boolean;
  response: ChatResponse | null;
  onSend: (message: string) => void;
  onViewAudit: (requestId: string) => void;
}

const MAX_LENGTH = 4000;

export default function ChatWindow({
  disabled,
  isLoading,
  response,
  onSend,
  onViewAudit,
}: ChatWindowProps) {
  const [message, setMessage] = useState("");

  function handleSend() {
    const trimmed = message.trim();
    if (!trimmed || disabled || isLoading) return;
    onSend(trimmed);
    setMessage("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Ctrl+Enter or Cmd+Enter submits
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  }

  const sendDisabled = disabled || isLoading || message.trim().length === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Message composition area */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <label
          htmlFor="chat-message"
          style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}
        >
          Message
        </label>
        <textarea
          id="chat-message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          maxLength={MAX_LENGTH}
          rows={4}
          disabled={disabled || isLoading}
          aria-label="Chat message"
          placeholder="Type your message… (Ctrl+Enter to send)"
          style={{
            padding: "8px 12px",
            borderRadius: 6,
            border: "1px solid #d1d5db",
            fontSize: 14,
            resize: "vertical",
            fontFamily: "inherit",
            background: disabled || isLoading ? "#f3f4f6" : "#ffffff",
            cursor: disabled || isLoading ? "not-allowed" : "text",
          }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 12, color: "#9ca3af" }}>
            {message.length} / {MAX_LENGTH}
          </span>
          <button
            onClick={handleSend}
            disabled={sendDisabled}
            aria-label={isLoading ? "Sending…" : "Send message"}
            style={{
              padding: "8px 20px",
              borderRadius: 6,
              border: "none",
              background: sendDisabled ? "#93c5fd" : "#3b82f6",
              color: "#ffffff",
              fontWeight: 600,
              fontSize: 14,
              cursor: sendDisabled ? "not-allowed" : "pointer",
              transition: "background 0.15s",
            }}
          >
            {isLoading ? "Sending…" : "Send"}
          </button>
        </div>
      </div>

      {/* Response display */}
      {response && (
        <section
          role="region"
          aria-label="Assistant response"
          style={{
            background: "#f0f9ff",
            border: "1px solid #bae6fd",
            borderRadius: 6,
            padding: "12px 16px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          <p style={{ margin: 0, fontSize: 14, color: "#1e3a5f", whiteSpace: "pre-wrap" }}>
            {response.content}
          </p>

          <div style={{ fontSize: 12, color: "#6b7280" }}>
            <span style={{ fontWeight: 600 }}>Request ID: </span>
            <code style={{ fontFamily: "monospace" }}>{response.requestId}</code>
          </div>

          <button
            onClick={() => onViewAudit(response.requestId)}
            aria-label={`View audit trail for request ${response.requestId}`}
            style={{
              alignSelf: "flex-start",
              padding: "6px 14px",
              borderRadius: 6,
              border: "1px solid #3b82f6",
              background: "#ffffff",
              color: "#3b82f6",
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            View Audit Trail
          </button>
        </section>
      )}
    </div>
  );
}
