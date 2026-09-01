# Templates — add your business's specialists

This scaffold ships as a **general-business** company OS: 17 cross-industry departments
(Engineering, Product, Design, Marketing, Sales, Finance, Legal, People, Operations, Data,
Customer, Knowledge, R&D, and the Chief of Staff). No industry-specific skill is active by
default — you add the specialists your business needs.

## Add a new department (agent)

1. Copy `department-agent.template.md` → `.claude/agents/<your-agent>.md` and fill it in.
2. Add a row to `CLAUDE.md` §2 (the org-chart index) and, if it has multiple skills, a section
   to `company/routing-map.md`.
3. Run `bash tests/run.sh` — the core suite's routing-integrity check confirms the new agent is
   wired in and nothing drifted.

## Add a new skill (optional, for a repeatable multi-step capability)

Only add a skill once the workflow is proven manually (see `company/lessons.md`).
1. Copy `skill/SKILL.template.md` → `.claude/skills/<your-skill>/SKILL.md` and fill it in.
2. Reference it from the owning department's agent and from `company/routing-map.md`.
3. Add an optional `tests/module-<name>.sh` if the skill produces artifacts worth validating.

## Activate the Content Studio example (YouTube writer)

A complete, tested example specialist ships **dormant** under `examples/content-studio/`. To turn
it on:
```
cp -R examples/content-studio/youtube-script-writer .claude/skills/
cp    examples/content-studio/head-of-content.md    .claude/agents/
```
Then add its row back to `CLAUDE.md` §2. Its worked runs, playbook, and acceptance report stay in
`examples/content-studio/` as reference. Use it as a worked model for building your own specialist.

## Rules of thumb (from `company/operating-principles.md`)

Before adding any agent, skill, tool, MCP, hook, or dependency, confirm: a real gap exists, the
current setup can't meet it, the outcome is defined, and the change can be tested and reversed.
Keep `CLAUDE.md` lightweight — detail belongs in `company/` and loads on demand.
