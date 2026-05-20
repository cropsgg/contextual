"use client";

/** Reserved for a future Settings page; not mounted in the default chat shell. */

import axios from "axios";
import { motion } from "framer-motion";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { API_URL } from "@/lib/api";

export type UserFact = {
  id: number;
  fact_key: string;
  fact_value: string;
  confidence: number;
  status?: string;
  updated_at: string;
};

export type MemoryEpisode = {
  id: number;
  session_id: string;
  summary: string;
  created_at: string;
};

type MemoryEpisodeList = {
  items: MemoryEpisode[];
  total: number;
  page: number;
  limit: number;
};

type Props = {
  token: string | null;
  refreshSignal: number;
  onUnauthorized?: () => void;
  onFactsChanged?: () => void;
};

export function MemorySidebar({
  token,
  refreshSignal,
  onUnauthorized,
  onFactsChanged,
}: Props) {
  const [facts, setFacts] = useState<UserFact[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const [episodes, setEpisodes] = useState<MemoryEpisode[]>([]);
  const [episodesTotal, setEpisodesTotal] = useState(0);
  const [episodesLoading, setEpisodesLoading] = useState(false);
  const [clearing, setClearing] = useState(false);

  const refresh = useCallback(async () => {
    if (!token) {
      setFacts([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const { data } = await axios.get<UserFact[]>(`${API_URL}/api/memory/facts`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setFacts(data);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) {
        onUnauthorized?.();
      } else {
        setError("Could not load facts");
      }
    } finally {
      setLoading(false);
    }
  }, [token, onUnauthorized]);

  const refreshEpisodes = useCallback(async () => {
    if (!token) {
      setEpisodes([]);
      setEpisodesTotal(0);
      return;
    }
    setEpisodesLoading(true);
    try {
      const { data } = await axios.get<MemoryEpisodeList>(
        `${API_URL}/api/memory/episodes`,
        {
          headers: { Authorization: `Bearer ${token}` },
          params: { page: 1, limit: 20 },
        },
      );
      setEpisodes(data.items);
      setEpisodesTotal(data.total);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) {
        onUnauthorized?.();
      }
    } finally {
      setEpisodesLoading(false);
    }
  }, [token, onUnauthorized]);

  useEffect(() => {
    void refresh();
    void refreshEpisodes();
  }, [refresh, refreshEpisodes, refreshSignal]);

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    if (!token || !newKey.trim() || !newValue.trim()) return;
    try {
      await axios.post(
        `${API_URL}/api/memory/facts`,
        { fact_key: newKey.trim(), fact_value: newValue.trim() },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setNewKey("");
      setNewValue("");
      setAdding(false);
      void refresh();
      onFactsChanged?.();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        onUnauthorized?.();
      }
    }
  }

  async function onDelete(id: number) {
    if (!token) return;
    try {
      await axios.delete(`${API_URL}/api/memory/facts/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      void refresh();
      onFactsChanged?.();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        onUnauthorized?.();
      }
    }
  }

  async function onDeleteEpisode(id: number) {
    if (!token) return;
    if (!window.confirm("Delete this stored memory chunk?")) return;
    try {
      await axios.delete(`${API_URL}/api/memory/episodes/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      void refreshEpisodes();
      onFactsChanged?.();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        onUnauthorized?.();
      }
    }
  }

  async function onClearAll() {
    if (!token) return;
    if (
      !window.confirm(
        "Delete all stored facts and episodic memories? Chat messages are kept.",
      )
    ) {
      return;
    }
    setClearing(true);
    try {
      await axios.post(
        `${API_URL}/api/memory/clear`,
        { confirm: "DELETE_ALL_MEMORY" },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      void refresh();
      void refreshEpisodes();
      onFactsChanged?.();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        onUnauthorized?.();
      }
    } finally {
      setClearing(false);
    }
  }

  async function onSaveEdit(id: number) {
    if (!token || !editValue.trim()) return;
    try {
      await axios.patch(
        `${API_URL}/api/memory/facts/${id}`,
        { fact_value: editValue.trim() },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setEditingId(null);
      void refresh();
      onFactsChanged?.();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        onUnauthorized?.();
      }
    }
  }

  if (!token) return null;

  return (
    <motion.div
      layout
      className="mx-3 mb-3 space-y-2 rounded-xl border border-surface-border bg-surface/80 p-3"
    >
      <motion.div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          What I know about you
        </p>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
          title="Add fact"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </motion.div>

      {adding ? (
        <form onSubmit={onAdd} className="space-y-2 text-[11px]">
          <input
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="Key (e.g. favorite_language)"
            className="w-full rounded border border-surface-border bg-surface px-2 py-1 text-zinc-200 outline-none focus:ring-1 focus:ring-accent"
          />
          <input
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder="Value"
            className="w-full rounded border border-surface-border bg-surface px-2 py-1 text-zinc-200 outline-none focus:ring-1 focus:ring-accent"
          />
          <button
            type="submit"
            className="w-full rounded bg-accent/90 py-1 text-white hover:bg-accent"
          >
            Save
          </button>
        </form>
      ) : null}

      {error ? <p className="text-[11px] text-red-400">{error}</p> : null}

      {loading && facts.length === 0 ? (
        <div className="flex items-center gap-2 text-[11px] text-zinc-500">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading…
        </div>
      ) : facts.length === 0 ? (
        <p className="text-[10px] text-zinc-600">
          No facts yet. They appear after a few messages or when you add one.
        </p>
      ) : (
        <ul className="max-h-40 space-y-2 overflow-y-auto">
          {facts.map((f) => (
            <li
              key={f.id}
              className="rounded-lg border border-surface-border/80 bg-zinc-900/50 p-2"
            >
              <motion.div className="flex items-start justify-between gap-1">
                <span className="font-mono text-[10px] text-indigo-300">
                  {f.fact_key}
                  <span className="ml-1 text-zinc-500">
                    {(f.confidence * 100).toFixed(0)}%
                  </span>
                  {f.status && f.status !== "active" ? (
                    <span className="ml-1 text-amber-400/90">{f.status}</span>
                  ) : null}
                </span>
                <motion.div className="flex shrink-0 gap-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(f.id);
                      setEditValue(f.fact_value);
                    }}
                    className="rounded p-0.5 text-zinc-500 hover:text-zinc-200"
                    title="Edit"
                  >
                    <Pencil className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => void onDelete(f.id)}
                    className="rounded p-0.5 text-zinc-500 hover:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </motion.div>
              </motion.div>
              {editingId === f.id ? (
                <div className="mt-1 space-y-1">
                  <input
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    className="w-full rounded border border-surface-border bg-surface px-2 py-1 text-[11px] text-zinc-200"
                  />
                  <button
                    type="button"
                    onClick={() => void onSaveEdit(f.id)}
                    className="text-[10px] text-accent hover:underline"
                  >
                    Save
                  </button>
                </div>
              ) : (
                <p className="mt-0.5 text-[11px] leading-snug text-zinc-300">
                  {f.fact_value}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      <motion.div className="border-t border-surface-border pt-2">
        <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Stored memories ({episodesTotal})
        </p>
        {episodesLoading && episodes.length === 0 ? (
          <div className="mt-2 flex items-center gap-2 text-[11px] text-zinc-500">
            <Loader2 className="h-3 w-3 animate-spin" />
            Loading…
          </div>
        ) : episodes.length === 0 ? (
          <p className="mt-1 text-[10px] text-zinc-600">
            No compressed memory chunks yet.
          </p>
        ) : (
          <ul className="mt-2 max-h-32 space-y-2 overflow-y-auto">
            {episodes.map((ep) => (
              <li
                key={ep.id}
                className="rounded-lg border border-surface-border/80 bg-zinc-900/50 p-2"
              >
                <motion.div className="flex items-start justify-between gap-1">
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] text-zinc-500">
                      {new Date(ep.created_at).toLocaleString()}
                    </p>
                    <p className="mt-0.5 text-[11px] leading-snug text-zinc-300">
                      {ep.summary}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void onDeleteEpisode(ep.id)}
                    className="shrink-0 rounded p-0.5 text-zinc-500 hover:text-red-400"
                    title="Delete memory"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </motion.div>
              </li>
            ))}
          </ul>
        )}
      </motion.div>

      <button
        type="button"
        disabled={clearing}
        onClick={() => void onClearAll()}
        className="w-full rounded-lg border border-red-900/50 bg-red-950/30 py-1.5 text-[11px] font-medium text-red-300 hover:bg-red-950/50 disabled:opacity-50"
      >
        {clearing ? "Clearing…" : "Clear episodic memory"}
      </button>
    </motion.div>
  );
}
