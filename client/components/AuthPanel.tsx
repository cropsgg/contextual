"use client";

import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { FormEvent, useState } from "react";

import { useAuth } from "@/lib/auth";
import { formatApiError } from "@/lib/errors";

export function AuthPanel() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password);
    } catch (err: unknown) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-surface to-surface-raised p-6">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md rounded-2xl border border-surface-border bg-surface-raised p-8 shadow-menu"
      >
        <h1 className="text-2xl font-semibold tracking-tight text-ink">
          Assistant
        </h1>
        <p className="mt-2 text-sm text-ink-muted">Sign in to continue.</p>

        <div className="mt-6 flex gap-2 rounded-lg bg-surface-muted p-1">
          <button
            type="button"
            onClick={() => setMode("login")}
            className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
              mode === "login"
                ? "bg-surface-raised text-ink"
                : "text-ink-faint hover:text-ink-muted"
            }`}
          >
            Login
          </button>
          <button
            type="button"
            onClick={() => setMode("register")}
            className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
              mode === "register"
                ? "bg-surface-raised text-ink"
                : "text-ink-faint hover:text-ink-muted"
            }`}
          >
            Register
          </button>
        </div>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div>
            <label className="block text-xs font-medium text-ink-muted">
              Email
            </label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-ink outline-none ring-accent/30 focus:ring-2"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-muted">
              Password
            </label>
            <input
              type="password"
              required
              minLength={mode === "register" ? 8 : 1}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-ink outline-none ring-accent/30 focus:ring-2"
            />
            {mode === "register" ? (
              <p className="mt-1 text-xs text-ink-faint">
                At least 8 characters.
              </p>
            ) : null}
          </div>
          {error ? (
            <p className="text-sm text-red-400" role="alert">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2.5 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:opacity-60"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </motion.div>
    </div>
  );
}
