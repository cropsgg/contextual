"use client";

import axios from "axios";
import { AnimatePresence, motion } from "framer-motion";
import {
  Archive,
  ChevronDown,
  ChevronUp,
  Database,
  Loader2,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { API_URL } from "@/lib/api";

export type ContextStatus = {
  active_token_count: number;
  context_threshold: number;
  offloaded_message_count: number;
  memory_chunk_count: number;
  last_summary: string | null;
  last_compressed_at: string | null;
  latest_memory_episode_id: number | null;
  offloaded_summary_label: string | null;
  compression_in_progress: boolean;
  compression_attempted?: boolean;
  compression_succeeded?: boolean;
  failure_reason?: string | null;
  memory_paused?: boolean;
  retrieval_mode?: string | null;
  cross_session_memory_available?: boolean | null;
  retrieval_degraded?: boolean;
  retrieval_failure_reason?: string | null;
  last_fact_extraction_at?: string | null;
  fact_extraction_last_error?: string | null;
  fact_extraction_consecutive_failures?: number;
  embedding_cache_hit_rate?: number | null;
  retrieval_bundle_cache_hit?: boolean | null;
};

type OffloadedItem = {
  id: number;
  role: string;
  snippet: string;
  created_at: string;
  offloaded_at: string | null;
};

type Props = {
  sessionId: string | null;
  token: string | null;
  refreshSignal: number;
  onUnauthorized?: () => void;
};

export function ContextMonitor({
  sessionId,
  token,
  refreshSignal,
  onUnauthorized,
}: Props) {
  const [data, setData] = useState<ContextStatus | null>(null);
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archive, setArchive] = useState<OffloadedItem[]>([]);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPulse, setShowPulse] = useState(false);
  const prevCompressed = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId || !token) {
      setData(null);
      return;
    }
    setError(null);
    try {
      const { data: d } = await axios.get<ContextStatus>(
        `${API_URL}/api/history/context`,
        {
          params: { session_id: sessionId },
          headers: { Authorization: `Bearer ${token}` },
        },
      );
      setData(d);
      if (
        prevCompressed.current != null &&
        d.last_compressed_at != null &&
        d.last_compressed_at !== prevCompressed.current
      ) {
        setShowPulse(true);
      }
      prevCompressed.current = d.last_compressed_at;
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) {
        onUnauthorized?.();
      } else {
        setError("Could not load context status");
      }
    }
  }, [sessionId, token, onUnauthorized]);

  const loadArchive = useCallback(async () => {
    if (!sessionId || !token) return;
    setArchiveLoading(true);
    try {
      const { data: page } = await axios.get<{
        items: OffloadedItem[];
      }>(`${API_URL}/api/history/offloaded`, {
        params: { session_id: sessionId, page: 1, limit: 50 },
        headers: { Authorization: `Bearer ${token}` },
      });
      setArchive(page.items);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) {
        onUnauthorized?.();
      }
    } finally {
      setArchiveLoading(false);
    }
  }, [sessionId, token, onUnauthorized]);

  useEffect(() => {
    if (!showPulse) return;
    const id = window.setTimeout(() => setShowPulse(false), 3200);
    return () => window.clearTimeout(id);
  }, [showPulse]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshSignal]);

  useEffect(() => {
    if (!data?.compression_in_progress) return;
    const id = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(id);
  }, [data?.compression_in_progress, refresh]);

  useEffect(() => {
    if (archiveOpen && archive.length === 0 && !archiveLoading) {
      void loadArchive();
    }
  }, [archiveOpen, archive.length, archiveLoading, loadArchive]);

  if (!sessionId || !token) return null;

  const pct = data
    ? Math.min(
        100,
        Math.round(
          (data.active_token_count / Math.max(1, data.context_threshold)) * 100,
        ),
      )
    : 0;

  return (
    <motion.div
      layout
      className="mx-3 mb-3 space-y-2 rounded-xl border border-surface-border bg-surface/80 p-3"
    >
      <motion.div layout className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
          <Database className="h-3.5 w-3.5" />
          Context
        </div>
        {data?.compression_in_progress ? (
          <span className="inline-flex items-center gap-1 text-[10px] text-amber-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            Compressing…
          </span>
        ) : null}
      </motion.div>

      <AnimatePresence>
        {showPulse ? (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            className="flex items-center gap-2 rounded-lg bg-indigo-500/15 px-2 py-1.5 text-[11px] text-indigo-200"
          >
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-indigo-300" />
            {data?.offloaded_summary_label ??
              "Context compressed — older turns moved to memory."}
          </motion.div>
        ) : null}
      </AnimatePresence>

      {data?.memory_paused ? (
        <p className="rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200">
          Memory paused — compression failed. Fix API keys or retry after the
          issue is resolved.
        </p>
      ) : null}

      {(data?.fact_extraction_consecutive_failures ?? 0) > 0 &&
      data?.fact_extraction_last_error ? (
        <p
          className="rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200"
          title={data.fact_extraction_last_error}
        >
          Profile fact sync paused — {data.fact_extraction_last_error}
        </p>
      ) : null}

      {data?.retrieval_mode === "degraded_keyword" ? (
        <p
          className="rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200"
          title={data.retrieval_failure_reason ?? undefined}
        >
          Cross-session memory degraded — keyword fallback (semantic search
          unavailable).
        </p>
      ) : data?.cross_session_memory_available === false ? (
        <p
          className="rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200"
          title={data.retrieval_failure_reason ?? undefined}
        >
          Cross-session memory unavailable for this session.
        </p>
      ) : null}

      {error ? (
        <p className="text-[11px] text-red-400">{error}</p>
      ) : !data ? (
        <motion.div
          layout
          className="flex items-center gap-2 text-[11px] text-zinc-500"
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading…
        </motion.div>
      ) : (
        <>
          <motion.div layout>
            <motion.div
              layout
              className="mb-1 flex justify-between text-[10px] text-zinc-500"
            >
              <span>Active tokens</span>
              <span>
                {data.active_token_count.toLocaleString()} /{" "}
                {data.context_threshold.toLocaleString()}
              </span>
            </motion.div>
            <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
              <motion.div
                className={`h-full rounded-full ${
                  pct > 90
                    ? "bg-amber-500"
                    : pct > 70
                      ? "bg-indigo-400"
                      : "bg-emerald-500"
                }`}
                initial={false}
                animate={{ width: `${pct}%` }}
                transition={{ type: "spring", stiffness: 120, damping: 18 }}
              />
            </div>
          </motion.div>

          <motion.div
            layout
            className="grid grid-cols-2 gap-2 text-[11px] text-zinc-400"
          >
            <div>
              <p className="text-zinc-500">Offloaded msgs</p>
              <p className="font-mono text-zinc-200">
                {data.offloaded_message_count}
              </p>
            </div>
            <motion.div layout>
              <p className="text-zinc-500">Memory chunks</p>
              <p className="font-mono text-zinc-200">
                {data.memory_chunk_count}
              </p>
            </motion.div>
          </motion.div>

          {data.offloaded_summary_label ? (
            <p className="text-[10px] leading-snug text-zinc-500">
              {data.offloaded_summary_label}
            </p>
          ) : null}

          {data.last_summary ? (
            <motion.div layout>
              <button
                type="button"
                onClick={() => setSummaryExpanded((v) => !v)}
                className="flex w-full items-center justify-between gap-2 rounded-lg py-1 text-left text-[11px] text-zinc-400 hover:text-zinc-200"
              >
                <span>Latest compressed summary</span>
                {summaryExpanded ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>
              {summaryExpanded ? (
                <p className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap rounded-md bg-zinc-900/80 p-2 text-[11px] leading-snug text-zinc-300">
                  {data.last_summary}
                </p>
              ) : null}
            </motion.div>
          ) : (
            <p className="text-[10px] text-zinc-600">No compression yet.</p>
          )}

          {data.offloaded_message_count > 0 ? (
            <motion.div layout>
              <button
                type="button"
                onClick={() => setArchiveOpen((v) => !v)}
                className="flex w-full items-center justify-between gap-2 rounded-lg border border-zinc-800/80 px-2 py-1.5 text-left text-[11px] text-zinc-400 hover:border-zinc-700 hover:text-zinc-200"
              >
                <span className="inline-flex items-center gap-1.5">
                  <Archive className="h-3.5 w-3.5" />
                  Offloaded archive
                </span>
                {archiveOpen ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>
              {archiveOpen ? (
                <div className="mt-2 max-h-40 space-y-1.5 overflow-y-auto">
                  {archiveLoading ? (
                    <motion.div className="flex items-center gap-2 text-[10px] text-zinc-500">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Loading archive…
                    </motion.div>
                  ) : archive.length === 0 ? (
                    <p className="text-[10px] text-zinc-600">
                      No archived messages.
                    </p>
                  ) : (
                    archive.map((item) => (
                      <motion.div
                        key={item.id}
                        layout
                        className="rounded-md bg-zinc-900/60 px-2 py-1.5 text-[10px]"
                      >
                        <p className="mb-0.5 font-medium uppercase text-zinc-500">
                          {item.role}
                          {item.offloaded_at
                            ? ` · ${new Date(item.offloaded_at).toLocaleDateString()}`
                            : ""}
                        </p>
                        <p className="line-clamp-3 text-zinc-400">
                          {item.snippet}
                        </p>
                      </motion.div>
                    ))
                  )}
                </div>
              ) : null}
            </motion.div>
          ) : null}
        </>
      )}
    </motion.div>
  );
}
