"use client";

import axios from "axios";
import { AnimatePresence, motion } from "framer-motion";
import { Brain } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { API_URL } from "@/lib/api";

export type AttributionFact = {
  fact_key: string;
  fact_value: string;
  selection_reason?: string | null;
  pinned?: boolean;
};

export type AttributionMemory = {
  episode_id: number;
  session_id: string;
  snippet: string;
  score?: number | null;
  scope?: string;
};

export type AttributionRetrieval = {
  mode?: string;
  cross_session_memory_available?: boolean;
  reranked?: boolean;
  rerank_fallback?: boolean;
  keyword_fallback_used?: boolean;
  failure_reason?: string | null;
};

export type Attribution = {
  facts: AttributionFact[];
  memories: AttributionMemory[];
  retrieval?: AttributionRetrieval | null;
};

type Props = {
  sessionId: string | null;
  messageId: number | undefined;
  token: string | null;
  enabled: boolean;
  onUnauthorized?: () => void;
};

export function MessageAttribution({
  sessionId,
  messageId,
  token,
  enabled,
  onUnauthorized,
}: Props) {
  const [data, setData] = useState<Attribution | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token || !sessionId || !messageId || !enabled) {
      setData(null);
      return;
    }
    setLoading(true);
    try {
      const { data: attr } = await axios.get<Attribution>(
        `${API_URL}/api/history/attribution`,
        {
          params: { session_id: sessionId, message_id: messageId },
          headers: { Authorization: `Bearer ${token}` },
        },
      );
      const hasFacts = attr.facts.length > 0;
      const hasMemories = attr.memories.length > 0;
      const retrieval = attr.retrieval;
      const showDegradedOnly =
        !hasFacts &&
        !hasMemories &&
        retrieval &&
        retrieval.cross_session_memory_available === false;
      const hasContent = hasFacts || hasMemories || showDegradedOnly;
      setData(hasContent ? attr : null);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) {
        onUnauthorized?.();
      } else if (axios.isAxiosError(e) && e.response?.status === 404) {
        setData(null);
      } else {
        setData(null);
      }
    } finally {
      setLoading(false);
    }
  }, [token, sessionId, messageId, enabled, onUnauthorized]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!enabled || loading || !data) return null;

  const crossSession = data.memories.filter(
    (m) => (m.scope ?? "cross_session") === "cross_session",
  );
  const inSession = data.memories.filter((m) => m.scope === "in_session");
  const retrieval = data.retrieval;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="relative mt-1 flex justify-start"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-zinc-500 hover:bg-zinc-800/80 hover:text-zinc-400"
        aria-label="Sources used for this reply"
      >
        <Brain className="h-2.5 w-2.5 opacity-70" />
        Sources
      </button>
      <AnimatePresence>
        {open ? (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            className="absolute left-0 top-full z-20 mt-1 w-72 rounded-lg border border-surface-border bg-zinc-900 p-2 text-[11px] shadow-lg"
          >
            <p className="mb-2 text-[10px] text-zinc-500">
              Sources used for this reply
            </p>
            {data.facts.length > 0 ? (
              <motion.div className="mb-2">
                <p className="mb-1 font-medium text-zinc-400">Profile facts</p>
                <ul className="space-y-1 text-zinc-300">
                  {data.facts.map((f) => (
                    <li key={f.fact_key}>
                      <span className="font-mono text-zinc-400">
                        {f.fact_key}
                        {f.pinned ? " · pinned" : ""}
                      </span>
                      : {f.fact_value}
                    </li>
                  ))}
                </ul>
              </motion.div>
            ) : null}
            {crossSession.length > 0 ? (
              <motion.div className="mb-2">
                <p className="mb-1 font-medium text-zinc-400">Past sessions</p>
                <ul className="space-y-1 text-zinc-300">
                  {crossSession.map((m) => (
                    <li key={m.episode_id} className="leading-snug">
                      <span className="font-mono text-[10px] text-zinc-500">
                        {m.session_id.slice(0, 8)}…
                        {m.score != null
                          ? ` · ${Math.round(m.score * 100)}%`
                          : ""}
                      </span>
                      <p className="line-clamp-3">{m.snippet}</p>
                    </li>
                  ))}
                </ul>
              </motion.div>
            ) : null}
            {inSession.length > 0 ? (
              <motion.div className="mb-2">
                <p className="mb-1 font-medium text-zinc-400">
                  Earlier in this chat
                </p>
                <ul className="space-y-1 text-zinc-300">
                  {inSession.map((m) => (
                    <li key={m.episode_id} className="leading-snug">
                      <p className="line-clamp-3">{m.snippet}</p>
                    </li>
                  ))}
                </ul>
              </motion.div>
            ) : null}
            {retrieval &&
            (retrieval.mode === "degraded_keyword" ||
              retrieval.cross_session_memory_available === false ||
              (retrieval.mode &&
                retrieval.mode.startsWith("unavailable"))) ? (
              <p className="mt-1 border-t border-zinc-800 pt-2 text-[10px] text-amber-500/90">
                {retrieval.mode === "degraded_keyword"
                  ? "Cross-session search used keyword fallback (semantic search unavailable)."
                  : retrieval.cross_session_memory_available === false
                    ? "Cross-session memory unavailable."
                    : null}
                {retrieval.rerank_fallback
                  ? " Results were not reranked (vector order used)."
                  : null}
              </p>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.div>
  );
}
