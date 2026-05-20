import axios from "axios";

import { API_URL } from "@/lib/api";

export type AdminUser = {
  id: number;
  email: string;
  role: string;
  created_at: string;
  expert_preview_enabled: boolean;
  token_unlimited: boolean;
  quota_primary_daily: number;
  quota_fallback_daily: number;
  tokens_primary_today: number;
  tokens_fallback_today: number;
  tokens_primary_lifetime: number;
  tokens_fallback_lifetime: number;
  usage_period_date: string | null;
};

export type PlatformStats = {
  total_users: number;
  tokens_primary_today: number;
  tokens_fallback_today: number;
  tokens_primary_lifetime: number;
  tokens_fallback_lifetime: number;
  tokens_total_today: number;
  tokens_total_lifetime: number;
};

export type AdminUserPatch = {
  quota_primary_daily?: number;
  quota_fallback_daily?: number;
  token_unlimited?: boolean;
  expert_preview_enabled?: boolean;
};

function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` };
}

export async function fetchAdminUsers(token: string): Promise<AdminUser[]> {
  const { data } = await axios.get<AdminUser[]>(`${API_URL}/api/admin/users`, {
    headers: authHeaders(token),
  });
  return data;
}

export async function fetchAdminStats(token: string): Promise<PlatformStats> {
  const { data } = await axios.get<PlatformStats>(`${API_URL}/api/admin/stats`, {
    headers: authHeaders(token),
  });
  return data;
}

export async function patchAdminUser(
  token: string,
  userId: number,
  patch: AdminUserPatch,
): Promise<AdminUser> {
  const { data } = await axios.patch<AdminUser>(
    `${API_URL}/api/admin/users/${userId}`,
    patch,
    { headers: authHeaders(token) },
  );
  return data;
}
