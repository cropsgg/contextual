"use client";

import axios from "axios";
import { motion } from "framer-motion";
import {
  Loader2,
  MessageSquarePlus,
  MoreVertical,
  PanelLeftClose,
  PanelLeft,
  Send,
  Square,
} from "lucide-react";
import Link from "next/link";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AuthPanel } from "@/components/AuthPanel";
import { ChatMessage } from "@/components/ChatMessage";
import { ContextMonitor } from "@/components/ContextMonitor";
import {
  ChatPreviewData,
  PromptPreviewModal,
} from "@/components/PromptPreviewModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  DropdownMenu,
  DropdownMenuItem,
} from "@/components/ui/DropdownMenu";
import { RenameDialog } from "@/components/ui/RenameDialog";
import { API_URL } from "@/lib/api";
import { useAuth, userHasExpertPreview } from "@/lib/auth";
import { streamChatMessage } from "@/lib/chatStream";
import { quotaBarLabel } from "@/lib/quota";
import {
  isDebugQueryEnabled,
  isDevToolsEnabledFromEnv,
} from "@/lib/devTools";
import { formatApiError } from "@/lib/errors";

type SessionSummary = {
  session_id: string;
  last_message_at: string;
  title: string;
  preview_text: string | null;
};
type ChatMessageData = {
  id?: number;
  role: "user" | "assistant";
  content: string;
};

const SESSION_KEY = "maestro_session_id";
const PENDING_SEND_KEY = "maestro_pending_send";

