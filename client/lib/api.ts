/**
 * API base for browser requests.
 * - Development: NEXT_PUBLIC_API_URL → direct to local FastAPI (CORS required).
 * - Production: always same-origin /api (Next rewrites to BACKEND_URL on the server).
 *   Ignores NEXT_PUBLIC_API_URL so Railway build vars cannot force cross-origin calls.
 */
export const API_URL =
  process.env.NODE_ENV === "development"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : "";
