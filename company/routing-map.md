# Routing Map — request → department → skill

**Load this when** you're inside a department's work and need the exact skill for a micro-task.
For top-level routing (which department/agent owns a request), the compact index in `CLAUDE.md`
§2 is enough — each agent file (`.claude/agents/*.md`) also carries its own skill list. This file
is the detailed catalog, kept out of the always-loaded constitution on purpose.

Match the user's intent to a row, then invoke the skill (or dispatch the agent). A catalog entry
is not proof that its plugin is installed: check availability before promising execution. If it
is unavailable, use the department charter with existing tools, label the degraded mode, and ask
for installation only when the named outcome cannot otherwise be delivered.

### Engineering — CTO  → agent: `cto-engineering`
| If they want to… | Use |
|---|---|
| Design a system / choose tech / write an ADR | `engineering:architecture`, `engineering:system-design` |
| Review a PR / diff for bugs & security | `engineering:code-review`, `security-review`, `code-review` |
| Debug a failure | `engineering:debug` |
| Ship safely | `engineering:deploy-checklist`, `verify`, `run` |
| Handle an outage | `engineering:incident-response` |
| Plan tests / pay down debt / document | `engineering:testing-strategy`, `engineering:tech-debt`, `engineering:documentation` |
| Run a standup | `engineering:standup` |
| Build on the Claude API | `claude-api` |

### Product — CPO  → agent: `cpo-product`
| Want to… | Use |
|---|---|
| Write a PRD / spec | `product-management:write-spec` |
| Update roadmap / plan a sprint | `product-management:roadmap-update`, `product-management:sprint-planning` |
| Review metrics | `product-management:metrics-review` |
| Brainstorm an idea / problem | `product-management:product-brainstorming`, `product-management:brainstorm` |
| Synthesize user research | `product-management:synthesize-research` |
| Update stakeholders | `product-management:stakeholder-update` |
| Size up a competitor | `product-management:competitive-brief` |

### Design — Head of Design  → agent: `head-of-design`
| Want to… | Use |
|---|---|
| Critique a mockup/screen | `design:design-critique` |
| Build/maintain a design system | `design:design-system` |
| Write UI copy | `design:ux-copy` |
| Check accessibility | `design:accessibility-review` |
| Run/synthesize user research | `design:user-research`, `design:research-synthesis` |
| Prep engineering handoff | `design:design-handoff` |
| Work in Figma / design→code | `figma:figma-use`, `figma:figma-design-to-code`, `figma:figma-generate-design` |
| Build a themed web artifact | `anthropic-skills:web-artifacts-builder`, `anthropic-skills:theme-factory`, `artifact-design` |

### Marketing — CMO  → agent: `cmo-marketing`
| Want to… | Use |
|---|---|
| Plan a campaign | `marketing:campaign-plan` |
| Create / draft content | `marketing:content-creation`, `marketing:draft-content` |
| Audit / improve SEO | `marketing:seo-audit` |
| Build an email sequence | `marketing:email-sequence` |
| Report on performance | `marketing:performance-report` |
| Review brand consistency | `marketing:brand-review`, `brand-voice:enforce-voice` |
| Establish brand voice | `brand-voice:discover-brand`, `brand-voice:generate-guidelines`, `anthropic-skills:brand-guidelines` |
| Write internal comms | `anthropic-skills:internal-comms` |
| Run paid ads / fix landing pages / build nurture / referral program | `demand-gen` |
| Respond to reviews / handle a public incident / collect testimonials | `reputation-management` |

### Sales — CRO  → agent: `cro-sales`
| Want to… | Use |
|---|---|
| Handle a new inbound lead (qualify → route → gated draft, under SLA) | `lead-response` |
| Check lead-response SLA status / breaches | `lead-response` + `audit-log` review |
| Tune lead SLA targets, ICP, routing | `revops` owns (CRO consulted) — standing-rule change, 🟡 gated |
| Research an account | `sales:account-research` |
| Prep / summarize a call | `sales:call-prep`, `sales:call-summary` |
| Draft outreach | `sales:draft-outreach` |
| Review pipeline / forecast | `sales:pipeline-review`, `sales:forecast` |
| Get a daily briefing | `sales:daily-briefing` |
| Competitive intel | `sales:competitive-intelligence` |
| Create a sales asset | `sales:create-an-asset` |
| Build a quote / handle a discount request / non-standard terms | `deal-desk` |

