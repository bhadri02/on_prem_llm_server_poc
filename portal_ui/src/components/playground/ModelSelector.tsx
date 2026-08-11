/**
 * ModelSelector
 *
 * Populates a dropdown from GET /portal/models via portalClient.getModels().
 * Signals the parent when loading fails so the parent can disable Send.
 *
 * Accessibility: the trigger button is associated with a visible label.
 *
 * Requirements: 2.1
 */

import { useEffect, useRef, useState } from "react";
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
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchModels() {
      setLoading(true);
      setError(null);
      onLoadError(false);

      try {
        const data = await portalClient.getModels();
        if (cancelled) return;

        const active = data.models.filter((m) => m.status === "active");
        setModels(active);

        if (active.length > 0) {
          const defaultSel = active[0].name;
          setSelected(defaultSel);
          onModelChange(defaultSel);
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
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close when clicking outside
  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, []);

  function handleSelect(name: string) {
    setSelected(name);
    setOpen(false);
    onModelChange(name);
  }

  return (
    <div className="form-group">
      <label htmlFor="model-selector" className="form-label">Model</label>

      {error ? (
        <p role="alert" className="badge badge-red" style={{ margin: 0, display: "inline-block", alignSelf: "flex-start" }}>
          {error}
        </p>
      ) : (
        <>
          <div ref={containerRef} style={{ position: "relative", minWidth: 220 }}>
            {/* Trigger button — exposed as an ARIA combobox so it's a real
                accessible control, not just a styled <div> */}
            <button
              type="button"
              id="model-selector"
              role="combobox"
              aria-label="Select model"
              onClick={() => !loading && !disabled && setOpen((o) => !o)}
              disabled={loading || disabled}
              aria-haspopup="listbox"
              aria-expanded={open}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                padding: "10px 12px 10px 14px",
                borderRadius: 6,
                border: `1.5px solid ${open ? "var(--primary)" : "var(--border-color)"}`,
                background: open ? "#fdfcff" : "#ffffff",
                color: loading ? "var(--text-light)" : "var(--text-main)",
                fontSize: 14,
                fontFamily: "inherit",
                fontWeight: 500,
                cursor: loading || disabled ? "not-allowed" : "pointer",
                boxShadow: open ? "0 0 0 3px rgba(124,58,237,0.12)" : "none",
                transition: "border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease",
                outline: "none",
              }}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {loading ? "Loading models…" : selected || "Select model"}
              </span>
              {/* Animated chevron */}
              <svg
                width="16" height="16" viewBox="0 0 24 24" fill="none"
                stroke="var(--primary)" strokeWidth="2.2"
                strokeLinecap="round" strokeLinejoin="round"
                style={{
                  flexShrink: 0,
                  transform: open ? "rotate(180deg)" : "rotate(0deg)",
                  transition: "transform 0.22s ease",
                }}
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>

            {/* Smooth dropdown panel */}
            <ul
              role="listbox"
              aria-label="Select model"
              style={{
                position: "absolute",
                top: "calc(100% + 6px)",
                left: 0,
                right: 0,
                zIndex: 200,
                margin: 0,
                padding: "4px 0",
                listStyle: "none",
                background: "#ffffff",
                border: "1.5px solid var(--primary-border)",
                borderRadius: 8,
                boxShadow: "0 8px 24px -4px rgba(124,58,237,0.14), 0 2px 8px -2px rgba(124,58,237,0.08)",
                overflow: "hidden",
                // Smooth open/close
                opacity: open ? 1 : 0,
                transform: open ? "translateY(0)" : "translateY(-8px)",
                pointerEvents: open ? "auto" : "none",
                transition: "opacity 0.18s ease, transform 0.18s ease",
              }}
            >
              {models.map((m) => (
                <li
                  key={m.name}
                  role="option"
                  aria-selected={m.name === selected}
                  onClick={() => handleSelect(m.name)}
                  style={{
                    padding: "10px 14px",
                    fontSize: 13.5,
                    fontWeight: m.name === selected ? 600 : 400,
                    color: m.name === selected ? "var(--primary)" : "var(--text-main)",
                    background: m.name === selected ? "var(--primary-light)" : "transparent",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                    transition: "background 0.15s ease, color 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (m.name !== selected) {
                      (e.currentTarget as HTMLElement).style.background = "#faf8ff";
                    }
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.background =
                      m.name === selected ? "var(--primary-light)" : "transparent";
                  }}
                >
                  <span>{m.name}</span>
                  {m.name === selected && (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                      stroke="var(--primary)" strokeWidth="2.5"
                      strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
