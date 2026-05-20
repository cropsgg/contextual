"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type RenameDialogProps = {
  open: boolean;
  title: string;
  initialValue: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
};

export function RenameDialog({
  open,
  title,
  initialValue,
  confirmLabel = "Save",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
}: RenameDialogProps) {
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setValue(initialValue);
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [open, initialValue]);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onCancel]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    onConfirm(trimmed);
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
      role="presentation"
      onClick={onCancel}
    >
      <form
        role="dialog"
        aria-labelledby="rename-title"
        className="w-full max-w-sm rounded-xl border border-surface-border bg-surface-raised p-5 shadow-menu"
        onClick={(e) => e.stopPropagation()}
        onSubmit={onSubmit}
      >
        <h2 id="rename-title" className="text-base font-medium text-ink">
          {title}
        </h2>
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="mt-3 w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-ink outline-none ring-accent/30 focus:ring-2"
        />
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-4 py-2 text-sm text-ink-muted transition hover:bg-surface-muted hover:text-ink"
          >
            {cancelLabel}
          </button>
          <button
            type="submit"
            disabled={!value.trim()}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent-hover disabled:opacity-50"
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