### Finance — CFO  → agent: `cfo-finance`
| Want to… | Use |
|---|---|
| Chase overdue invoices / failed payments (gated dunning) | `ar-collections` |
| AR aging / DSO / collections escalation | `ar-collections` |
| Build financial statements | `finance:financial-statements` |
| Reconcile accounts | `finance:reconciliation` |
| Run the monthly close | `finance:close-management` |
| Book / prep journal entries | `finance:journal-entry`, `finance:journal-entry-prep` |
| Explain variances | `finance:variance-analysis` |
| Support an audit / SOX | `finance:audit-support`, `finance:sox-testing` |
| Model in a spreadsheet | `anthropic-skills:xlsx` |

### Legal — CLO  → agent: `clo-legal`
| Want to… | Use |
|---|---|
| Review a contract | `legal:review-contract` |
| Triage an NDA | `legal:triage-nda` |
| Check compliance of an action | `legal:compliance-check` |
| Assess legal risk | `legal:legal-risk-assessment` |
| Respond to a legal inquiry (DSR, hold, subpoena) | `legal:legal-response` |
| Route for e-signature | `legal:signature-request` |
| Check vendor agreements | `legal:vendor-check` |
| Daily legal brief / meeting prep | `legal:brief`, `legal:meeting-briefing` |
| Handle a DSR / suspected breach / privacy review | `privacy-program` |
| Track signed contracts / auto-renew traps / obligations | `contract-lifecycle` |

### People — CHRO  → agent: `chro-people`
| Want to… | Use |
|---|---|
| Manage recruiting pipeline | `human-resources:recruiting-pipeline` |
| Draft an offer | `human-resources:draft-offer` |
| Prep an interview | `human-resources:interview-prep` |
| Onboard a hire | `human-resources:onboarding` |
| Run a performance review | `human-resources:performance-review` |
| Analyze comp | `human-resources:comp-analysis` |
| Plan the org | `human-resources:org-planning` |
| People metrics / policy lookup | `human-resources:people-report`, `human-resources:policy-lookup` |

### Operations — COO  → agent: `coo-operations`
| Want to… | Use |
|---|---|
| Write a status report | `operations:status-report` |
| Author a runbook | `operations:runbook` |
| Document / optimize a process | `operations:process-doc`, `operations:process-optimization` |
| Assess risk | `operations:risk-assessment` |
| Plan capacity | `operations:capacity-plan` |
| Review a vendor | `operations:vendor-review` |
| File a change request | `operations:change-request` |
| Track compliance | `operations:compliance-tracking` |
| Monthly contract sweep / notice-window alerts | `contract-lifecycle` (co-owned with CLO) |
| Review the audit log (breaches, pending approvals) | `audit-log` |

### Data — Head of Data  → agent: `head-of-data`
| Want to… | Use |
|---|---|
| Analyze a dataset | `data:analyze`, `data:explore-data` |
| Write / run SQL | `data:write-query`, `data:sql-queries` |
| Build a dashboard | `data:build-dashboard` |
| Visualize | `data:create-viz`, `data:data-visualization`, `dataviz` |
| Run statistics | `data:statistical-analysis` |
| Validate / QA data | `data:validate-data` |
| Define company KPIs / unit economics / find revenue leaks / design experiments | `kpi-tree` |

### Customer — Head of Customer  → agent: `head-of-customer`
| Want to… | Use |
|---|---|
| Triage a ticket | `customer-support:ticket-triage` |
| Draft a customer response | `customer-support:draft-response` |
| Research a customer question | `customer-support:customer-research` |
| Escalate to eng/product | `customer-support:customer-escalation` |
| Write a KB article | `customer-support:kb-article` |

