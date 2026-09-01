# Acknowledgment — DRAFT (not sent)

> ⚠️ **DRAFT — requires your explicit approval before sending.** Nothing has been sent.
> On your "yes," this goes to the recipient below and a **new** `email.send` entry with
> `approval: granted` is **appended** to the audit log. On "no," a new `denied` entry is
> appended and nothing moves. (The log is append-only — the earlier `pending` line is never
> edited; the latest entry per action is the current state.)

- **Lead:** lead-2026-07-14-001 · band **HOT** (5/5)
- **Drafted:** 2026-07-14T09:18:47Z by `cro-sales` — **6m43s after intake, within the 15m SLA** ✅
- **Template:** HOT, from `.claude/skills/lead-response/references/response-templates.md`
- **Recipient:** jordan.lee@acme-robotics.example (fictional example address)
- **Audit:** `lead.response.drafted` logged 09:18:47Z · `email.send` logged 09:18:52Z with `approval: pending`

---

**Subject:** Support-ticket triage without engineers — demo this week?

Hi Jordan,

Thanks for reaching out — doubling ticket volume in a quarter is exactly the kind of load
that breaks a manual triage process, especially when every fix needs an engineer.

Happy to show you exactly how we handle that. I have {{slot_option_1}} or {{slot_option_2}}
open this week — does either work? If it's easier, grab any time here: {{scheduling_link}}.

{{sender_name}}

---

## Left deliberately unfilled (for the human)

`{{slot_option_1}}`, `{{slot_option_2}}`, `{{scheduling_link}}`, `{{sender_name}}` — real
calendar availability and identity are the human's to supply; the skill never invents them
(no fabricated availability, per the template rules).

**Status: Drafted & awaiting your approval.**
