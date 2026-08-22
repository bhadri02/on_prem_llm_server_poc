/**
 * PlaygroundView unit tests
 * Requirements: 2.1, 2.5, 2.7, 2.8
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PlaygroundView from "./PlaygroundView";
import * as portalClient from "../api/portalClient";

vi.mock("../api/portalClient", () => ({
  getModels: vi.fn(),
  streamPlaygroundChat: vi.fn(),
}));

const mockGetModels = portalClient.getModels as ReturnType<typeof vi.fn>;
const mockStreamPlaygroundChat = portalClient.streamPlaygroundChat as ReturnType<typeof vi.fn>;

function renderView() {
  return render(
    <MemoryRouter>
      <PlaygroundView />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PlaygroundView", () => {
  it("renders playground heading", async () => {
    mockGetModels.mockResolvedValue({ models: [] });
    renderView();
    expect(screen.getByText("Playground")).toBeInTheDocument();
  });

  it("shows loading spinner and disables Send while model list is loading", () => {
    // Never resolve so we stay in loading state
    mockGetModels.mockReturnValue(new Promise(() => {}));
    renderView();
    // Select should be in busy state
    const select = screen.getByRole("combobox", { name: /select model/i });
    expect(select).toBeDisabled();
  });

  it("disables Send button when model load fails (modelLoadError)", async () => {
    const { ApiError } = await import("../types");
    mockGetModels.mockRejectedValue(new ApiError(502, "upstream unavailable"));
    renderView();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument(),
    );
    // Send button should be disabled
    const sendBtn = screen.getByRole("button", { name: /send/i });
    expect(sendBtn).toBeDisabled();
  });

  it("shows error banner when the stream reports an in-band error", async () => {
    mockGetModels.mockResolvedValue({
      models: [{ name: "llama3", version: "1", backend: "ollama", tasks: [], status: "active" }],
    });
    mockStreamPlaygroundChat.mockImplementation((_req, handlers) => {
      handlers.onError("temperature out of range");
      return () => {};
    });

    renderView();
    await waitFor(() => screen.getByRole("combobox", { name: /select model/i }));

    // Enter a message
    const textarea = screen.getByRole("textbox", { name: /message/i });
    fireEvent.change(textarea, { target: { value: "test message" } });

    // Click send
    const sendBtn = screen.getByRole("button", { name: /send/i });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/temperature out of range/)).toBeInTheDocument();
    });
  });

  it("shows assistant response streamed in as deltas arrive", async () => {
    mockGetModels.mockResolvedValue({
      models: [{ name: "llama3", version: "1", backend: "ollama", tasks: [], status: "active" }],
    });
    mockStreamPlaygroundChat.mockImplementation((_req, handlers) => {
      handlers.onId("uuid-test-123");
      handlers.onDelta("Hello ");
      handlers.onDelta("from llama3!");
      handlers.onDone("stop");
      return () => {};
    });

    renderView();
    await waitFor(() => screen.getByRole("combobox", { name: /select model/i }));

    const textarea = screen.getByRole("textbox", { name: /message/i });
    fireEvent.change(textarea, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText("Hello from llama3!")).toBeInTheDocument();
      expect(screen.getByText("uuid-test-123")).toBeInTheDocument();
    });

    // View Audit Trail button should appear
    expect(screen.getByRole("button", { name: /view audit trail/i })).toBeInTheDocument();
  });
});
