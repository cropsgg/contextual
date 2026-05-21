"use client";

import { motion } from "framer-motion";

import { MessageAttribution } from "@/components/MessageAttribution";

type ChatMessageProps = {
  role: "user" | "assistant";
  content: string;
  isStreaming?: boolean;
  messageId?: number;
  sessionId: string | null;
  token: string;
  onUnauthorized: () => void;
  /** When false, hide Sources / attribution (normal chat UX). */
  showAttribution?: boolean;
};

function TypingIndicator() {
  return (
    <span className="typing-dots" aria-label="Generating response">
      <span />
      <span />
      <span />
    </span>
  );
}

export function ChatMessage({
  role,
  content,
  isStreaming = false,
  messageId,
  sessionId,
  token,
  onUnauthorized,
  showAttribution: showAttributionUi = false,
}: ChatMessageProps) {
  const showAttribution =
    showAttributionUi &&
    role === "assistant" &&
    messageId &&
    !isStreaming &&
    Boolean(content);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex w-full min-w-0 ${role === "user" ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`w-fit max-w-[85%] shrink-0 rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          role === "user"
            ? "bg-accent text-white"
            : "bg-surface-raised text-ink"
        }`}
      >
        {role === "assistant" && !content && isStreaming ? (
          <TypingIndicator />
        ) : (
          <span className="whitespace-pre-wrap break-words">
            {content}
            {isStreaming && content ? (
              <span className="streaming-cursor" aria-hidden="true" />
            ) : null}
          </span>
        )}
        {showAttribution ? (
          <MessageAttribution
            sessionId={sessionId}
            messageId={messageId}
            token={token}
            enabled={Boolean(content)}
            onUnauthorized={onUnauthorized}
          />
        ) : null}
      </div>
    </motion.div>
  );
}
