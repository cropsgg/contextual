"use client";

import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AuthPanel } from "@/components/AuthPanel";
import { ExpertPreviewPanel } from "@/components/ExpertPreviewPanel";
import { MemorySidebar } from "@/components/MemorySidebar";
import { useAuth, userHasExpertPreview } from "@/lib/auth";
import {
  isDebugQueryEnabled,
  isDevToolsEnabledFromEnv,
} from "@/lib/devTools";

type Tab = "memory" | "expert";

export default function SettingsPage() {
  const { user, token, loading, logout } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<Tab>("memory");
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [debugFromUrl, setDebugFromUrl] = useState(false);

  const expertAllowed = userHasExpertPreview(user);
  const engineeringUiEnabled = isDevToolsEnabledFromEnv() || debugFromUrl;
  const canAccessSettings = engineeringUiEnabled || expertAllowed;

  useEffect(() => {
    setDebugFromUrl(isDebugQueryEnabled(window.location.search));
  }, []);

  useEffect(() => {
    if (!loading && user && token && !canAccessSettings) {
      router.replace("/");
    }
  }, [loading, user, token, canAccessSettings, router]);

  const initialTab = useMemo(() => {
    const t = searchParams.get("tab");
    if (t === "expert" && expertAllowed) return "expert" as Tab;
    if (engineeringUiEnabled) return "memory" as Tab;
    return expertAllowed ? ("expert" as Tab) : ("memory" as Tab);
  }, [searchParams, expertAllowed, engineeringUiEnabled]);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  if (loading) {
    return (
      <motion.div className="flex min-h-screen items-center justify-center text-zinc-400">
        Loading…
      </motion.div>
    );
  }

  if (!user || !token) {
    return <AuthPanel />;
  }

  if (!canAccessSettings) {
    return (
      <motion.div className="flex min-h-screen items-center justify-center text-zinc-400">
        Loading…
      </motion.div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-surface">
      <header className="flex items-center gap-3 border-b border-surface-border bg-surface-raised px-4 py-3">
        <Link
          href="/"
          className="inline-flex items-center gap-1 rounded-lg p-2 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
        >
          <ArrowLeft className="h-4 w-4" />
          Chat
        </Link>
        <h1 className="text-sm font-semibold text-zinc-100">Settings</h1>
      </header>

      <div className="flex flex-wrap gap-2 border-b border-surface-border px-4 py-2">
        {engineeringUiEnabled ? (
          <button
            type="button"
            onClick={() => setTab("memory")}
            className={`rounded-lg px-3 py-1.5 text-sm ${
              tab === "memory"
                ? "bg-zinc-800 text-white"
                : "text-zinc-400 hover:bg-zinc-800/60"
            }`}
          >
            Memory
          </button>
        ) : null}
        {expertAllowed ? (
          <button
            type="button"
            onClick={() => setTab("expert")}
            className={`rounded-lg px-3 py-1.5 text-sm ${
              tab === "expert"
                ? "bg-zinc-800 text-white"
                : "text-zinc-400 hover:bg-zinc-800/60"
            }`}
          >
            Expert preview
          </button>
        ) : null}
      </div>

      <main className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto p-4">
        {tab === "memory" && engineeringUiEnabled ? (
          <MemorySidebar
            token={token}
            refreshSignal={refreshSignal}
            onUnauthorized={logout}
            onFactsChanged={() => setRefreshSignal((n) => n + 1)}
          />
        ) : expertAllowed ? (
          <ExpertPreviewPanel
            token={token}
            onUnauthorized={logout}
            onSent={() => router.push("/")}
          />
        ) : null}
      </main>
    </div>
  );
}
