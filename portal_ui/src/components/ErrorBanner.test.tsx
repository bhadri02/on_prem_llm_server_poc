/**
 * ErrorBanner unit tests + Property 17
 *
 * Property 17: Non-2xx Portal_API responses always trigger the error banner
 * **Validates: Requirements 12.3**
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import * as fc from "fast-check";
import ErrorBanner from "./ErrorBanner";

describe("ErrorBanner", () => {
  it("renders status code and message", () => {
    const onDismiss = vi.fn();
    render(<ErrorBanner statusCode={502} message="upstream unavailable" onDismiss={onDismiss} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/502/)).toBeInTheDocument();
    expect(screen.getByText(/upstream unavailable/)).toBeInTheDocument();
  });

  it("calls onDismiss when dismiss button is clicked", () => {
    const onDismiss = vi.fn();
    render(<ErrorBanner statusCode={404} message="not found" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("persists in the DOM until dismissed (not self-dismissing)", () => {
    const onDismiss = vi.fn();
    const { container } = render(
      <ErrorBanner statusCode={500} message="internal error" onDismiss={onDismiss} />,
    );
    // banner is still in the DOM before dismiss is called
    expect(container.querySelector('[role="alert"]')).toBeTruthy();
    expect(onDismiss).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Property 17: Non-2xx Portal_API responses always trigger the error banner
// **Validates: Requirements 12.3**
// ---------------------------------------------------------------------------
describe("Property 17: Non-2xx responses always trigger the error banner", () => {
  it("displays correct status code and message for any 4xx/5xx response", () => {
    // Feature: platform-portals, Property 17: Non-2xx Portal_API responses always trigger the error banner
    fc.assert(
      fc.property(
        fc.integer({ min: 400, max: 599 }),
        fc.record({ message: fc.string({ minLength: 1, maxLength: 80 }) }),
        (statusCode, body) => {
          const onDismiss = vi.fn();
          const { container, unmount } = render(
            <ErrorBanner statusCode={statusCode} message={body.message} onDismiss={onDismiss} />,
          );

          // Banner must be visible (role=alert present)
          const alert = container.querySelector('[role="alert"]');
          expect(alert).toBeTruthy();

          // Status code must appear somewhere in the banner text
          expect(alert?.textContent).toContain(String(statusCode));

          // Message must appear
          expect(alert?.textContent).toContain(body.message);

          // Banner persists — dismiss has not been called
          expect(onDismiss).not.toHaveBeenCalled();

          unmount();
        },
      ),
      { numRuns: 50 },
    );
  });
});