export function ChatShell() {
  const { user, token, loading, logout, setUserQuota } = useAuth();
  const quotaBlocked = user?.quota?.tier_in_use === "blocked";
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);
  const [contextRefreshSignal, setContextRefreshSignal] = useState(0);
  const [debugFromUrl, setDebugFromUrl] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<ChatPreviewData | null>(null);
  const [pendingSend, setPendingSend] = useState<string | null>(null);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null);
  const [renameTarget, setRenameTarget] = useState<SessionSummary | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const engineeringUiEnabled = isDevToolsEnabledFromEnv() || debugFromUrl;
  const expertEnabled = userHasExpertPreview(user);
  const settingsVisible = engineeringUiEnabled || expertEnabled;

  useEffect(() => {
    setDebugFromUrl(isDebugQueryEnabled(window.location.search));
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  }, []);

  const authHeader = useMemo(
    () => (token ? { Authorization: `Bearer ${token}` } : {}),
    [token],
  );

  const refreshSessions = useCallback(async () => {
    if (!token) return;
    try {
      const { data } = await axios.get<SessionSummary[]>(
        `${API_URL}/api/history/sessions`,
        { headers: authHeader },
      );
      setSessions(data);
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 401) logout();
    }
  }, [token, authHeader, logout]);

  const loadMessages = useCallback(
    async (sid: string) => {
      if (!token) return;
      try {
        const { data } = await axios.get<
          { id: number; role: string; content: string }[]
        >(`${API_URL}/api/history/messages`, {
          params: { session_id: sid },
          headers: authHeader,
        });
        setMessages(
          data.map((m) => ({
            id: m.id,
            role: m.role === "assistant" ? "assistant" : "user",
            content: m.content,
          })),
        );
      } catch (e) {
        if (axios.isAxiosError(e) && e.response?.status === 401) logout();
      }
    },
    [token, authHeader, logout],
  );

  const createSessionOnServer = useCallback(async (): Promise<string> => {
    const { data } = await axios.post<{ session_id: string }>(
      `${API_URL}/api/history/sessions`,
      {},
      { headers: authHeader },
    );
    return data.session_id;
  }, [authHeader]);

  useEffect(() => {
    if (!user || !token) return;
    void (async () => {
      let sid = localStorage.getItem(SESSION_KEY);
      if (!sid) {
        try {
          sid = await createSessionOnServer();
          localStorage.setItem(SESSION_KEY, sid);
        } catch {
          sid = crypto.randomUUID();
          localStorage.setItem(SESSION_KEY, sid);
        }
      }
      setSessionId(sid);
      await refreshSessions();
      await loadMessages(sid);
      const pending = sessionStorage.getItem(PENDING_SEND_KEY);
      if (pending) {
        sessionStorage.removeItem(PENDING_SEND_KEY);
        setInput(pending);
        setPendingSend(pending);
      }
    })();
  }, [user, token, refreshSessions, loadMessages, createSessionOnServer]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: sending ? "auto" : "smooth",
    });
  }, [messages, sending]);

  useEffect(() => {
    if (!sessionNotice) return;
    const t = window.setTimeout(() => setSessionNotice(null), 4000);
    return () => window.clearTimeout(t);
  }, [sessionNotice]);

  async function startNewChat() {
    try {
      const sid = await createSessionOnServer();
      localStorage.setItem(SESSION_KEY, sid);
      setSessionId(sid);
      setMessages([]);
      setStreamError(null);
      await refreshSessions();
      setContextRefreshSignal((n) => n + 1);
    } catch {
      const sid = crypto.randomUUID();
      localStorage.setItem(SESSION_KEY, sid);
      setSessionId(sid);
      setMessages([]);
    }
  }

  function selectSession(sid: string) {
    localStorage.setItem(SESSION_KEY, sid);
    setSessionId(sid);
    setStreamError(null);
    void loadMessages(sid);
    setContextRefreshSignal((n) => n + 1);
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  }

  async function deleteSessionConfirmed(sid: string) {
    if (!token) return;
    try {
      await axios.delete(`${API_URL}/api/history/sessions/${sid}`, {
        headers: authHeader,
      });
      await refreshSessions();
      if (sessionId === sid) {
        await startNewChat();
      }
      setSessionNotice("Conversation deleted.");
    } catch (e) {
      setStreamError(formatApiError(e));
    }
  }

  async function renameSessionConfirmed(sid: string, title: string) {
    try {
      await axios.patch(
        `${API_URL}/api/history/sessions/${sid}`,
        { title },
        { headers: authHeader },
      );
      await refreshSessions();
    } catch (e) {
      setStreamError(formatApiError(e));
    }
  }

  function discardInFlightAssistant() {
    setMessages((prev) => {
      if (prev[prev.length - 1]?.role === "assistant") {
        return prev.slice(0, -1);
      }
      return prev;
    });
  }

  function stopGenerating() {
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    discardInFlightAssistant();
  }

  const streamChat = useCallback(
    async (text: string) => {
      if (!token || !sessionId) return;
      setStreamError(null);
      const userMsg: ChatMessageData = { role: "user", content: text };
      const asstMsg: ChatMessageData = { role: "assistant", content: "" };
      setMessages((prev) => [...prev, userMsg, asstMsg]);
      setSending(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamChatMessage(
          token,
          sessionId,
          text,
          controller.signal,
          (ev) => {
            if (ev.type === "token") {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next[next.length - 1] = {
                    ...last,
                    content: last.content + ev.text,
                  };
                }
                return next;
              });
            }
            if (ev.type === "done") {
              if (ev.quota) setUserQuota(ev.quota);
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next[next.length - 1] = {
                    ...last,
                    id: ev.assistant_message_id,
                  };
                }
                return next;
              });
            }
            if (ev.type === "error") {
              setStreamError(ev.message);
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next.pop();
                }
                return next;
              });
            }
          },
        );
        void refreshSessions();
        setContextRefreshSignal((n) => n + 1);
      } catch (err) {
        if (controller.signal.aborted) {
          discardInFlightAssistant();
          return;
        }
        const msg = formatApiError(err);
        setStreamError(msg);
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next.pop();
          }
          return next;
        });
      } finally {
        setSending(false);
        abortRef.current = null;
      }
    },
    [token, sessionId, refreshSessions, setUserQuota],
  );

  async function runPreview(text: string) {
    if (!token || !sessionId) return;
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewData(null);
    setPendingSend(text);
    try {
      const { data } = await axios.post<ChatPreviewData>(
        `${API_URL}/api/chat/preview`,
        { message: text, session_id: sessionId },
        { headers: authHeader },
      );
      setPreviewData(data);
    } catch (e) {
      setPreviewError(formatApiError(e));
    } finally {
      setPreviewLoading(false);
    }
  }

  async function onSend(e: FormEvent) {
    e.preventDefault();
    if (!token || !sessionId || !input.trim() || sending || quotaBlocked) return;
    const text = input.trim();
    setInput("");
    await streamChat(text);
  }

  useEffect(() => {
    if (pendingSend && sessionId && token && !sending) {
      const t = pendingSend;
      setPendingSend(null);
      void streamChat(t);
    }
  }, [pendingSend, sessionId, token, sending, streamChat]);

  if (loading) {
    return (
      <motion.div className="flex min-h-screen items-center justify-center gap-2 text-ink-muted">
        <Loader2 className="h-6 w-6 animate-spin" />
        Loading…
      </motion.div>
    );
  }

  if (!user || !token) {
    return <AuthPanel />;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <motion.aside
        initial={false}
        animate={{ width: sidebarOpen ? 280 : 0, opacity: sidebarOpen ? 1 : 0 }}
        className="flex shrink-0 flex-col overflow-hidden border-r border-surface-border bg-surface-raised max-md:absolute max-md:z-20 max-md:h-full max-md:shadow-xl"
      >
        <div className="flex h-full w-[280px] flex-col">
          <div className="flex items-center justify-between gap-2 border-b border-surface-border p-3">
            <span className="min-w-0 truncate text-sm font-medium text-ink">
              {user.email}
            </span>
            <DropdownMenu
              align="end"
              trigger={
                <button
                  type="button"
                  title="Account menu"
                  className="rounded-lg p-2 text-ink-faint transition hover:bg-surface-muted hover:text-ink"
                >
                  <MoreVertical className="h-4 w-4" />
                </button>
              }
            >
              {settingsVisible ? (
                <DropdownMenuItem href="/settings">Settings</DropdownMenuItem>
              ) : null}
              {user.role === "admin" ? (
                <DropdownMenuItem href="/admin">Admin</DropdownMenuItem>
              ) : null}
              <DropdownMenuItem
                variant="danger"
                onClick={() => setLogoutConfirmOpen(true)}
              >
                Log out
              </DropdownMenuItem>
            </DropdownMenu>
          </div>

          <button
            type="button"
            onClick={() => void startNewChat()}
            className="mx-3 mt-3 flex items-center justify-center gap-2 rounded-lg bg-surface-muted py-2.5 text-sm text-ink-muted transition hover:text-ink"
          >
            <MessageSquarePlus className="h-4 w-4" />
            New chat
          </button>

          {sessionNotice ? (
            <p className="mx-3 mt-2 text-xs text-ink-muted">{sessionNotice}</p>
          ) : null}

          {engineeringUiEnabled ? (
            <ContextMonitor
              sessionId={sessionId}
              token={token}
              refreshSignal={contextRefreshSignal}
              onUnauthorized={logout}
            />
          ) : null}

          <nav className="flex-1 overflow-y-auto px-2 pb-4 pt-4">
            <p className="px-2 pb-2 text-xs text-ink-faint">Recent chats</p>
            <ul className="space-y-0.5">
              {sessions.map((s) => {
                const isActive = s.session_id === sessionId;
                return (
                  <li key={s.session_id} className="group relative">
                    <div
                      className={`flex items-center rounded-lg transition ${
                        isActive
                          ? "bg-surface-muted"
                          : "hover:bg-surface-muted/60"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => selectSession(s.session_id)}
                        className={`min-w-0 flex-1 border-l-2 py-2 pl-3 pr-1 text-left text-sm ${
                          isActive
                            ? "border-accent text-ink"
                            : "border-transparent text-ink-muted hover:text-ink"
                        }`}
                      >
                        <span className="block truncate text-xs font-medium">
                          {s.title || "New conversation"}
                        </span>
                        <span className="block truncate text-[10px] text-ink-faint">
                          {s.preview_text ||
                            new Date(s.last_message_at).toLocaleString()}
                        </span>
                      </button>
                      <DropdownMenu
                        align="end"
                        className={`shrink-0 pr-1 ${
                          isActive
                            ? "opacity-100"
                            : "opacity-0 group-hover:opacity-100 focus-within:opacity-100 max-md:opacity-100"
                        }`}
                        trigger={
                          <button
                            type="button"
                            title="Conversation options"
                            className="rounded-md p-1.5 text-ink-faint transition hover:bg-surface hover:text-ink"
                          >
                            <MoreVertical className="h-3.5 w-3.5" />
                          </button>
                        }
                      >
                        <DropdownMenuItem
                          onClick={() => setRenameTarget(s)}
                        >
                          Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="danger"
                          onClick={() => setDeleteTarget(s)}
                        >
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenu>
                    </div>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>
      </motion.aside>

      <div className="flex min-w-0 flex-1 flex-col bg-surface">
        <header className="flex items-center gap-2 border-b border-surface-border px-3 py-2.5">
          <button
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            className="rounded-lg p-2 text-ink-faint transition hover:bg-surface-muted hover:text-ink"
            aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
          >
            {sidebarOpen ? (
              <PanelLeftClose className="h-5 w-5" />
            ) : (
              <PanelLeft className="h-5 w-5" />
            )}
          </button>
          <h2 className="text-sm font-medium text-ink-muted">Chat</h2>
          {quotaBarLabel(user.quota ?? undefined) ? (
            <span className="ml-auto truncate text-xs text-ink-faint">
              {quotaBarLabel(user.quota ?? undefined)}
            </span>
          ) : engineeringUiEnabled && sessionId ? (
            <span className="ml-auto truncate font-mono text-xs text-ink-faint">
              {sessionId}
            </span>
          ) : null}
        </header>

        {streamError ? (
          <div className="border-b border-red-900/50 bg-red-950/40 px-4 py-2 text-sm text-red-300">
            {streamError}
          </div>
        ) : null}

        <div className="flex-1 overflow-y-auto px-4 py-6">
          <div className="mx-auto w-full max-w-3xl space-y-5">
            {messages.length === 0 ? (
              <p className="py-12 text-center text-sm text-ink-faint">
                How can I help you today?
              </p>
            ) : null}
            {messages.map((m, i) => (
              <ChatMessage
                key={`${m.role}-${i}-${m.id ?? m.content.slice(0, 8)}`}
                role={m.role}
                content={m.content}
                isStreaming={
                  sending &&
                  m.role === "assistant" &&
                  i === messages.length - 1
                }
                messageId={m.id}
                sessionId={sessionId}
                token={token}
                onUnauthorized={logout}
                showAttribution={engineeringUiEnabled}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          onSubmit={onSend}
          className="border-t border-surface-border bg-surface-raised p-4"
        >
          <div className="mx-auto w-full max-w-3xl space-y-2">
            {quotaBlocked ? (
              <p className="text-sm text-amber-400/90">
                Daily token quota exhausted. New messages are disabled until your
                limit resets (see header).
              </p>
            ) : null}
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  quotaBlocked ? "Quota exhausted — cannot send" : "Message…"
                }
                disabled={sending || quotaBlocked}
                className="min-w-0 flex-1 rounded-xl border border-surface-border bg-surface px-4 py-3 text-sm text-ink outline-none ring-accent/30 focus:ring-2 disabled:opacity-50"
              />
              {sending ? (
                <button
                  type="button"
                  onClick={stopGenerating}
                  title="Stop generating"
                  className="flex items-center justify-center rounded-xl border border-surface-border px-4 py-3 text-ink-muted transition hover:bg-surface-muted hover:text-ink"
                >
                  <Square className="h-4 w-4" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim() || quotaBlocked}
                  title="Send message"
                  className="flex items-center justify-center rounded-xl bg-accent px-4 py-3 text-white transition hover:bg-accent-hover disabled:opacity-50"
                >
                  <Send className="h-4 w-4" />
                </button>
              )}
            </div>
            {expertEnabled ? (
              <button
                type="button"
                disabled={sending || !input.trim() || quotaBlocked}
                onClick={() => void runPreview(input.trim())}
                className="self-start text-xs text-accent hover:text-accent-hover disabled:opacity-50"
              >
                Preview before send
              </button>
            ) : null}
          </div>
        </form>
      </div>

      <ConfirmDialog
        open={logoutConfirmOpen}
        title="Log out?"
        description="You'll need to sign in again to continue."
        confirmLabel="Log out"
        cancelLabel="Cancel"
        variant="danger"
        onCancel={() => setLogoutConfirmOpen(false)}
        onConfirm={() => {
          setLogoutConfirmOpen(false);
          logout();
        }}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete this conversation?"
        description="All messages in this chat will be permanently removed. This cannot be undone."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        variant="danger"
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return;
          const sid = deleteTarget.session_id;
          setDeleteTarget(null);
          void deleteSessionConfirmed(sid);
        }}
      />

      <RenameDialog
        open={Boolean(renameTarget)}
        title="Rename conversation"
        initialValue={renameTarget?.title || "New conversation"}
        confirmLabel="Save"
        cancelLabel="Cancel"
        onCancel={() => setRenameTarget(null)}
        onConfirm={(title) => {
          if (!renameTarget) return;
          const sid = renameTarget.session_id;
          setRenameTarget(null);
          void renameSessionConfirmed(sid, title);
        }}
      />

      <PromptPreviewModal
        open={previewOpen}
        loading={previewLoading}
        error={previewError}
        preview={previewData}
        onClose={() => setPreviewOpen(false)}
        onSend={() => {
          const text = pendingSend ?? input.trim();
          if (!text) return;
          setPreviewOpen(false);
          setInput(text);
          void streamChat(text);
        }}
      />
    </div>
  );
}
