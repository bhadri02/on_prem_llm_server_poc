/**
 * AuditView unit tests
 * Requirements: 4.1, 4.4, 4.5, 4.7, 4.8
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AuditView from "./AuditView";
import * as portalClient from "../api/portalClient";
import type { AuditEvent } from "../types";

vi.mock("../api/portalClient", () => ({
  getAuditEvents: vi.fn(),
  getAuditRequest: vi.fn(),
}));

const mockGetAuditEvents = portalClient.getAuditEvents as ReturnType<typeof vi.fn>;
const mockGetAuditRequest = portalClient.getAuditRequest as ReturnType<typeof vi.fn>;

function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    audit_id: "a1",
    request_id: "req-1234-5678-90ab-cdef",
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

function renderView(initialRoute = "/audit") {
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <AuditView />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AuditView", () => {
  it("renders filter controls and audit heading", async () => {
    mockGetAuditEvents.mockResolvedValue({ events: [] });
    renderView();
    expect(screen.getByText("Audit Viewer")).toBeInTheDocument();
    // Layer and outcome dropdowns are rendered
    expect(screen.getByRole("combobox", { name: /layer/i })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /outcome/i })).toBeInTheDocument();
    // From / To datetime-local inputs are rendered
    expect(screen.getByText("From")).toBeInTheDocument();
    expect(screen.getByText("To")).toBeInTheDocument();
  });

  it("shows empty-state message when no events returned", async () => {
    mockGetAuditEvents.mockResolvedValue({ events: [] });
    renderView();
    await waitFor(() => {
      expect(screen.getByText(/no audit records found/i)).toBeInTheDocument();
    });
  });

  it("renders audit events in a table", async () => {
    mockGetAuditEvents.mockResolvedValue({ events: [makeEvent()] });
    renderView();
    await waitFor(() => {
      expect(screen.getByText("req-1234-5678-90ab-cdef")).toBeInTheDocument();
    });
  });

  it("shows error banner on 502 from audit store", async () => {
    const { ApiError } = await import("../types");
    mockGetAuditEvents.mockRejectedValue(new ApiError(502, "audit-store unreachable"));
    renderView();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/502/)).toBeInTheDocument();
    });
  });

  it("opens detail panel when request_id is clicked", async () => {
    mockGetAuditEvents.mockResolvedValue({ events: [makeEvent()] });
    mockGetAuditRequest.mockResolvedValue({ events: [makeEvent()] });

    renderView();
    await waitFor(() => screen.getByText("req-1234-5678-90ab-cdef"));

    // Click the request_id cell
    fireEvent.click(screen.getByText("req-1234-5678-90ab-cdef"));

    // Detail panel dialog should appear
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("pre-populates detail panel from URL query param ?request_id=", async () => {
    mockGetAuditEvents.mockResolvedValue({ events: [] });
    mockGetAuditRequest.mockResolvedValue({ events: [makeEvent()] });

    renderView("/audit?request_id=prepopulated-id");

    // Detail panel should open immediately with the prepopulated request_id
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("re-fetches when a filter changes", async () => {
    mockGetAuditEvents.mockResolvedValue({ events: [] });
    renderView();
    await waitFor(() => screen.getByText(/no audit records found/i));

    // Change the outcome filter
    const outcomeSelect = screen.getByRole("combobox", { name: /outcome/i });
    fireEvent.change(outcomeSelect, { target: { value: "pass" } });

    await waitFor(() => {
      expect(mockGetAuditEvents).toHaveBeenCalledTimes(2);
    });
  });
});
