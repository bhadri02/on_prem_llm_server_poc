/**
 * PlaygroundView
 *
 * Composes ModelSelector, TemperatureInput, ChatWindow, LoadingSpinner, and
 * ErrorBanner into the main playground experience.
 *
 * State owned here:
 *   - selectedModel  — set by ModelSelector
 *   - temperature    — set by TemperatureInput (default 0.7)
 *   - chatResponse   — last assistant reply + request_id
 *   - error          — ApiError captured during postChat
 *   - inFlight       — true while the chat request is pending
 *   - modelLoadError — true when ModelSelector reports a fetch failure
 *
 * Send is disabled while: request is in-flight OR the model list
 * failed to load (modelLoadError) OR the temperature is out of range.
 *
 * "View Audit Trail" navigates to /audit?request_id=<uuid>.
 *
 * Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import ModelSelector from "../components/playground/ModelSelector";
import TemperatureInput from "../components/playground/TemperatureInput";
import ChatWindow from "../components/playground/ChatWindow";
import LoadingSpinner from "../components/LoadingSpinner";
import ErrorBanner from "../components/ErrorBanner";
import * as portalClient from "../api/portalClient";
import { ApiError } from "../types";

const DEFAULT_TEMPERATURE = 0.7;
const TEMP_MIN = 0.0;
const TEMP_MAX = 2.0;

/** Narrows the unknown postChat response to extract content and request_id. */
function extractChatResponse(raw: unknown): { content: string; requestId: string } {
  if (raw === null || typeof raw !== "object") {
    throw new Error("Unexpected response format from chat endpoint.");
  }

  const obj = raw as Record<string, unknown>;

  // Extract request_id
  const requestId =
    typeof obj["request_id"] === "string" ? obj["request_id"] : "";

  // Try the IMF envelope: response.content
  const responseEnv = obj["response"];
  if (responseEnv !== null && typeof responseEnv === "object") {
    const respObj = responseEnv as Record<string, unknown>;
    if (typeof respObj["content"] === "string") {
      return { content: respObj["content"], requestId };
    }
  }

  // Try OpenAI-compatible: choices[0].message.content
  const choices = obj["choices"];
  if (Array.isArray(choices) && choices.length > 0) {
    const first = choices[0] as Record<string, unknown>;
    const msgEnv = first["message"];
    if (msgEnv !== null && typeof msgEnv === "object") {
      const msg = msgEnv as Record<string, unknown>;
      if (typeof msg["content"] === "string") {
        return { content: msg["content"], requestId };
      }
    }
  }

  // Fallback: stringify the whole thing
  return { content: JSON.stringify(raw, null, 2), requestId };
}

export default function PlaygroundView() {
  const navigate = useNavigate();

  const [selectedModel, setSelectedModel] = useState<string>("");
  const [temperature, setTemperature] = useState<number>(DEFAULT_TEMPERATURE);
  const [chatResponse, setChatResponse] = useState<{
    content: string;
    requestId: string;
  } | null>(null);
  const [error, setError] = useState<{ statusCode: number; message: string } | null>(
    null,
  );
  const [inFlight, setInFlight] = useState(false);
  const [modelLoadError, setModelLoadError] = useState(false);

  const temperatureOutOfRange = temperature < TEMP_MIN || temperature > TEMP_MAX;
  const sendDisabled = inFlight || modelLoadError || temperatureOutOfRange;

  async function handleSend(message: string) {
    if (sendDisabled) return;

    setInFlight(true);
    setError(null);
    setChatResponse(null);

    try {
      const raw = await portalClient.postChat({
        model: selectedModel,
        messages: [{ role: "user", content: message }],
        temperature,
      });

      const parsed = extractChatResponse(raw);
      setChatResponse(parsed);
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ statusCode: err.status, message: err.message });
      } else if (err instanceof Error) {
        setError({ statusCode: 0, message: err.message });
      } else {
        setError({ statusCode: 0, message: "An unknown error occurred." });
      }
    } finally {
      setInFlight(false);
    }
  }

  function handleViewAudit(requestId: string) {
    navigate(`/audit?request_id=${encodeURIComponent(requestId)}`);
  }

  return (
    <div className="playground-layout">
      <h1>Playground</h1>

      {/* Error banner — displayed when postChat fails */}
      {error && (
        <ErrorBanner
          statusCode={error.statusCode}
          message={error.message}
          onDismiss={() => setError(null)}
        />
      )}

      {/* Controls row: model selector + temperature */}
      <div
        className="card"
        style={{
          display: "flex",
          gap: 32,
          flexWrap: "wrap",
          alignItems: "flex-start",
        }}
      >
        <ModelSelector
          onModelChange={setSelectedModel}
          onLoadError={setModelLoadError}
          disabled={inFlight}
        />

        <TemperatureInput
          value={temperature}
          onChange={setTemperature}
          disabled={inFlight}
        />
      </div>

      {/* Chat window */}
      <div className="card">
        <ChatWindow
          disabled={sendDisabled}
          isLoading={inFlight}
          response={chatResponse}
          onSend={handleSend}
          onViewAudit={handleViewAudit}
        />
      </div>

      {/* Loading spinner — visible while request is in-flight */}
      {inFlight && <LoadingSpinner label="Waiting for response…" />}
    </div>
  );
}
