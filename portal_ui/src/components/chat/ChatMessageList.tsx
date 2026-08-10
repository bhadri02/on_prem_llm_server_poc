/**
 * ChatMessageList
 *
 * Scrollable chat-bubble message list — user messages right-aligned,
 * assistant messages left-aligned. Reuses the `.chat-message-bubble` /
 * `.chat-bubble-user` / `.chat-bubble-assistant` classes already defined in
 * index.css for the Playground's response bubble.
 */

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface ChatMessageListProps {
  messages: ChatMessage[];
}

export default function ChatMessageList({ messages }: ChatMessageListProps) {
  if (messages.length === 0) {
    return (
      <div style={{ textAlign: "center", color: "var(--text-light)", padding: "48px 24px" }}>
        No messages yet — say hello below.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {messages.map((m, i) => (
        <div
          key={i}
          style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}
        >
          <div
            className={`chat-message-bubble ${m.role === "user" ? "chat-bubble-user" : "chat-bubble-assistant"}`}
            style={{ maxWidth: "80%", whiteSpace: "pre-wrap" }}
          >
            {m.content}
          </div>
        </div>
      ))}
    </div>
  );
}
