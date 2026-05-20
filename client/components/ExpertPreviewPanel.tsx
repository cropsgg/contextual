"use client";

import axios from "axios";
import { Loader2 } from "lucide-react";
import { FormEvent, useState } from "react";

import {
  ChatPreviewData,
  PromptPreviewModal,
} from "@/components/PromptPreviewModal";
import { API_URL } from "@/lib/api";
import { formatApiError } from "@/lib/errors";

const SESSION_KEY = "maestro_session_id";

type Props = {
  token: string;
  sessionId?: string | null;
  onUnauthorized?: () => void;
  onSent?: () => void;
};

export function ExpertPreviewPanel({
  token,
  sessionId: sessionIdProp,
  onUnauthorized,
  onSent,
}: Props) {
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ChatPreviewData | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [pendingSend, setPendingSend] = useState<string | null>(null);

  function resolveSessionId(): string {
    if (sessionIdProp) return sessionIdProp;
    if (typeof window === "undefined") return "";
    let id = localStorage.getItem(SESSION_KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(SESSION_KEY, id);
    }
    return id;
  }

  async function runPreview(text: string) {
    setLoading(true);
    setError(null);
    setPreview(null);
    setModalOpen(true);
    try {
      const { data } = await axios.post<ChatPreviewData>(
        `${API_URL}/api/chat/preview`,
        { message: text, session_id: resolveSessionId() },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setPreview(data);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) {
        onUnauthorized?.();
      } else {
        setError(formatApiError(e));
      }
    } finally {
      setLoading(false);
    }
  }

  async function onPreviewSubmit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setPendingSend(text);
    await runPreview(text);
  }

  function onSendAnyway() {
    const text = pendingSend ?? draft.trim();
    if (!text) return;
    setModalOpen(false);
    if (typeof window !== "undefined") {
      sessionStorage.setItem("maestro_pending_send", text);
    }
    onSent?.();
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Preview the assembled prompt before sending. Preview does not save messages or
        change memory.
      </p>
      <form onSubmit={onPreviewSubmit} className="space-y-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={4}
          placeholder="Message to preview…"
          className="w-full rounded-xl border border-surface-border bg-surface px-3 py-2 text-sm outline-none ring-accent/30 focus:ring-2"
        />
        <button
          type="submit"
          disabled={loading || !draft.trim()}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Preview prompt
        </button>
      </form>

      <PromptPreviewModal
        open={modalOpen}
        loading={loading}
        error={error}
        preview={preview}
        onClose={() => setModalOpen(false)}
        onSend={onSendAnyway}
      />
    </div>
  );
}
