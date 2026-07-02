/**
 * StatusBadge unit tests
 * Tests that correct colour classes / labels are rendered for each status.
 * Requirements: 6.3
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
  it('renders "Active" label for active status', () => {
    const { container } = render(<StatusBadge status="active" />);
    expect(screen.getByText("Active")).toBeInTheDocument();
    // green background
    const span = container.querySelector("span");
    expect(span?.style.background).toBe("rgb(220, 252, 231)");
  });

  it('renders "Staging" label for staging status', () => {
    const { container } = render(<StatusBadge status="staging" />);
    expect(screen.getByText("Staging")).toBeInTheDocument();
    // yellow background
    const span = container.querySelector("span");
    expect(span?.style.background).toBe("rgb(254, 249, 195)");
  });

  it('renders "Retired" label for retired status', () => {
    const { container } = render(<StatusBadge status="retired" />);
    expect(screen.getByText("Retired")).toBeInTheDocument();
    // grey background
    const span = container.querySelector("span");
    expect(span?.style.background).toBe("rgb(241, 245, 249)");
  });
});
