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

/** Context monitor, Sources panel, memory inspector — not shown to normal chat users. */
export function isEngineeringUiEnabled(
  search: string = typeof window !== "undefined"
    ? window.location.search
    : "",
): boolean {
  return isDevToolsEnabledFromEnv() || isDebugQueryEnabled(search);
}
