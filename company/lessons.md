# Lessons ledger — verified, reusable

Durable lessons the company has *verified through evidence* — decisions, failures, and patterns
worth reusing. This is shared knowledge across agents. **CLAUDE.md stays the constitution; this
file holds evidence-backed lessons; `memory/` holds business facts (people, customers, vendors).**
The protocol and guardrails that govern this file — recall, contribute, and the Dos & Don'ts — are
canonical in `company/memory-and-learning.md`.

## Rules for this file (controlled knowledge sharing)

- **Only verified lessons.** Each entry needs a real source and executed evidence — not a hunch.
- **Retrieve only what's relevant** to the current task; don't load the whole ledger by default.
- **No self-modification.** An agent may *propose* an entry, but adding, rewriting, or deleting
  lessons — like any change to shared rules or core governance — **requires human approval**
  (see `operating-principles.md` §1). No autonomous or self-rewriting learning loops.
- **Temporary task detail does not belong here** — that's working memory, not a durable lesson.
- Each entry uses the template below. Keep entries short; link to the evidence, don't inline it.

**Template**
```
### <short imperative title>
- **Source:** where it came from (run, review, incident, external doc)
- **Date:** YYYY-MM-DD
- **Evidence:** the executed/observed proof
- **Applies when:** the condition under which the lesson holds
- **Lesson:** the rule to follow next time
```

---

## Lessons

### Verify artifacts on disk — never trust a subagent's self-report
- **Source:** Content Studio production run (`examples/content-studio/runs/…`), recorded in `examples/content-studio/ACCEPTANCE.md`.
- **Date:** 2026-07-11
- **Evidence:** A swarm agent reported "completed" for two episodes but had written no files; only an on-disk file-count check caught it. Re-dispatched with a tightened prompt → files produced; final count verified on disk.
- **Applies when:** dispatching subagents or tools that produce files/outputs, especially in parallel.
- **Lesson:** A "completed" status is a claim to check, not evidence. Confirm the actual output exists and meets the bar before reporting done. Codified in `operating-principles.md` §7.

### Keep the router in sync with the agent inventory
- **Source:** External-skills audit, 2026-07 (report removed from the tree; preserved in git history), adapting the "router must stay synchronized" invariant from `mattpocock/skills` (MIT).
- **Date:** 2026-07-14
- **Evidence:** Two agent files (`chief-of-staff`, `rnd-tooling`) existed but were not referenced in `CLAUDE.md`; a negative-control test confirmed the drift. Added a routing-integrity check to the suite.
- **Applies when:** adding, renaming, or removing an agent or department.
- **Lesson:** The routing index and the `.claude/agents/` inventory must not drift. The `tests/core.sh` routing-integrity check enforces this; run it after any org change.

### Prefer adapting small prose/tests over adopting external tooling
- **Source:** External-skills audit of 10 repos, 2026-07 (report removed from the tree; preserved in git history).
- **Date:** 2026-07-14
- **Evidence:** Of 10 reviewed repos, the genuinely-novel capabilities all shipped as auto-executing hooks / `curl|bash` installers / telemetry — incompatible with this no-hooks, human-in-the-loop scaffold. Only 3 small prose/test components cleared the bar; all validated by the test suite.
- **Applies when:** evaluating any external skill, plugin, hook, or MCP for adoption.
- **Lesson:** This scaffold is a thin, no-hooks, human-in-the-loop router. Adopt small prose/conventions and tests; treat hook-installing external tools as opt-in, sandboxed, human-approved additions only — never auto-loaded. Check license, maintenance, and security before adopting; popularity is not a criterion.
