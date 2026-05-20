/** True when NEXT_PUBLIC_DEV_TOOLS=1 (build-time). */
export function isDevToolsEnabledFromEnv(): boolean {
  return process.env.NEXT_PUBLIC_DEV_TOOLS === "1";
}

/** True when URL contains ?debug=1 */
export function isDebugQueryEnabled(search: string): boolean {
  if (!search) return false;
  const q = search.startsWith("?") ? search.slice(1) : search;
  return new URLSearchParams(q).get("debug") === "1";
}
