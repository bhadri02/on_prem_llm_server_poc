/**
 * ErrorBanner
 *
 * Dismissible error banner that surfaces HTTP errors to the user.
 * Scoped per view — the parent controls mount/unmount.
 *
 * Accessibility: role="alert" so screen-readers announce the message
 * immediately on mount. The dismiss button has an aria-label.
 */

interface ErrorBannerProps {
  statusCode: number;
  message: string;
  onDismiss: () => void;
}

export default function ErrorBanner({ statusCode, message, onDismiss }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      style={{
        background: "#fee2e2",
        border: "1px solid #ef4444",
        padding: "12px 16px",
        borderRadius: 6,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: 16,
      }}
    >
      <div>
        <strong>Error {statusCode}</strong>: {message}
      </div>
      <button
        onClick={onDismiss}
        aria-label="Dismiss error"
        style={{
          marginLeft: 16,
          background: "none",
          border: "none",
          cursor: "pointer",
          fontSize: 16,
          lineHeight: 1,
          color: "#b91c1c",
          flexShrink: 0,
        }}
      >
        ✕
      </button>
    </div>
  );
}
