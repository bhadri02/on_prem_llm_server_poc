/**
 * ModelTable unit tests + Property 10
 *
 * Property 10: Model status action buttons follow lifecycle rules
 * **Validates: Requirements 6.5, 6.6**
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import * as fc from "fast-check";
import ModelTable from "./ModelTable";
import type { ModelRecord } from "../../types";

function makeModel(overrides: Partial<ModelRecord> = {}): ModelRecord {
  return {
    name: "test-model",
    version: "1.0.0",
    backend: "ollama",
    tasks: ["chat"],
    status: "active",
    ...overrides,
  };
}

describe("ModelTable unit tests", () => {
  it("renders empty-state when no models provided", () => {
    render(
      <ModelTable
        models={[]}
        loading={false}
        onAction={vi.fn()}
        actionError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByText("No models found.")).toBeInTheDocument();
  });

  it("renders model name, version, backend, tasks and status", () => {
    const model = makeModel({
      name: "llama3",
      version: "3.2",
      backend: "ollama",
      tasks: ["chat", "reasoning"],
      status: "active",
    });
    render(
      <ModelTable
        models={[model]}
        loading={false}
        onAction={vi.fn()}
        actionError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByText("llama3")).toBeInTheDocument();
    expect(screen.getByText("3.2")).toBeInTheDocument();
    expect(screen.getByText("ollama")).toBeInTheDocument();
    expect(screen.getByText("chat, reasoning")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("shows [Retire] only for active model", () => {
    render(
      <ModelTable
        models={[makeModel({ status: "active" })]}
        loading={false}
        onAction={vi.fn()}
        actionError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /retire/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /activate/i })).not.toBeInTheDocument();
  });

  it("shows [Activate] only for retired model", () => {
    render(
      <ModelTable
        models={[makeModel({ status: "retired" })]}
        loading={false}
        onAction={vi.fn()}
        actionError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /activate/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retire/i })).not.toBeInTheDocument();
  });

  it("shows both [Activate] and [Retire] for staging model", () => {
    render(
      <ModelTable
        models={[makeModel({ status: "staging" })]}
        loading={false}
        onAction={vi.fn()}
        actionError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /activate/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retire/i })).toBeInTheDocument();
  });

  it("disables action buttons when loading=true", () => {
    render(
      <ModelTable
        models={[makeModel({ status: "active" })]}
        loading={true}
        onAction={vi.fn()}
        actionError={null}
        onDismissError={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button", { name: /retire/i });
    expect(btn).toBeDisabled();
  });

  it("shows inline error for the affected row when actionError is set", () => {
    render(
      <ModelTable
        models={[makeModel({ name: "my-model", status: "active" })]}
        loading={false}
        onAction={vi.fn()}
        actionError={{ name: "my-model", message: "PATCH failed" }}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByText("PATCH failed")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Property 10: Model status action buttons follow lifecycle rules
// **Validates: Requirements 6.5, 6.6**
// ---------------------------------------------------------------------------
describe("Property 10: Model status action buttons follow lifecycle rules", () => {
  it("renders correct action buttons for any ModelRecord status", () => {
    // Feature: platform-portals, Property 10: Model status action buttons follow lifecycle rules
    fc.assert(
      fc.property(
        fc.record({
          name: fc.string({ minLength: 1, maxLength: 30 }),
          version: fc.string({ minLength: 1, maxLength: 20 }),
          backend: fc.string({ minLength: 1, maxLength: 20 }),
          tasks: fc.array(fc.string({ minLength: 1, maxLength: 20 }), {
            minLength: 0,
            maxLength: 5,
          }),
          status: fc.constantFrom("active" as const, "retired" as const, "staging" as const),
        }),
        (modelRecord) => {
          const { unmount } = render(
            <ModelTable
              models={[modelRecord]}
              loading={false}
              onAction={vi.fn()}
              actionError={null}
              onDismissError={vi.fn()}
            />,
          );

          const retireBtn = screen.queryByRole("button", { name: /retire/i });
          const activateBtn = screen.queryByRole("button", { name: /activate/i });

          if (modelRecord.status === "active") {
            // [Retire] shown, [Activate] not shown
            expect(retireBtn).toBeTruthy();
            expect(activateBtn).toBeFalsy();
          } else if (modelRecord.status === "retired") {
            // [Activate] shown, [Retire] not shown
            expect(activateBtn).toBeTruthy();
            expect(retireBtn).toBeFalsy();
          } else if (modelRecord.status === "staging") {
            // Both shown
            expect(retireBtn).toBeTruthy();
            expect(activateBtn).toBeTruthy();
          }

          unmount();
        },
      ),
      { numRuns: 50 },
    );
  });
});
