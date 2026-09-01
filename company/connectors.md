# Connectors — wiring the company to real data

Every department works today from files, pasted data, and manual input. Each becomes
**live** once its systems are connected via MCP. Connectors need one-time OAuth
authorization, done in an **interactive** session (this scaffold can't authorize them
for you):

- **claude.ai connectors:** authorize in your claude.ai connector settings.
- **Local/other MCP servers:** `claude mcp add …`, or `/mcp` inside an interactive `claude` session.

Until a connector is authorized, its department degrades gracefully to offline mode.

## Suggested connectors by department

| Department | High-value connectors |
|---|---|
| Engineering (CTO) | GitHub, Datadog, PagerDuty, Sentry, CI |
| Product (CPO) | Linear/Jira, Amplitude/Pendo, Figma, Fireflies |
| Design | Figma |
| Marketing (CMO) | Ahrefs, Klaviyo, Similarweb, Supermetrics, Google Analytics |
| Sales (CRO) | HubSpot/Salesforce, Gong, Google Calendar, Gmail |
| Finance (CFO) | QuickBooks, BigQuery, Stripe/PayPal, bank feeds |
| Legal (CLO) | DocuSign, Box/Egnyte, Atlassian, Slack |
| People (CHRO) | HRIS (Workday/BambooHR), ATS (Greenhouse/Lever) |
| Operations (COO) | Asana/ClickUp/Monday, ServiceNow |
| Data | BigQuery, Snowflake, Hex, Definite |
| Customer | Intercom, Zendesk, Guru, HubSpot |
| Customer Success | CRM (HubSpot/Salesforce), product analytics (Amplitude/Pendo), billing (Stripe) |
| RevOps | CRM (HubSpot/Salesforce), ad platforms, analytics |
| Security & GRC | identity provider (Okta/Google), cloud consoles (read), ticketing |
| Knowledge | Google Drive, Box, Confluence, Slack, Gmail |

## Priority order (revenue-critical first)

Until connectors are live, every revenue function runs advisory-only — this is the single
biggest production gap. Authorize in this order:
1. **CRM** (HubSpot/Salesforce) — turns `lead-response`, `funnel-attribution`, `renewals-retention`, and forecasting live.
2. **Email + Calendar** (Gmail/Google Calendar) — Sales, CS, Legal, Chief of Staff.
3. **Support inbox** (Intercom/Zendesk) — Customer + CS health scoring.
4. **Billing/accounting** (Stripe/QuickBooks) — `ar-collections`, MRR/ARR in `kpi-tree`.
Then the rest as each department comes online. The `rnd-tooling` agent can build a custom MCP
server (`anthropic-skills:mcp-builder`) for any internal system without an off-the-shelf connector.

## Secrets & credentials (when connectors go live)

- OAuth flows and credential entry are **always the human's** (🔴) — agents never see, type, or
  store tokens, passwords, or keys.
- Secrets never enter: repo files, `memory/`, `company/lessons.md`, `logs/audit-log.jsonl`,
  task contracts, or URLs. If a tool result exposes one, don't repeat it — flag it for rotation.
- Grant least scope (read-only where read is enough) and prefer per-department connectors over
  one all-powerful credential. Revoking access is also the human's act.
- `security-grc` includes connector grants in its access reviews (who/what/scope/last-used).

## Degraded mode (connector down, rate-limited, or not yet authorized)

- **Fall back, don't guess:** work from exports, pasted data, and files — and label every
  output's source + as-of date so stale data is visible ("from CSV export 2026-07-01", not
  implied-live).
- **Never fabricate** what the connector would have returned; a data gap is reported as a gap.
- SLA clocks that depend on a dead connector pause, with the pause logged (`audit-log`) and
  the human notified — same rule as out-of-hours in `lead-response`.
- Repeated connector failures → task contract to `rnd-tooling`/COO, and a proposed lesson.
