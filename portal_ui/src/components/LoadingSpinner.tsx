/**
 * LoadingSpinner
 *
 * Accessible loading indicator.
 * role="status" announces dynamic content updates to screen readers.
 * The spinning element itself is aria-hidden so only the label is read.
 *
 * The CSS keyframe is injected via a <style> tag once (idempotent due to the
 * unique id on the rule).
 */

const STYLE_ID = "loading-spinner-keyframes";

function ensureKeyframes() {
  if (typeof document !== "undefined" && !document.getElementById(STYLE_ID)) {
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `@keyframes _spin { to { transform: rotate(360deg); } }`;
    document.head.appendChild(style);
  }
}

interface LoadingSpinnerProps {
  label?: string;
}

export default function LoadingSpinner({ label = "Loading…" }: LoadingSpinnerProps) {
  ensureKeyframes();

  return (
    <div
      role="status"
      aria-label={label}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: 16,
        color: "#6b7280",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: 20,
          height: 20,
          border: "3px solid #e5e7eb",
          borderTopColor: "#3b82f6",
          borderRadius: "50%",
          animation: "_spin 0.8s linear infinite",
        }}
      />
      <span>{label}</span>
    </div>
  );
}
