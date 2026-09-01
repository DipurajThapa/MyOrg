import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import test from "node:test";

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;

test("renders development preview metadata", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        "oai-authenticated-user-email": "operator@example.com",
        "oai-authenticated-user-full-name": "MyOrg%20Operator",
        "oai-authenticated-user-full-name-encoding": "percent-encoded-utf-8",
      },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );

  assert.equal(response.status, 200);
  assert.match(
    response.headers.get("content-type") ?? "",
    /^text\/html\b/i,
  );
  const html = await response.text();
  assert.match(html, developmentPreviewMeta);
  assert.match(html, /MyOrg Control Center/i);
  assert.match(html, /Frame the work/i);
  assert.match(html, /Release gate/i);
  assert.match(html, /Begin project intake/i);
  // The overview shows what is really waiting on a person. Server-side there is no
  // session yet, so the honest render is the loading/empty state -- never invented work.
  assert.match(html, /WAITING ON YOU/i);
  assert.match(html, /Reading|Nothing needs you/i);
  assert.match(html, /Open queue/i);
  assert.match(html, /you can decide/i);
  // Guard against the regression this replaced: the panel used to ship a fabricated run
  // ("Maker-checker validation", "13 / 24 cycles") that no runtime had ever produced, and
  // two Preview buttons that did nothing. If invented run data returns, fail here.
  assert.doesNotMatch(html, /\d+\s*(?:<!--[^>]*-->\s*)?\/\s*(?:<!--[^>]*-->\s*)?\d+\s*cycles/i,
    "the overview must not ship hard-coded run progress");
  assert.doesNotMatch(html, /maker-checker-validation|RUN-0001/i,
    "the overview must not ship a hard-coded run id");
  assert.doesNotMatch(html, /Preview approve|Preview return/i,
    "decision controls must call the runtime, not preview a decision that never happens");
  assert.match(html, /Governed · durable/i);
  assert.match(html, /Skip to main content/i);
  assert.match(html, /id="main-content"/i);
  assert.match(html, /MyOrg Operator/i);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.match(response.headers.get("content-security-policy") ?? "", /frame-ancestors 'none'/i);
  assert.equal(response.headers.get("x-frame-options"), "DENY");
});

test("runtime proxy fails closed when configuration or identity is absent", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `closed-${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const ctx = { waitUntil() {}, passThroughOnException() {} };
  const assets = { fetch: async () => new Response("Not found", { status: 404 }) };
  const unconfigured = await worker.fetch(new Request("https://control.example/api/runtime/v1/me", {
    headers: { "oai-authenticated-user-email": "operator@example.com" },
  }), { ASSETS: assets }, ctx);
  assert.equal(unconfigured.status, 503);
  const unsigned = await worker.fetch(new Request("https://control.example/api/runtime/v1/me"), {
    ASSETS: assets,
    MYORG_API_URL: "https://runtime.example",
    MYORG_GATEWAY_SECRET: "gateway-0123456789abcdef0123456789abcdef",
  }, ctx);
  assert.equal(unsigned.status, 401);
});

test("runtime proxy signs the platform identity and exact request body", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `signed-${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const secret = "gateway-0123456789abcdef0123456789abcdef";
  const body = JSON.stringify({ active_view: "intake" });
  let captured;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    captured = new Request(input, init);
    return Response.json({ revision: 1 }, { headers: { "X-Trace-Id": "runtime-trace" } });
  };
  let response;
  try {
    response = await worker.fetch(new Request("https://control.example/api/runtime/v1/ui-state", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "oai-authenticated-user-email": "Operator@Example.com" },
      body,
    }), {
      ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
      MYORG_API_URL: "https://runtime.example",
      MYORG_GATEWAY_SECRET: secret,
    }, { waitUntil() {}, passThroughOnException() {} });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(response.status, 200);
  assert.equal(captured.url, "https://runtime.example/v1/ui-state");
  assert.equal(captured.headers.get("x-myorg-gateway-subject"), "operator@example.com");
  assert.equal(await captured.text(), body);
  const timestamp = captured.headers.get("x-myorg-gateway-timestamp");
  const nonce = captured.headers.get("x-myorg-gateway-nonce");
  const bodyHash = createHash("sha256").update(body).digest("hex");
  const signingInput = ["PUT", "/v1/ui-state", timestamp, nonce, "chatgpt-sites",
    "operator@example.com", "myorg-api", bodyHash].join("\n");
  assert.equal(captured.headers.get("x-myorg-gateway-signature"),
    `v1=${createHmac("sha256", secret).update(signingInput).digest("hex")}`);
  assert.match(captured.headers.get("x-request-id") ?? "", /^[0-9a-f-]{36}$/);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("x-trace-id"), "runtime-trace");
});
