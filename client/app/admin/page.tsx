"use client";

import axios from "axios";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  AdminUser,
  AdminUserPatch,
  PlatformStats,
  fetchAdminStats,
  fetchAdminUsers,
  patchAdminUser,
} from "@/lib/adminApi";
import { useAuth } from "@/lib/auth";
import { formatTokenCount } from "@/lib/quota";

export default function AdminPage() {
  const { user, token, loading } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [primaryLimit, setPrimaryLimit] = useState("");
  const [fallbackLimit, setFallbackLimit] = useState("");
  const [unlimited, setUnlimited] = useState(false);
  const [expertPreview, setExpertPreview] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const [u, s] = await Promise.all([
        fetchAdminUsers(token),
        fetchAdminStats(token),
      ]);
      setUsers(u);
      setStats(s);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 403) {
        router.replace("/");
        return;
      }
      setError("Failed to load admin data");
    }
  }, [token, router]);

  useEffect(() => {
    if (loading) return;
    if (!user || !token) {
      router.replace("/");
      return;
    }
    if (user.role !== "admin") {
      router.replace("/");
      return;
    }
    void load();
  }, [user, token, loading, load, router]);

  function openEdit(u: AdminUser) {
    setEditing(u);
    setPrimaryLimit(String(u.quota_primary_daily));
    setFallbackLimit(String(u.quota_fallback_daily));
    setUnlimited(u.role === "admin" || u.token_unlimited);
    setExpertPreview(u.expert_preview_enabled);
  }

  async function saveEdit(e: FormEvent) {
    e.preventDefault();
    if (!token || !editing) return;
    setSaving(true);
    setError(null);
    try {
      const primary = Number(primaryLimit);
      const fallback = Number(fallbackLimit);
      if (!Number.isFinite(primary) || !Number.isFinite(fallback)) {
        setError("Daily limits must be valid numbers");
        setSaving(false);
        return;
      }
      const patch: AdminUserPatch = {
        quota_primary_daily: primary,
        quota_fallback_daily: fallback,
        token_unlimited: editing.role === "admin" ? true : unlimited,
        expert_preview_enabled: expertPreview,
      };
      await patchAdminUser(token, editing.id, patch);
      setEditing(null);
      await load();
    } catch {
      setError("Failed to save user");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !user || user.role !== "admin") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface text-zinc-400">
        Loading…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-surface text-zinc-200">
      <header className="border-b border-surface-border px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Admin console</h1>
          <p className="text-sm text-zinc-500">Users and token usage</p>
        </div>
        <Link href="/" className="text-sm text-zinc-400 hover:text-zinc-200">
          Back to chat
        </Link>
      </header>

      {error ? <p className="px-6 py-3 text-sm text-red-400">{error}</p> : null}

      {stats ? (
        <section className="grid gap-4 px-6 py-6 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Users" value={String(stats.total_users)} />
          <StatCard
            label="Tokens today (all)"
            value={formatTokenCount(stats.tokens_total_today)}
          />
          <StatCard
            label="Lifetime primary"
            value={formatTokenCount(stats.tokens_primary_lifetime)}
          />
          <StatCard
            label="Lifetime fallback"
            value={formatTokenCount(stats.tokens_fallback_lifetime)}
          />
        </section>
      ) : null}

      <div className="overflow-x-auto px-6 pb-12">
        <table className="w-full min-w-[800px] text-left text-sm">
          <thead>
            <tr className="border-b border-surface-border text-zinc-500">
              <th className="py-2 pr-4">Email</th>
              <th className="py-2 pr-4">Role</th>
              <th className="py-2 pr-4">Primary today</th>
              <th className="py-2 pr-4">Fallback today</th>
              <th className="py-2 pr-4">Lifetime</th>
              <th className="py-2 pr-4">Limits</th>
              <th className="py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-surface-border/60">
                <td className="py-3 pr-4">{u.email}</td>
                <td className="py-3 pr-4">{u.role}</td>
                <td className="py-3 pr-4 font-mono text-xs">
                  {formatTokenCount(u.tokens_primary_today)} /{" "}
                  {formatTokenCount(u.quota_primary_daily)}
                </td>
                <td className="py-3 pr-4 font-mono text-xs">
                  {formatTokenCount(u.tokens_fallback_today)} /{" "}
                  {formatTokenCount(u.quota_fallback_daily)}
                </td>
                <td className="py-3 pr-4 font-mono text-xs">
                  {formatTokenCount(
                    u.tokens_primary_lifetime + u.tokens_fallback_lifetime,
                  )}
                </td>
                <td className="py-3 pr-4 text-xs text-zinc-500">
                  {u.token_unlimited ? "Unlimited" : "Daily caps"}
                </td>
                <td className="py-3">
                  <button
                    type="button"
                    onClick={() => openEdit(u)}
                    className="rounded-lg border border-surface-border px-2 py-1 text-xs hover:bg-zinc-800"
                  >
                    Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <form
            onSubmit={(e) => void saveEdit(e)}
            className="w-full max-w-md rounded-xl border border-surface-border bg-surface-raised p-6 shadow-xl"
          >
            <h2 className="text-base font-medium">Edit {editing.email}</h2>
            <label className="mt-4 block text-xs text-zinc-500">
              Primary daily limit (V4 Flash)
              <input
                type="number"
                min={0}
                value={primaryLimit}
                onChange={(e) => setPrimaryLimit(e.target.value)}
                className="mt-1 w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm"
              />
            </label>
            <label className="mt-3 block text-xs text-zinc-500">
              Fallback daily limit (V3.2)
              <input
                type="number"
                min={0}
                value={fallbackLimit}
                onChange={(e) => setFallbackLimit(e.target.value)}
                className="mt-1 w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm"
              />
            </label>
            <label className="mt-4 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={unlimited}
                disabled={editing.role === "admin"}
                onChange={(e) => setUnlimited(e.target.checked)}
              />
              {editing.role === "admin"
                ? "Unlimited tokens (admin role)"
                : "Unlimited tokens"}
            </label>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={expertPreview}
                onChange={(e) => setExpertPreview(e.target.checked)}
              />
              Expert preview enabled
            </label>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditing(null)}
                className="rounded-lg px-3 py-2 text-sm text-zinc-400 hover:bg-zinc-800"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-raised p-4">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
    </div>
  );
}
