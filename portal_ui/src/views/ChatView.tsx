/**
 * ChatView
 *
 * Interactive Chat view (Phase 4 — non-streaming per the MVP scope
 * decision: no end-to-end SSE exists yet in the core pipeline).
 *
 * - Model dropdown populated by GET /portal/chat/models (already
 *   entitlement-filtered server-side — not a reuse of ModelSelector.tsx,
 *   which mixes in hardcoded demo models that don't belong on a real
 *   chat surface).
 * - In-browser-only session history (Section 3.2 — not persisted to a DB).
 * - Enter submits, Shift+Enter inserts a newline (Section 3.3).
 * - Errors (400/403/429/502) surfaced inline via ErrorBanner.
 */

import { useEffect, useState } from "react";
import * as portalClient from "../api/portalClient";
import { ApiError, ModelRecord } from "../types";
import ChatMessageList, { ChatMessage } from "../components/chat/ChatMessageList";
import ErrorBanner from "../components/ErrorBanner";
import LoadingSpinner from "../components/LoadingSpinner";

/** Narrows the unknown chat-completion response to the assistant's reply text. */
function extractAssistantContent(raw: unknown): string {
  if (raw !== null && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;

    // IMF envelope: response.content
    const responseEnv = obj["response"];
    if (responseEnv !== null && typeof responseEnv === "object") {
      const content = (responseEnv as Record<string, unknown>)["content"];
      if (typeof content === "string") return content;
    }

    // OpenAI-compatible: choices[0].message.content
    const choices = obj["choices"];
    if (Array.isArray(choices) && choices.length > 0) {
      const message = (choices[0] as Record<string, unknown>)["message"];
      if (message !== null && typeof message === "object") {
        const content = (message as Record<string, unknown>)["content"];
        if (typeof content === "string") return content;
      }
    }
  }
  return JSON.stringify(raw, null, 2);
}

export default function ChatView() {
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState("");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<{ statusCode: number; message: string } | null>(null);

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

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || sendDisabled) return;

    const nextMessages: ChatMessage[] = [...messages, { role: "user", content: trimmed }];
    setMessages(nextMessages);
    setInput("");
    setSending(true);
    setError(null);

    try {
      const raw = await portalClient.postChatCompletion({
        model: selectedModel,
        messages: nextMessages,
        temperature: 0.7,
      });
      const content = extractAssistantContent(raw);
      setMessages((prev) => [...prev, { role: "assistant", content }]);
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ statusCode: err.status, message: err.message });
      } else {
        setError({ statusCode: 0, message: String(err) });
      }
    } finally {
      setSending(false);
    }
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
        {sending && <LoadingSpinner label="Waiting for response…" />}
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
