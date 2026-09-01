# Control Center — design direction

**Decisions before pixels.** A dashboard reference was supplied as inspiration. This records
what to take from it, what to adapt, and what to ignore — because the reference is a
*marketing analytics* dashboard and this is an *operations console for an autonomous
company*. Those two things want different interfaces, and copying the wrong parts would make
this product worse.

Status: direction agreed, **not yet implemented**. The existing screens still use the current
system (`app/globals.css`).

---

## 1. What this product actually is

Read the difference before reading the rules.

| | The reference | This product |
|---|---|---|
| Reader's question | "How are we doing?" | **"Does anything need me right now?"** |
| Consequence of a glance | A number is noted | A run is approved, a schedule is paused, money moves |
| Data cadence | Historical, aggregated | Live operational state |
| Wrong design costs | A mildly worse report | A gate approved without reading it |

Everything below follows from one sentence: **this is a console where a tired person makes
irreversible decisions.** Not a place to admire trends.

---

## 2. Take from the reference

**2.1 Persistent left rail with a compact icon+label pattern.** Already how the Control
Center works (`navItems`, `.side-nav`). The reference confirms the shape and shows the
improvement: **a live badge on the nav item that needs attention.** "Work & approvals · 3"
answers the reader's only question from every screen. Adopt.

**2.2 A summary row above the fold.** The reference leads with four stat cards. Adopt the
*structure*, reject the *content*: our four are not revenue metrics but
**Waiting on you · Running · Stalled · Unresolved calls** — the four numbers OBS-08 already
computes. Each tile is a link to its filtered view.

**2.3 Generous card padding and clear panel separation.** The reference's panels breathe.
Our current `.panel` (26px, 1px line) is close; keep the direction, raise the internal
rhythm to an 8px scale.

**2.4 A prominent status ring/summary in the top-right.** The reference uses a donut. Adopt
the *placement and prominence* for a **run-state summary**; see §3.1 on the chart form.

**2.5 Tabular rows with strong left-aligned identity and right-aligned actions.** Correct for
schedules and receipts. Adopt.

## 3. Adapt

**3.1 Charts — keep the position, change the form.** The reference's donut and area chart are
decorative-first. Our equivalents must be *readable at a glance and honest about zero*:
- Run states → a **stacked bar**, not a donut. Donuts are unreadable below ~5% and our
  dangerous category (`stalled`) is usually the smallest slice.
- Approval age → a **single number with a threshold colour**, not a trend line. "4h 12m"
  next to a 4-hour threshold is actionable; a sparkline is not.
- Follow `dataviz` when any chart is built. Never colour by category alone.

**3.2 Colour — invert the reference's priority.** The reference uses colour decoratively
(pastel gradients per card). Here **colour is reserved for governance state**, and the
existing tokens already encode it: `--green` green-band, `--amber` yellow gate, `--red` red
hand-back. Adapt the reference's *balance* (large calm neutral field, colour used sparingly)
but never spend an accent on decoration. A person must be able to scan for amber and red and
find only real gates.

**3.3 Density — denser than the reference.** The reference is spacious because it shows ~8
numbers. An operations console shows a queue that can be 40 rows. Adopt the spacing *scale*
but the tighter end of it; the reference's card heights would put two schedules on a screen.

**3.4 The stat tiles are not decorative.** In the reference they are inert. Here every tile is
a filter and shows a **zero state with meaning**: "Nothing needs you" is a *good* answer and
should read as one — not as an empty card that looks broken.

## 4. Ignore

**4.1 The purple gradient and pastel card fills.** Pretty, and wrong here. Gradient fills
behind numbers reduce contrast, and this product has a WCAG AA obligation (4.5:1). The
existing warm-paper neutral is better for a screen someone stares at during an incident.

**4.2 Rounded, floating, shadowed cards.** Depth implies hierarchy that does not exist here —
every panel is equally real. The current flat 1px-line treatment is more honest and cheaper
to keep consistent.

**4.3 The overlapping "Status" card breaking out of the grid.** Attractive, fragile,
bad on mobile and for screen readers. Ignore.

**4.4 Decorative iconography per nav item.** Icons for their own sake add scanning cost. Use
icons only where they carry state (paused, unresolved, blocked) — see §5.

**4.5 The dense multi-column bottom table.** Our tables need fewer columns and more room per
row, because each row is a decision, not a record.

---

## 5. Type and icons

**Fonts (Google Fonts, `display=swap`, preloaded — per the frontend rules):**

| Role | Face | Why |
|---|---|---|
| UI and headings | **Inter** | Boring, legible at 11–13px, excellent tabular figures |
| Numbers in tiles/tables | **Inter** with `font-variant-numeric: tabular-nums` | Columns of times and counts must align |
| Identifiers, hashes, IDs | **JetBrains Mono** | Run ids, receipt ids, idempotency keys are read character by character |

This replaces the current Geist pair with equivalents; the substitution is neutral in
character and better in numeric alignment. Keep a real fallback stack on both.

**Icons: Material Symbols (Rounded), weight 300, `opsz 20`.** Only state-bearing:
`pause_circle` / `play_circle` (schedule), `schedule` (waiting), `warning` (stalled),
`sync_problem` (unresolved call), `lock` (red, unapprovable). Never an icon alone — always
icon + text, since colour and shape must not be the only carriers.

---

## 6. The design system, stated once

Six rules that keep every screen coherent. These, not the reference, are the source of truth.

1. **One neutral field, three governance accents.** `--paper` ground; `--green`/`--amber`/`--red`
   mean green-band / needs-a-person / never-automated, and nothing else. Ever.
2. **8px spacing scale.** 8 / 16 / 24 / 32. Panel padding 24. No arbitrary values.
3. **Flat surfaces, 1px `--line` separation.** No shadows, no radii above 2px.
4. **Every number is tabular.** Every identifier is mono.
5. **Every list has a written zero state** that says whether zero is good news.
6. **Every destructive or outward control states its consequence in the button's own
   sentence** — "Pause · nothing new will start" beats a bare "Pause".

## 7. Accessibility, non-negotiable

Carried over from the frontend rules, and the reason several reference choices were rejected:
contrast ≥ 4.5:1 for text and ≥ 3:1 for large; nothing conveyed by colour alone; every
control keyboard-reachable with a visible focus ring; the queue and the autonomy tables are
real semantic structures, not styled divs.

## 8. Sequence when this is built

Not now — the current screens work and the priority list has runtime work above it.

1. Tokens and type: swap fonts, formalise the 8px scale, add tabular numerals. *No layout
   change.* Everything else builds on this and it is independently shippable.
2. The four-tile summary row on Overview, wired to the OBS-08 gauges.
3. Nav attention badges.
4. Autonomy screen visual pass (it is functionally complete already).
5. Charts, last, and only if the numbers alone prove insufficient.

Step 1 alone gets most of the coherence. Steps 2–3 get most of the operational value. Step 5
may never be needed — which is the point of doing it last.
