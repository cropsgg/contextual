"use client";

import axios from "axios";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { API_URL } from "@/lib/api";

export type QuotaStatus = {
  token_unlimited: boolean;
  primary_limit: number;
  fallback_limit: number;
  primary_used_today: number;
  fallback_used_today: number;
  primary_remaining: number | null;
  fallback_remaining: number | null;
  primary_lifetime: number;
  fallback_lifetime: number;
  tier_in_use: string;
  primary_model: string;
  fallback_model: string;
  resets_at: string;
  usage_period_date: string | null;
};

export type User = {
  id: number;
  email: string;
  role: string;
  expert_preview_enabled: boolean;
  quota?: QuotaStatus | null;
};

export function userHasExpertPreview(user: User | null): boolean {
  if (!user) return false;
  if (user.role === "admin") return true;
  return user.expert_preview_enabled;
}

type AuthContextValue = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  setUserQuota: (quota: QuotaStatus) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "maestro_token";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async (t: string) => {
    const { data } = await axios.get<User>(`${API_URL}/api/auth/me`, {
      headers: { Authorization: `Bearer ${t}` },
    });
    setUser(data);
  }, []);

  useEffect(() => {
    const stored = typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
    if (!stored) {
      setLoading(false);
      return;
    }
    setToken(stored);
    refreshMe(stored)
      .catch(() => {
        localStorage.removeItem(TOKEN_KEY);
        setToken(null);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, [refreshMe]);

  const login = useCallback(async (email: string, password: string) => {
    const { data } = await axios.post<{ access_token: string }>(
      `${API_URL}/api/auth/login`,
      { email, password },
    );
    localStorage.setItem(TOKEN_KEY, data.access_token);
    setToken(data.access_token);
    await refreshMe(data.access_token);
  }, [refreshMe]);

  const register = useCallback(async (email: string, password: string) => {
    await axios.post(`${API_URL}/api/auth/register`, { email, password });
    await login(email, password);
  }, [login]);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem("maestro_session_id");
    setToken(null);
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    if (!token) return;
    await refreshMe(token);
  }, [token, refreshMe]);

  const setUserQuota = useCallback((quota: QuotaStatus) => {
    setUser((prev) => (prev ? { ...prev, quota } : prev));
  }, []);

  const value = useMemo(
    () => ({
      user,
      token,
      loading,
      login,
      register,
      logout,
      refreshUser,
      setUserQuota,
    }),
    [user, token, loading, login, register, logout, refreshUser, setUserQuota],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
