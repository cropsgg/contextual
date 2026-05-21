/**
 * API base for browser requests.
 * - Local dev: set NEXT_PUBLIC_API_URL in .env.local (direct to FastAPI, CORS required).
 * - Production: leave unset; calls use same-origin /api and Next rewrites to BACKEND_URL.
 */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
