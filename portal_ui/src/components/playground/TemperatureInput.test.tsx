/**
 * TemperatureInput unit tests
 * Requirements: 2.2
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import TemperatureInput from "./TemperatureInput";

describe("TemperatureInput", () => {
  it("renders with the provided value", () => {
    render(<TemperatureInput value={0.7} onChange={vi.fn()} />);
    const input = screen.getByRole("spinbutton", { name: /temperature/i }) as HTMLInputElement;
    expect(input.value).toBe("0.7");
  });

  it("calls onChange with parsed float when value changes", () => {
    const onChange = vi.fn();
    render(<TemperatureInput value={0.7} onChange={onChange} />);
    const input = screen.getByRole("spinbutton", { name: /temperature/i });
    fireEvent.change(input, { target: { value: "1.2" } });
    expect(onChange).toHaveBeenCalledWith(1.2);
  });

  it("shows error message when value is below 0.0", () => {
    render(<TemperatureInput value={-0.1} onChange={vi.fn()} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/must be between/i)).toBeInTheDocument();
  });

  it("shows error message when value is above 2.0", () => {
    render(<TemperatureInput value={2.5} onChange={vi.fn()} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("shows no error for boundary value 0.0", () => {
    render(<TemperatureInput value={0.0} onChange={vi.fn()} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows no error for boundary value 2.0", () => {
    render(<TemperatureInput value={2.0} onChange={vi.fn()} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("is disabled when disabled prop is true", () => {
    render(<TemperatureInput value={0.7} onChange={vi.fn()} disabled />);
    const input = screen.getByRole("spinbutton", { name: /temperature/i });
    expect(input).toBeDisabled();
  });
});