### Customer Success — proactive lifecycle  → agent: `customer-success`
| Want to… | Use |
|---|---|
| Score account health / find at-risk accounts | `renewals-retention` |
| Work a renewal / run a save-play / plan expansion | `renewals-retention` |
| Onboarding-to-value plan / QBR prep / CSAT-NPS readout | `renewals-retention` |
| Churn post-mortem | `renewals-retention` (+ propose a lesson) |

### RevOps — the funnel as a system  → agent: `revops`
| Want to… | Use |
|---|---|
| Define/repair funnel stages & MQL→SQL handoffs | `funnel-attribution` |
| Which channel produces revenue (attribution) | `funnel-attribution` |
| Audit/clean the CRM (gated mass changes) | `funnel-attribution` |
| Tune lead SLAs / routing rules | `lead-response` config (with CRO; standing-rule change) |

### Security & GRC — the trust program  → agent: `security-grc`
| Want to… | Use |
|---|---|
| Answer a security questionnaire (gated send) | `grc-readiness` |
| SOC2/ISO readiness + gap list | `grc-readiness` |
| Vet a vendor's security / run an access review | `grc-readiness` |
| Security-incident coordination | `grc-readiness` + `engineering:incident-response` |

### Knowledge — Chief Knowledge Officer  → agent: `chief-knowledge-officer`
| Want to… | Use |
|---|---|
| Search across company sources | `enterprise-search:search`, `enterprise-search:search-strategy` |
| Daily digest | `enterprise-search:digest` |
| Synthesize knowledge | `enterprise-search:knowledge-synthesis` |
| Deep, cited research report | `deep-research` |
| Manage sources | `enterprise-search:source-management` |

### Content Studio — Head of Content  → agent: `head-of-content`  *(dormant example — not auto-loaded)*
This department is a **dormant example module**. Its agent and skill live under
`examples/content-studio/` and are **not** active until you copy them into `.claude/`
(see `templates/README.md`). Remove it, activate it, or use it as a model for your own business's
specialist — the rest of the OS doesn't depend on it. Once activated, it routes:
| Want to… | Use |
|---|---|
| Write a YouTube script / video | `youtube-script-writer` |
| Build a 15-episode video series | `youtube-script-writer` |
| Research a video topic (keywords + GEO questions) | `youtube-script-writer` (research leg), `deep-research` |
| Grow subscribers / viewers / followers | `youtube-script-writer` (growth plan) |
| Turn the writer into a specific channel's dedicated writer | fill `examples/content-studio/youtube-script-writer/config/channel-profile.md` |

### R&D / Tooling (grow the company)  → agent: `rnd-tooling`
| Want to… | Use |
|---|---|
| Build a new skill (hire a new capability) | `anthropic-skills:skill-creator` |
| Build an MCP server (wire a new system) | `anthropic-skills:mcp-builder` |
| Create / customize a plugin | `cowork-plugin-management:create-cowork-plugin`, `cowork-plugin-management:cowork-plugin-customizer` |

### Documents (shared services — any department)
`anthropic-skills:docx` · `anthropic-skills:pptx` · `anthropic-skills:xlsx` ·
`anthropic-skills:pdf` · `anthropic-skills:doc-coauthoring` · `pdf-viewer:*`

### Audit log (shared service — any department; COO reviews)
| Want to… | Use |
|---|---|
| Record a gated action / refusal / SLA event | `audit-log` (appends to `logs/audit-log.jsonl`) |
| Answer "who approved what, when?" / review breaches & pending approvals | `audit-log` (review recipes in `logs/README.md`) |

### Organization management (Chief of Staff)
| User asks for | Skill |
|---|---|
| Goals, task ownership, decisions, organization status | `organization-management` |

---

*To add a department: add an agent file in `.claude/agents/`, a row to the `CLAUDE.md` §2 index, and
a section here. To repurpose the OS for another business, swap the optional modules and edit these
rows — the operating loop and governance stay the same.*
