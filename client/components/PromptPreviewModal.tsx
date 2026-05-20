"use client";

import { Loader2, X } from "lucide-react";

export type PreviewMessage = { role: string; content: string };

export type ChatPreviewData = {
  messages: PreviewMessage[];
  token_count: number;
  model: string;
  enhanced: {
    facts: { fact_key: string; fact_value: string }[];
    memories: { episode_id: number; session_id: string; snippet: string }[];
  };
  would_compress?: boolean;
  projected_offload_count?: number;
};

type Props = {
  open: boolean;
  loading: boolean;
  error: string | null;
  preview: ChatPreviewData | null;
  onClose: () => void;
  onSend: () => void;
};

export function PromptPreviewModal({
  open,
  loading,
  error,
  preview,
  onClose,
  onSend,
}: Props) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border border-surface-border bg-surface-raised shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-surface-border px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">
              Expert mode — prompt preview
            </h3>
            {preview ? (
              <p className="text-[11px] text-zinc-500">
                ~{preview.token_count} tokens · {preview.model}
                {preview.would_compress
                  ? ` · would summarize ~${preview.projected_offload_count ?? 0} msgs`
                  : ""}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              Building prompt…
            </div>
          ) : null}
          {error ? <p className="text-sm text-red-400">{error}</p> : null}
          {preview && !loading ? (
            <div className="space-y-4">
              {preview.enhanced.facts.length > 0 ? (
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-zinc-500">Facts</p>
                  <ul className="space-y-1 text-[11px] text-zinc-400">
                    {preview.enhanced.facts.map((f) => (
                      <li key={f.fact_key}>
                        <span className="font-mono text-indigo-300">{f.fact_key}</span>:{" "}
                        {f.fact_value}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {preview.enhanced.memories.length > 0 ? (
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-zinc-500">
                    Retrieved memories
                  </p>
                  <ul className="space-y-1 text-[11px] text-zinc-400">
                    {preview.enhanced.memories.map((m) => (
                      <li key={m.episode_id}>
                        [{m.session_id.slice(0, 8)}…] {m.snippet}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div>
                <p className="mb-2 text-xs font-medium uppercase text-zinc-500">
                  Messages sent to model
                </p>
                <pre className="max-h-64 overflow-auto rounded-lg border border-surface-border bg-zinc-950 p-3 text-[11px] leading-relaxed text-zinc-300">
                  {JSON.stringify(preview.messages, null, 2)}
                </pre>
              </div>
            </div>
          ) : null}
        </div>

        <div className="flex gap-2 border-t border-surface-border p-4">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-lg border border-surface-border py-2 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={loading || Boolean(error) || !preview}
            onClick={onSend}
            className="flex-1 rounded-lg bg-accent py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
          >
            Send to DeepSeek
          </button>
        </div>
      </div>
    </div>
  );
}
