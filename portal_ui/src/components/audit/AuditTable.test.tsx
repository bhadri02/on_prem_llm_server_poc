/**
 * AuditTable unit tests
 * Requirements: 4.1, 4.8
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import AuditTable from "./AuditTable";
import type { AuditEvent } from "../../types";

function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    audit_id: "a1",
    request_id: "req-1111-2222-3333-4444",
    timestamp_utc: "2024-01-01T12:00:00Z",
    user_id: "user-1",
    department: null,
    model_used: null,
    layer: "api_gateway",
    event_type: "request_received",
    prompt_tokens: null,
    completion_tokens: null,
    latency_ms: 42,
    pii_actions: [],
    policy_decisions: [],
    outcome: "pass",
    error_code: null,
    ...overrides,
  };
}

describe("AuditTable", () => {
  it("shows empty-state message when no events provided", () => {
    render(<AuditTable events={[]} onRequestIdClick={vi.fn()} />);
    expect(screen.getByText(/no audit records found/i)).toBeInTheDocument();
  });

  it("renders correct column headers", () => {
    const event = makeEvent();
    render(<AuditTable events={[event]} onRequestIdClick={vi.fn()} />);
    expect(screen.getByText(/timestamp/i)).toBeInTheDocument();
    expect(screen.getByText(/request id/i)).toBeInTheDocument();
    expect(screen.getByText(/layer/i)).toBeInTheDocument();
    expect(screen.getByText(/event type/i)).toBeInTheDocument();
    expect(screen.getByText(/user id/i)).toBeInTheDocument();
    expect(screen.getByText(/outcome/i)).toBeInTheDocument();
    expect(screen.getByText(/latency/i)).toBeInTheDocument();
  });

  it("renders event data in the table row", () => {
    const event = makeEvent({ outcome: "pass", latency_ms: 123 });
    render(<AuditTable events={[event]} onRequestIdClick={vi.fn()} />);
    expect(screen.getByText("req-1111-2222-3333-4444")).toBeInTheDocument();
    expect(screen.getByText("user-1")).toBeInTheDocument();
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(screen.getByText("123")).toBeInTheDocument();
  });

  it("shows '—' when latency_ms is null", () => {
    const event = makeEvent({ latency_ms: null });
    render(<AuditTable events={[event]} onRequestIdClick={vi.fn()} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("calls onRequestIdClick with the request_id when clicked", () => {
    const onRequestIdClick = vi.fn();
    const event = makeEvent({ request_id: "click-target-id" });
    render(<AuditTable events={[event]} onRequestIdClick={onRequestIdClick} />);
    fireEvent.click(screen.getByText("click-target-id"));
    expect(onRequestIdClick).toHaveBeenCalledWith("click-target-id");
  });
});
