import { NextRequest, NextResponse } from "next/server";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
]);

/** Resolved at request time (not at `next build`). */
export function resolveBackendUrl(): string | null {
  const raw = process.env.BACKEND_URL?.trim();
  if (raw) {
    return raw.replace(/\/$/, "");
  }
  if (process.env.NODE_ENV === "development") {
    const dev =
      process.env.NEXT_PUBLIC_API_URL?.trim() || "http://localhost:8000";
    return dev.replace(/\/$/, "");
  }
  return null;
}

function forwardRequestHeaders(req: NextRequest): Headers {
  const headers = new Headers(req.headers);
  for (const name of HOP_BY_HOP) {
    headers.delete(name);
  }
  headers.delete("host");
  return headers;
}

function forwardResponseHeaders(upstream: Response): Headers {
  const headers = new Headers(upstream.headers);
  for (const name of HOP_BY_HOP) {
    headers.delete(name);
  }
  return headers;
}

export async function proxyToBackend(
  req: NextRequest,
  backendPath: string,
): Promise<NextResponse> {
  const backend = resolveBackendUrl();
  if (!backend) {
    return NextResponse.json(
      {
        detail:
          "BACKEND_URL is not set on the UI service. Set it to your API public URL (e.g. https://glistening-determination-production-f71b.up.railway.app).",
      },
      { status: 503 },
    );
  }

  const url = `${backend}${backendPath}${req.nextUrl.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: forwardRequestHeaders(req),
    redirect: "manual",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch (err) {
    console.error(`Failed to proxy ${url}`, err);
    return NextResponse.json(
      { detail: "Unable to reach the API service." },
      { status: 502 },
    );
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: forwardResponseHeaders(upstream),
  });
}
