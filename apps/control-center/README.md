# MyOrg Control Center

Signed-in operator surface for governed project intake, controlled work and value-stream
visibility. The browser never selects an organization, actor or role. The hosting worker reads
the platform-authenticated email, signs the exact method/path/body with a 60-second nonce, and
the MyOrg runtime resolves the subject to database-bound organization membership and roles.

## Required environment

- `MYORG_API_URL`: public HTTPS origin of the MyOrg API
- `MYORG_GATEWAY_SECRET`: at least 32 bytes, injected into both worker and API secret stores

Missing configuration, missing identity, bad methods, oversized bodies and non-`/v1/` runtime
paths fail closed. Never expose the gateway secret or a runtime bearer token to browser code.

## Local and CI checks

- `npm run install:ci` — bounded exact lockfile install
- `npm run lint` — application and worker lint
- `npm test` — build, rendered UI checks, fail-closed proxy tests and exact signature test
- `npm run validate:artifact` — ESM Worker and hosting-manifest validation
- `npm audit --omit=dev --audit-level=moderate` — production dependency release gate

Node.js 24 is used in CI. Production state remains in the authoritative MyOrg SQLite service;
the Site does not create a second database. The repository copy lives at `apps/control-center/`;
this Sites checkout is its deployment mirror. Publishing a checkpoint or production deployment
requires the human release gate in the main repository.
