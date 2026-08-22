/**
 * PlaygroundView
 *
 * Composes ModelSelector, TemperatureInput, ChatWindow, LoadingSpinner, and
 * ErrorBanner into the main playground experience. Streams the assistant's
 * reply token-by-token via portalClient.streamPlaygroundChat.
 *
 * State owned here:
 *   - selectedModel  — set by ModelSelector
 *   - temperature    — set by TemperatureInput (default 0.7)
 *   - chatResponse   — in-progress/last assistant reply + request_id, filled
 *                      incrementally as stream deltas arrive
 *   - error          — message from an in-band stream error or transport failure
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

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import ModelSelector from "../components/playground/ModelSelector";
import TemperatureInput from "../components/playground/TemperatureInput";
import ChatWindow from "../components/playground/ChatWindow";
import LoadingSpinner from "../components/LoadingSpinner";
import ErrorBanner from "../components/ErrorBanner";
import * as portalClient from "../api/portalClient";

const DEFAULT_TEMPERATURE = 0.7;
const TEMP_MIN = 0.0;
const TEMP_MAX = 2.0;

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
  const abortStreamRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      abortStreamRef.current?.();
    };
  }, []);

  const temperatureOutOfRange = temperature < TEMP_MIN || temperature > TEMP_MAX;
  const sendDisabled = inFlight || modelLoadError || temperatureOutOfRange;

  function handleSend(message: string) {
    if (sendDisabled) return;

    setInFlight(true);
    setError(null);
    setChatResponse({ content: "", requestId: "" });

    abortStreamRef.current = portalClient.streamPlaygroundChat(
      {
        model: selectedModel,
        messages: [{ role: "user", content: message }],
        temperature,
      },
      {
        onId: (requestId) => {
          setChatResponse((prev) => ({ content: prev?.content ?? "", requestId }));
        },
        onDelta: (delta) => {
          setChatResponse((prev) => ({
            content: (prev?.content ?? "") + delta,
            requestId: prev?.requestId ?? "",
          }));
        },
        onDone: () => {
          setInFlight(false);
          abortStreamRef.current = null;
        },
        onError: (message) => {
          setError({ statusCode: 0, message });
          setChatResponse(null);
          setInFlight(false);
          abortStreamRef.current = null;
        },
      }
    );
  }

  function handleViewAudit(requestId: string) {
    navigate(`/audit?request_id=${encodeURIComponent(requestId)}`);
  }

  return (
    <div className="playground-layout">
      <h1>Playground</h1>

      {/* Error banner — displayed on an in-band stream error or transport failure */}
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

      {/* Loading spinner — visible until the first delta arrives */}
      {inFlight && !chatResponse?.content && <LoadingSpinner label="Waiting for response…" />}
    </div>
  );
}
