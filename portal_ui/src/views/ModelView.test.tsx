/**
 * ModelView unit tests
 * Requirements: 6.3, 6.7, 6.8, 6.9, 6.10, 7.4
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ModelView from "./ModelView";
import * as portalClient from "../api/portalClient";
import type { ModelRecord } from "../types";

vi.mock("../api/portalClient", () => ({
  getModels: vi.fn(),
  patchModelStatus: vi.fn(),
}));

const mockGetModels = portalClient.getModels as ReturnType<typeof vi.fn>;
const mockPatchModelStatus = portalClient.patchModelStatus as ReturnType<typeof vi.fn>;

function makeModel(overrides: Partial<ModelRecord> = {}): ModelRecord {
  return {
    name: "llama3",
    version: "3.2",
    backend: "ollama",
    tasks: ["chat"],
    status: "active",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ModelView", () => {
  it("shows loading spinner while models are loading", () => {
    mockGetModels.mockReturnValue(new Promise(() => {}));
    render(<ModelView />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows empty-state when model list is empty", async () => {
    mockGetModels.mockResolvedValue({ models: [] });
    render(<ModelView />);
    await waitFor(() => {
      expect(screen.getByText(/no models are currently registered/i)).toBeInTheDocument();
    });
  });

  it("shows error banner when model fetch fails", async () => {
    const { ApiError } = await import("../types");
    mockGetModels.mockRejectedValue(new ApiError(502, "model-registry unavailable"));
    render(<ModelView />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/502/)).toBeInTheDocument();
    });
  });

  it("renders model table with correct data", async () => {
    mockGetModels.mockResolvedValue({ models: [makeModel()] });
    render(<ModelView />);
    await waitFor(() => {
      expect(screen.getByText("llama3")).toBeInTheDocument();
      expect(screen.getByText("Active")).toBeInTheDocument();
    });
  });

  it("re-fetches models after successful PATCH (Req 7.4)", async () => {
    // First call returns active model, second call returns retired
    mockGetModels
      .mockResolvedValueOnce({ models: [makeModel({ status: "active" })] })
      .mockResolvedValueOnce({ models: [makeModel({ status: "retired" })] });
    mockPatchModelStatus.mockResolvedValue(makeModel({ status: "retired" }));

    render(<ModelView />);
    await waitFor(() => screen.getByRole("button", { name: /retire/i }));

    fireEvent.click(screen.getByRole("button", { name: /retire/i }));

    await waitFor(() => {
      // After re-fetch, status badge should now show "Retired"
      expect(screen.getByText("Retired")).toBeInTheDocument();
    });

    expect(mockGetModels).toHaveBeenCalledTimes(2);
  });

  it("shows inline error on PATCH failure without changing the row", async () => {
    const { ApiError } = await import("../types");
    mockGetModels.mockResolvedValue({ models: [makeModel({ status: "active" })] });
    mockPatchModelStatus.mockRejectedValue(new ApiError(404, "model not found"));

    render(<ModelView />);
    await waitFor(() => screen.getByRole("button", { name: /retire/i }));

    fireEvent.click(screen.getByRole("button", { name: /retire/i }));

    await waitFor(() => {
      expect(screen.getByText(/model not found/i)).toBeInTheDocument();
    });

    // Status badge should still show Active (row unchanged)
    expect(screen.getByText("Active")).toBeInTheDocument();
  });
});
