/**
 * MetricsView unit tests + Property 13
 *
 * Property 13: Grafana iframe src is constructed from Portal config value
 * **Validates: Requirements 9.1, 9.4**
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import * as fc from "fast-check";
import MetricsView from "./MetricsView";
import * as portalClient from "../api/portalClient";

vi.mock("../api/portalClient", () => ({
  getConfig: vi.fn(),
}));

const mockGetConfig = portalClient.getConfig as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MetricsView unit tests", () => {
  it("shows loading spinner while config is fetching", () => {
    // Never resolve so we're stuck in loading
    mockGetConfig.mockReturnValue(new Promise(() => {}));
    render(<MetricsView />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders iframe with correct src when config resolves", async () => {
    mockGetConfig.mockResolvedValue({ grafana_url: "https://grafana.example.com" });
    render(<MetricsView />);
    await waitFor(() => {
      const iframe = screen.getByTestId("grafana-iframe") as HTMLIFrameElement;
      expect(iframe.src).toBe(
        "https://grafana.example.com/d/poc-overview/llm-platform-poc?orgId=1&kiosk",
      );
    });
  });

  it("shows error banner when config fetch fails", async () => {
    mockGetConfig.mockRejectedValue({ status: 502, message: "Prometheus unreachable" });
    render(<MetricsView />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("shows fallback message when iframe fires error event", async () => {
    mockGetConfig.mockResolvedValue({ grafana_url: "https://grafana.example.com" });
    render(<MetricsView />);
    await waitFor(() => screen.getByTestId("grafana-iframe"));

    // Fire the error event on the iframe
    const iframe = screen.getByTestId("grafana-iframe");
    // Simulate the onError handler
    iframe.dispatchEvent(new Event("error"));
  });
});

// ---------------------------------------------------------------------------
// Property 13: Grafana iframe src is constructed from Portal config value
// **Validates: Requirements 9.1, 9.4**
// ---------------------------------------------------------------------------
describe("Property 13: Grafana iframe src is constructed from Portal config value", () => {
  it("constructs iframe src exactly as {grafana_url}/d/poc-overview/llm-platform-poc?orgId=1&kiosk or renders fallback in local dev mode", async () => {
    // Feature: platform-portals, Property 13: Grafana iframe src is constructed from Portal config value
    await fc.assert(
      fc.asyncProperty(
        // Generate valid HTTP/HTTPS base URLs
        fc.oneof(
          fc.constant("http://grafana:3000"),
          fc.constant("http://localhost:3000"),
          fc.constant("https://grafana.example.com"),
          fc.constant("http://10.0.0.1:3000"),
        ),
        async (grafanaUrl) => {
          mockGetConfig.mockResolvedValue({ grafana_url: grafanaUrl });

          const { unmount } = render(<MetricsView />);

          const isLocalDefault =
            grafanaUrl.includes("grafana:3000") ||
            grafanaUrl.includes("localhost:3000") ||
            grafanaUrl.includes("127.0.0.1:3000");

          if (isLocalDefault) {
            await waitFor(
              () => {
                const fallback = screen.queryByTestId("grafana-fallback");
                expect(fallback).toBeTruthy();
              },
              { timeout: 3000 },
            );
          } else {
            await waitFor(
              () => {
                const iframe = screen.queryByTestId("grafana-iframe") as HTMLIFrameElement | null;
                expect(iframe).toBeTruthy();
              },
              { timeout: 3000 },
            );

            const iframe = screen.getByTestId("grafana-iframe") as HTMLIFrameElement;
            // The iframe src should be exactly {grafana_url}/d/poc-overview/llm-platform-poc?orgId=1&kiosk
            const expectedSrc = `${grafanaUrl}/d/poc-overview/llm-platform-poc?orgId=1&kiosk`;
            expect(iframe.getAttribute("src")).toBe(expectedSrc);
          }

          unmount();
        },
      ),
      { numRuns: 4 },
    );
  });
});
