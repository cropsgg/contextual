import type { QuotaStatus } from "@/lib/auth";

export function formatTokenCount(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return m >= 10 ? `${Math.round(m)}M` : `${m.toFixed(1)}M`;
  }
  if (n >= 1_000) {
    const k = n / 1_000;
    return k >= 100 ? `${Math.round(k)}K` : `${k.toFixed(1)}K`;
  }
  return String(n);
}

export function quotaBarLabel(quota: QuotaStatus | undefined): string | null {
  if (!quota) return null;
  if (quota.token_unlimited) return "Unlimited tokens (admin)";

  if (quota.tier_in_use === "fallback") {
    const rem = quota.fallback_remaining ?? 0;
    return `V3.2 tier · ${formatTokenCount(rem)} left today`;
  }
  if (quota.tier_in_use === "blocked") {
    return `Quota exhausted · resets ${formatResetsAt(quota.resets_at)}`;
  }
  const rem = quota.primary_remaining ?? 0;
  return `V4 Flash · ${formatTokenCount(rem)} left today`;
}

function formatResetsAt(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return "UTC midnight";
  }
}
