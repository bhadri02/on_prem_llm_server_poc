/**
 * ChatView
 *
 * Interactive Chat view. Streams the assistant's reply token-by-token via
 * portalClient.streamChatCompletion (real end-to-end SSE now exists in the
 * core pipeline: api_gateway -> security_layer -> intelligent_router ->
 * inference_adapter).
 *
 * - Model dropdown populated by GET /portal/chat/models (already
 *   entitlement-filtered server-side — not a reuse of ModelSelector.tsx,
 *   which mixes in hardcoded demo models that don't belong on a real
 *   chat surface).
 * - In-browser-only session history (Section 3.2 — not persisted to a DB).
 * - Enter submits, Shift+Enter inserts a newline (Section 3.3).
 * - Errors (400/403/429/502, or an in-band stream error) surfaced inline via ErrorBanner.
 */

import { useEffect, useRef, useState } from "react";
import * as portalClient from "../api/portalClient";
import { ApiError, ModelRecord } from "../types";
import ChatMessageList, { ChatMessage } from "../components/chat/ChatMessageList";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

export default function ChatView() {
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState("");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<{ statusCode: number; message: string } | null>(null);
  const abortStreamRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      abortStreamRef.current?.();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    portalClient
      .getChatModels()
      .then((data) => {
        if (cancelled) return;
        setModels(data);
        const firstEntitled = data.find((m) => m.entitled !== false) ?? data[0];
        if (firstEntitled) setSelectedModel(firstEntitled.name);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setModelsError(err instanceof ApiError ? `Error ${err.status}: ${err.message}` : String(err));
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const sendDisabled = sending || modelsLoading || !selectedModel || !input.trim();

  function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || sendDisabled) return;

    const nextMessages: ChatMessage[] = [...messages, { role: "user", content: trimmed }];
    // Append an empty assistant placeholder that gets filled in as deltas arrive.
    setMessages([...nextMessages, { role: "assistant", content: "" }]);
    setInput("");
    setSending(true);
    setError(null);

    abortStreamRef.current = portalClient.streamChatCompletion(
      { model: selectedModel, messages: nextMessages, temperature: 0.7 },
      {
        onDelta: (delta) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, content: last.content + delta };
            return updated;
          });
        },
        onDone: () => {
          setSending(false);
          abortStreamRef.current = null;
        },
        onError: (message) => {
          setError({ statusCode: 0, message });
          // Drop the (possibly partial) assistant placeholder — the error banner covers it.
          setMessages((prev) => prev.slice(0, -1));
          setSending(false);
          abortStreamRef.current = null;
        },
      }
    );
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleNewChat() {
    setMessages([]);
    setError(null);
  }

  return (
    <div className="playground-layout">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <h1 style={{ margin: 0 }}>Chat</h1>
        <button onClick={handleNewChat} className="btn btn-secondary" style={{ padding: "8px 16px", fontSize: 13 }}>
          New chat
        </button>
      </div>

      {error && (
        <ErrorBanner
          statusCode={error.statusCode}
          message={error.message}
          onDismiss={() => setError(null)}
        />
      )}

      <div className="card" style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-end" }}>
        <div className="form-group" style={{ margin: 0 }}>
          <label htmlFor="chat-model-select" className="form-label">Model</label>
          {modelsError ? (
            <p role="alert" className="badge badge-red" style={{ margin: 0, display: "inline-block" }}>
              {modelsError}
            </p>
          ) : (
            <select
              id="chat-model-select"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={modelsLoading || sending}
              className="form-select"
              style={{ padding: "8px 12px", fontSize: 13.5, minWidth: 220 }}
            >
              {modelsLoading && <option value="">Loading models…</option>}
              {!modelsLoading && models.length === 0 && <option value="">No models available</option>}
              {models.map((m) => (
                <option key={m.name} value={m.name} disabled={m.entitled === false}>
                  {m.entitled === false ? `🔒 ${m.name} (not entitled)` : m.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="card" style={{ minHeight: 320, display: "flex", flexDirection: "column", gap: 16 }}>
        <ChatMessageList messages={messages} />
        {sending && messages[messages.length - 1]?.content === "" && (
          <LoadingSpinner label="Waiting for response…" />
        )}
      </div>

      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label htmlFor="chat-input" className="form-label">Message</label>
        <textarea
          id="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          disabled={sending}
          aria-label="Chat message"
          placeholder="Type your message… (Enter to send, Shift+Enter for a new line)"
          className="form-textarea"
          style={{ resize: "vertical" }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            onClick={handleSend}
            disabled={sendDisabled}
            aria-label={sending ? "Sending…" : "Send message"}
            className="btn btn-primary"
            style={{ padding: "8px 24px" }}
          >
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
