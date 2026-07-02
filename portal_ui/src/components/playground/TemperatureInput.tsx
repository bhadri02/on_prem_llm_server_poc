/**
 * TemperatureInput
 *
 * Float input for the LLM temperature parameter.
 * Range: 0.0 – 2.0, step 0.1, default 0.7.
 * Performs client-side range validation and shows an inline error when the
 * entered value falls outside the allowed range.
 *
 * Accessibility: the <input> is associated with a <label> via htmlFor/id.
 *
 * Requirements: 2.2
 */

interface TemperatureInputProps {
  value: number;
  onChange: (val: number) => void;
  disabled?: boolean;
}

const MIN = 0.0;
const MAX = 2.0;
const STEP = 0.1;

export default function TemperatureInput({
  value,
  onChange,
  disabled = false,
}: TemperatureInputProps) {
  // Track the raw text the user is typing so we don't clobber mid-entry edits
  const isOutOfRange = value < MIN || value > MAX;

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const parsed = parseFloat(e.target.value);
    if (!isNaN(parsed)) {
      onChange(parsed);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label
        htmlFor="temperature-input"
        style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}
      >
        Temperature
      </label>

      <input
        id="temperature-input"
        type="number"
        min={MIN}
        max={MAX}
        step={STEP}
        value={value}
        onChange={handleChange}
        disabled={disabled}
        aria-label="Temperature"
        aria-describedby={isOutOfRange ? "temperature-error" : undefined}
        aria-invalid={isOutOfRange}
        style={{
          padding: "6px 10px",
          borderRadius: 6,
          border: isOutOfRange ? "1px solid #ef4444" : "1px solid #d1d5db",
          fontSize: 14,
          width: 90,
          background: disabled ? "#f3f4f6" : "#ffffff",
          cursor: disabled ? "not-allowed" : "text",
        }}
      />

      {isOutOfRange && (
        <p
          id="temperature-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 12, margin: 0 }}
        >
          Temperature must be between {MIN.toFixed(1)} and {MAX.toFixed(1)}.
        </p>
      )}
    </div>
  );
}
