/**
 * LoadingSpinner unit tests
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LoadingSpinner from "./LoadingSpinner";

describe("LoadingSpinner", () => {
  it("renders with default label", () => {
    render(<LoadingSpinner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders with a custom label", () => {
    render(<LoadingSpinner label="Fetching data…" />);
    expect(screen.getByRole("status", { name: "Fetching data…" })).toBeInTheDocument();
    expect(screen.getByText("Fetching data…")).toBeInTheDocument();
  });
});
