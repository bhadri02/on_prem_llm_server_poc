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
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Message composition area */}
      <div className="form-group" style={{ gap: 8 }}>
        <label
          htmlFor="chat-message"
          className="form-label"
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
          className="form-textarea"
          style={{ resize: "vertical" }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 12, color: "var(--text-light)" }}>
            {message.length} / {MAX_LENGTH}
          </span>
          <button
            onClick={handleSend}
            disabled={sendDisabled}
            aria-label={isLoading ? "Sending…" : "Send message"}
            className="btn btn-primary"
            style={{ padding: "8px 24px" }}
          >
            {isLoading ? "Sending…" : "Send"}
          </button>
        </div>
      </div>

      {/* Response display */}
      {response && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="form-label">Response</div>
          
          <section
            role="region"
            aria-label="Assistant response"
            className="chat-message-bubble chat-bubble-assistant"
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
          >
            <p style={{ margin: 0, fontSize: "14.5px", color: "var(--text-main)", whiteSpace: "pre-wrap" }}>
              {response.content}
            </p>

            <div style={{ fontSize: 12, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontWeight: 600 }}>Request ID:</span>
              <code style={{ fontFamily: "var(--font-mono)", background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                {response.requestId}
              </code>
            </div>

            <button
              onClick={() => onViewAudit(response.requestId)}
              aria-label={`View audit trail for request ${response.requestId}`}
              className="btn btn-outline"
              style={{
                alignSelf: "flex-start",
                padding: "6px 14px",
                fontSize: 13,
              }}
            >
              View Audit Trail
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
