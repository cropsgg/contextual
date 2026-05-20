import { API_URL } from "@/lib/api";
import type { QuotaStatus } from "@/lib/auth";

export type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "compression_started"; session_id: string }
  | {
      type: "done";
      assistant_message_id: number;
      session_id: string;
      model?: string;
      tier?: string;
      quota?: QuotaStatus;
    }
  | { type: "error"; message: string; code: string };

function parseSseBlock(block: string): ChatStreamEvent | null {
  let event = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try {
    const payload = JSON.parse(data) as Record<string, unknown>;
    if (event === "token" && typeof payload.text === "string") {
      return { type: "token", text: payload.text };
    }
    if (event === "compression_started" && typeof payload.session_id === "string") {
      return { type: "compression_started", session_id: payload.session_id };
    }
    if (
      event === "done" &&
      typeof payload.assistant_message_id === "number" &&
      typeof payload.session_id === "string"
    ) {
      const quota =
        payload.quota && typeof payload.quota === "object"
          ? (payload.quota as QuotaStatus)
          : undefined;
      return {
        type: "done",
        assistant_message_id: payload.assistant_message_id,
        session_id: payload.session_id,
        model: typeof payload.model === "string" ? payload.model : undefined,
        tier: typeof payload.tier === "string" ? payload.tier : undefined,
        quota,
      };
    }
    if (event === "error") {
      return {
        type: "error",
        message: String(payload.message ?? "Unknown error"),
        code: String(payload.code ?? "error"),
      };
    }
  } catch {
    return null;
  }
  return null;
}

export async function streamChatMessage(
  token: string,
  sessionId: string,
  message: string,
  signal: AbortSignal,
  onEvent: (ev: ChatStreamEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });

  if (!res.ok || !res.body) {
    const errText = await res.text();
    let message = errText || `HTTP ${res.status}`;
    try {
      const parsed = JSON.parse(errText) as { detail?: unknown };
      if (res.status === 429) {
        message = "Daily token quota exhausted. Limits reset at UTC midnight.";
      }
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      } else if (parsed.detail && typeof parsed.detail === "object") {
        const d = parsed.detail as {
          message?: string;
          error?: string;
          resets_at?: string;
        };
        if (d.message) {
          message = d.message;
        } else if (d.error === "quota_exceeded") {
          message =
            "Daily token quota exhausted. Your limits reset at UTC midnight.";
          if (d.resets_at) {
            message += ` Next reset: ${d.resets_at}.`;
          }
        } else if (d.error === "quota_insufficient") {
          message =
            d.message ??
            "This message is too large for your remaining daily token quota.";
        }
      }
    } catch {
      /* keep raw text */
    }
    throw new Error(message);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const ev = parseSseBlock(part.trim());
      if (ev) onEvent(ev);
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) {
    const ev = parseSseBlock(buffer.trim());
    if (ev) onEvent(ev);
  }
}
