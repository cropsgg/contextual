import axios from "axios";

/** Normalize FastAPI / Axios error payloads for UI. */
export function formatApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const raw = err.response?.data as { detail?: unknown } | undefined;
    const d = raw?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: string }).msg);
          }
          return JSON.stringify(item);
        })
        .join("; ");
    }
    if (d != null) return JSON.stringify(d);
    return err.message || `Request failed (${err.response?.status ?? "network"})`;
  }
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}
