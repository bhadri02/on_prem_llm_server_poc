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
      className="spinner-container"
    >
      <span
        aria-hidden="true"
        className="spinner"
      />
      <span>{label}</span>
    </div>
  );
}
