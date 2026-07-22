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
    <div className="form-group">
      <label
        htmlFor="temperature-input"
        className="form-label"
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
        className="form-input"
        style={{
          width: 100,
          borderColor: isOutOfRange ? "var(--accent-red-text)" : undefined,
        }}
      />

      {isOutOfRange && (
        <p
          id="temperature-error"
          role="alert"
          className="badge badge-red"
          style={{ margin: 0, marginTop: 4, display: "inline-block", alignSelf: "flex-start" }}
        >
          Temperature must be between {MIN.toFixed(1)} and {MAX.toFixed(1)}.
        </p>
      )}
    </div>
  );
}
