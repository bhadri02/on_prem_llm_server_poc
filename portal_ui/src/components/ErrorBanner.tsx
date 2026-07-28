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
      className="error-banner"
    >
      <div style={{ fontWeight: 500 }}>
        <strong style={{ fontWeight: 700 }}>Error {statusCode}</strong>: {message}
      </div>
      <button
        onClick={onDismiss}
        aria-label="Dismiss error"
        className="error-banner-close"
        style={{ marginLeft: 16 }}
      >
        ✕
      </button>
    </div>
  );
}
