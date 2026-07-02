/**
 * ModelSelector
 *
 * Populates a dropdown from GET /portal/models via portalClient.getModels().
 * Signals the parent when loading fails so the parent can disable Send.
 *
 * Accessibility: the <select> is associated with a visible <label>.
 *
 * Requirements: 2.1
 */

import { useEffect, useState } from "react";
import * as portalClient from "../../api/portalClient";
import { ApiError, ModelRecord } from "../../types";

interface ModelSelectorProps {
  onModelChange: (model: string) => void;
  onLoadError: (hasError: boolean) => void;
  disabled?: boolean;
}

export default function ModelSelector({
  onModelChange,
  onLoadError,
  disabled = false,
}: ModelSelectorProps) {
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");

  useEffect(() => {
    let cancelled = false;

    async function fetchModels() {
      setLoading(true);
      setError(null);
      onLoadError(false);

      try {
        const data = await portalClient.getModels();
        if (cancelled) return;

        // Only show active models in the playground selector
        const active = data.models.filter((m) => m.status === "active");
        setModels(active);

        if (active.length > 0) {
          setSelected(active[0].name);
          onModelChange(active[0].name);
        }
      } catch (err) {
        if (cancelled) return;
        const msg =
          err instanceof ApiError
            ? `Error ${err.status}: ${err.message}`
            : "Failed to load models.";
        setError(msg);
        onLoadError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchModels();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setSelected(e.target.value);
    onModelChange(e.target.value);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label
        htmlFor="model-selector"
        style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}
      >
        Model
      </label>

      {error ? (
        <p role="alert" style={{ color: "#b91c1c", fontSize: 13, margin: 0 }}>
          {error}
        </p>
      ) : (
        <select
          id="model-selector"
          value={selected}
          onChange={handleChange}
          disabled={loading || disabled}
          aria-label="Select model"
          aria-busy={loading}
          style={{
            padding: "6px 10px",
            borderRadius: 6,
            border: "1px solid #d1d5db",
            fontSize: 14,
            background: loading || disabled ? "#f3f4f6" : "#ffffff",
            cursor: loading || disabled ? "not-allowed" : "pointer",
            minWidth: 220,
          }}
        >
          {loading ? (
            <option value="">Loading models…</option>
          ) : models.length === 0 ? (
            <option value="">No active models</option>
          ) : (
            models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))
          )}
        </select>
      )}
    </div>
  );
}
