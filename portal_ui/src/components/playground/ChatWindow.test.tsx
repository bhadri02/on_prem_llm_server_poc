/**
 * ChatWindow unit tests
 * Requirements: 2.3, 2.5, 2.6, 2.7
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ChatWindow from "./ChatWindow";

const noop = vi.fn();

describe("ChatWindow", () => {
  it("renders the message textarea and Send button", () => {
    render(
      <ChatWindow
        disabled={false}
        isLoading={false}
        response={null}
        onSend={noop}
        onViewAudit={noop}
      />,
    );
    expect(screen.getByRole("textbox", { name: /message/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
  });

  it("Send button is disabled when no text is entered", () => {
    render(
      <ChatWindow
        disabled={false}
        isLoading={false}
        response={null}
        onSend={noop}
        onViewAudit={noop}
      />,
    );
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("Send button is disabled while isLoading=true", () => {
    render(
      <ChatWindow
        disabled={false}
        isLoading={true}
        response={null}
        onSend={noop}
        onViewAudit={noop}
      />,
    );
    const btn = screen.getByRole("button", { name: /sending/i });
    expect(btn).toBeDisabled();
  });

  it("Send button is disabled when disabled=true (model load error)", () => {
    render(
      <ChatWindow
        disabled={true}
        isLoading={false}
        response={null}
        onSend={noop}
        onViewAudit={noop}
      />,
    );
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("calls onSend with the trimmed message when Send is clicked", () => {
    const onSend = vi.fn();
    render(
      <ChatWindow
        disabled={false}
        isLoading={false}
        response={null}
        onSend={onSend}
        onViewAudit={noop}
      />,
    );
    const textarea = screen.getByRole("textbox", { name: /message/i });
    fireEvent.change(textarea, { target: { value: "  hello world  " } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onSend).toHaveBeenCalledWith("hello world");
  });

  it("displays assistant response content and request_id when response is provided", () => {
    render(
      <ChatWindow
        disabled={false}
        isLoading={false}
        response={{ content: "Here is the answer.", requestId: "abc-123" }}
        onSend={noop}
        onViewAudit={noop}
      />,
    );
    expect(screen.getByText("Here is the answer.")).toBeInTheDocument();
    expect(screen.getByText("abc-123")).toBeInTheDocument();
  });

  it('shows "View Audit Trail" button after successful response', () => {
    render(
      <ChatWindow
        disabled={false}
        isLoading={false}
        response={{ content: "Answer", requestId: "req-uuid" }}
        onSend={noop}
        onViewAudit={noop}
      />,
    );
    expect(
      screen.getByRole("button", { name: /view audit trail/i }),
    ).toBeInTheDocument();
  });

  it('calls onViewAudit with the request_id when "View Audit Trail" is clicked', () => {
    const onViewAudit = vi.fn();
    render(
      <ChatWindow
        disabled={false}
        isLoading={false}
        response={{ content: "Answer", requestId: "test-request-id" }}
        onSend={noop}
        onViewAudit={onViewAudit}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /view audit trail/i }));
    expect(onViewAudit).toHaveBeenCalledWith("test-request-id");
  });
});
