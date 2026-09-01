/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";

interface Env {
  ASSETS: Fetcher;
  DB?: D1Database;
  MYORG_API_URL?: string;
  MYORG_GATEWAY_SECRET?: string;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/api/runtime/")) {
      return withSecurityHeaders(request, await proxyRuntime(request, env));
    }

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      const response = await handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
      return withSecurityHeaders(request, response);
    }

    return withSecurityHeaders(request, await handler.fetch(request, env, ctx));
  },
};

const SAFE_METHODS = new Set(["GET", "POST", "PUT", "DELETE"]);
const MAX_BODY_BYTES = 262_144;

async function proxyRuntime(request: Request, env: Env): Promise<Response> {
  const traceId = crypto.randomUUID();
  const fail = (status: number, code: string, message: string) => Response.json(
    { error: { code, message } },
    { status, headers: { "X-Trace-Id": traceId } },
  );
  if (!env.MYORG_API_URL || !env.MYORG_GATEWAY_SECRET) {
    return fail(503, "runtime_unavailable", "the governed runtime is not configured");
  }
  if (new TextEncoder().encode(env.MYORG_GATEWAY_SECRET).byteLength < 32) {
    return fail(503, "runtime_unavailable", "the runtime gateway is misconfigured");
  }
  const email = request.headers.get("oai-authenticated-user-email")?.trim().toLowerCase();
  if (!email || email.length > 320 || /[\r\n]/.test(email)) {
    return fail(401, "unauthorized", "a signed-in operator is required");
  }
  if (!SAFE_METHODS.has(request.method)) {
    return fail(405, "method_not_allowed", "method is not allowed");
  }
  const incoming = new URL(request.url);
  if (incoming.search || incoming.hash) {
    return fail(400, "invalid_request", "query strings are not accepted");
  }
  const runtimePath = incoming.pathname.slice("/api/runtime".length);
  if (!runtimePath.startsWith("/v1/")) {
    return fail(404, "not_found", "route not found");
  }
  let base: URL;
  try {
    base = new URL(env.MYORG_API_URL);
  } catch {
    return fail(503, "runtime_unavailable", "the runtime gateway is misconfigured");
  }
  if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash) {
    return fail(503, "runtime_unavailable", "the runtime gateway is misconfigured");
  }
  const contentLength = Number(request.headers.get("Content-Length") ?? "0");
  if (!Number.isFinite(contentLength) || contentLength > MAX_BODY_BYTES) {
    return fail(413, "payload_too_large", "request exceeds 256 KiB");
  }
  const body = request.method === "POST" || request.method === "PUT"
    ? new Uint8Array(await request.arrayBuffer())
    : new Uint8Array();
  if (body.byteLength > MAX_BODY_BYTES) {
    return fail(413, "payload_too_large", "request exceeds 256 KiB");
  }
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomUUID().replaceAll("-", "");
  const issuer = "chatgpt-sites";
  const audience = "myorg-api";
  const bodyHash = await sha256Hex(body);
  const signingInput = [request.method, runtimePath, timestamp, nonce, issuer, email, audience, bodyHash].join("\n");
  const signature = await hmacHex(env.MYORG_GATEWAY_SECRET, signingInput);
  const headers = new Headers({
    "X-MyOrg-Gateway-Issuer": issuer,
    "X-MyOrg-Gateway-Subject": email,
    "X-MyOrg-Gateway-Audience": audience,
    "X-MyOrg-Gateway-Timestamp": timestamp,
    "X-MyOrg-Gateway-Nonce": nonce,
    "X-MyOrg-Gateway-Signature": `v1=${signature}`,
    "X-Trace-Id": traceId,
  });
  if (body.byteLength) headers.set("Content-Type", "application/json");
  if (request.method === "POST" && runtimePath === "/v1/projects") {
    headers.set("Idempotency-Key", crypto.randomUUID());
  } else if (request.method === "POST" || request.method === "PUT" || request.method === "DELETE") {
    headers.set("X-Request-Id", crypto.randomUUID());
  }
  let upstream: Response;
  try {
    upstream = await fetch(new URL(runtimePath, base), {
      method: request.method,
      headers,
      body: body.byteLength ? body : undefined,
      redirect: "error",
      signal: AbortSignal.timeout(8_000),
    });
  } catch {
    return fail(503, "runtime_unavailable", "the governed runtime could not be reached");
  }
  const responseHeaders = new Headers();
  responseHeaders.set("Content-Type", upstream.headers.get("Content-Type") ?? "application/json; charset=utf-8");
  responseHeaders.set("X-Trace-Id", upstream.headers.get("X-Trace-Id") ?? traceId);
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", value);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(secret: string, value: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return [...new Uint8Array(signature)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

function withSecurityHeaders(request: Request, response: Response): Response {
  const secured = new Response(response.body, response);
  secured.headers.set("Cache-Control", "no-store");
  secured.headers.set(
    "Content-Security-Policy",
    "default-src 'self'; base-uri 'self'; connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data:; object-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
  );
  secured.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=() ");
  secured.headers.set("Referrer-Policy", "no-referrer");
  secured.headers.set("X-Content-Type-Options", "nosniff");
  secured.headers.set("X-Frame-Options", "DENY");
  if (new URL(request.url).protocol === "https:") {
    secured.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  }
  return secured;
}

export default worker;
