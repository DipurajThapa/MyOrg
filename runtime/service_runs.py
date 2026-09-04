#!/usr/bin/env python3
"""Starting work, seeing it, deciding on it, and reading what it produced."""
from __future__ import annotations

from pathlib import Path
from runtime.auth import Principal
import argparse

from runtime.service_core import Forbidden, ID_RE, ServiceError, _quietly, _require


class RunsServiceMixin:
    """Starting work, seeing it, deciding on it, and reading what it produced."""

    def runs(self, principal: Principal) -> list[dict]:
        """Every run in this organization, newest change first, with what a person can do
        about it. Read-only; the same audience that may see the decision queue."""
        from runtime import company_runtime as core
        from runtime.escalation import DEAD_END, NEXT_STEP
        rows = self.store.runs(principal.org_id)
        summary = self.store.run_step_summary(principal.org_id)
        for row in rows:
            status = row.get("runtime_status")
            row["can_cancel"] = status not in core.TERMINAL_RUN
            # "8 of 12 cycles" tells nobody where the work is. The steps do, and the
            # projection already holds them.
            progress = summary.get(row["id"], {"total": 0, "done": 0, "blocking": None})
            row["steps_total"] = progress["total"]
            row["steps_done"] = progress["done"]
            row["blocking_step"] = progress["blocking"]
            # And what happened is only half of it: NEXT_STEP is the same wording the
            # notice uses, so the two cannot tell an operator different things.
            row["next_step"] = NEXT_STEP.get(status, "") if status in core.TERMINAL_RUN else ""
            # `blocked_retry_limit` is not an explanation. The plain-language reason already
            # exists -- escalation writes it into every notice -- and the page was left
            # showing the status word at exactly the moment something went wrong. One
            # source for both, so a new terminal state cannot be legible in a notice and
            # jargon on the screen.
            row["reason"] = DEAD_END.get(status, "") if status in core.TERMINAL_RUN else ""
        return rows

    def pending_decisions(self, principal: Principal) -> list[dict]:
        """Everything in this organization waiting on a person, worst first."""
        from runtime import approvals
        return [{"run_id": d.run_id, "step": d.step_id, "status": d.status,
                 "owner": d.owner, "action": d.action, "risk": d.risk, "goal": d.goal,
                 "workflow_id": d.workflow_id, "reason": d.reason, "impact": d.impact,
                 "actionable": d.actionable, "unblocks": d.unblocks, "depth": d.depth,
                 "waiting_since": d.waiting_since, "held_reason": d.held_reason,
                 "context": [{"step": step, "excerpt": text} for step, text in d.context],
                 "brief": d.brief.as_text() if d.brief else ""}
                for d in approvals.pending(org_id=principal.org_id)]

    def decide_step(self, principal: Principal, run_id: str, step_id: str,
                    body: dict, request_id: str) -> dict:
        """Approve or reject one parked step, as a named human, with a stated reason."""
        _require(principal, "decision-owner")
        if principal.actor_type != "human":
            raise Forbidden("decisions require a registered human identity")
        if set(body) != {"decision", "reason"} or body["decision"] not in {"approve", "reject"}:
            raise ServiceError("decision must be approve/reject with a reason")
        reason = str(body["reason"]).strip()
        if not 1 <= len(reason) <= 200 or not reason.isprintable():
            raise ServiceError("reason must be 1..200 printable characters on one line")
        if not ID_RE.fullmatch(str(run_id)) or not ID_RE.fullmatch(str(step_id)):
            raise ServiceError("invalid run or step id")

        from runtime import company_runtime as core
        state = self._run_state(principal, run_id)
        step = state["steps"].get(step_id)
        if not step:
            raise ServiceError(f"unknown step: {step_id}")
        if step["status"] != "awaiting_approval":
            raise ServiceError(
                f"{step_id} is {step['status']}, which is not a decision anyone can take")

        who = principal.display_name or principal.actor_id
        command = core.approve if body["decision"] == "approve" else core.reject
        arguments = argparse.Namespace(run_id=run_id, step=step_id, approver=who,
                                       actor_id=principal.actor_id,
                                       approval_ref=reason, request_id=request_id)
        try:
            _quietly(command, arguments)
        except SystemExit as error:
            raise ServiceError(str(error)) from error
        self.store.record_step_decision(principal.org_id, principal.actor_id, run_id,
                                        step_id, body["decision"], request_id)
        return {"run_id": run_id, "step": step_id, "decision": body["decision"],
                "status": core.read_events(run_id)[-1]["steps"][step_id]["status"]}

    def cancel_run(self, principal: Principal, run_id: str, body: dict, request_id: str) -> dict:
        """Stop a run, as a named human, with a stated reason (B-02). Same authority and
        same org scoping as a step decision: an agent cannot stop its own run to escape a
        gate, and another org's run answers exactly like a run that does not exist."""
        _require(principal, "decision-owner")
        if principal.actor_type != "human":
            raise Forbidden("stopping a run requires a registered human identity")
        if set(body) != {"reason"}:
            raise ServiceError("a cancel takes exactly one field: reason")
        reason = str(body["reason"]).strip()
        if not 1 <= len(reason) <= 200 or not reason.isprintable():
            raise ServiceError("reason must be 1..200 printable characters on one line")
        if not ID_RE.fullmatch(str(run_id)):
            raise ServiceError("invalid run id")

        from runtime import company_runtime as core
        self._run_state(principal, run_id)
        who = principal.display_name or principal.actor_id
        try:
            _quietly(core.cancel_run, argparse.Namespace(
                run_id=run_id, approver=who, actor_id=principal.actor_id,
                reason=reason, request_id=request_id))
        except SystemExit as error:
            raise ServiceError(str(error)) from error
        return {"run_id": run_id, "status": core.read_events(run_id)[-1]["run_status"]}

    # --- work in, work out -------------------------------------------------------
    # The two halves an operator actually needs: say what the company should do, and
    # read what it produced. Both sit on machinery that already existed and was reachable
    # only from a webhook or the filesystem.

    def submit_idea(self, principal: Principal, body: dict, request_id: str) -> dict:
        """Queue a goal for the planner, as `operator` rather than a webhook or a schedule.

        This creates no run itself. The scheduler's next intake pass plans the goal into a
        workflow and starts it, exactly as it does for every other trigger source -- so an
        idea typed here is governed identically to one that arrived from outside, and the
        queue-depth refusal that protects the planner protects this too."""
        # `decision-owner` is here alongside the roles `create_run` takes. Asking for work is
        # green: it plans and starts internal steps, and every outward step still parks at
        # this same person's gate. Withholding it would mean the human who must approve the
        # company's actions cannot ask it to act -- and a webhook, which needs no role at
        # all, could start work they could not.
        _require(principal, "decision-owner", "chief-of-staff", "system-admin")
        # A webhook starts work but never says what the work *is* -- it selects a goal a
        # human registered in advance, because "a payload that could name its own goal
        # would be an instruction from outside the trust boundary". This route is the one
        # place free goal text reaches the planner, so the same boundary has to hold here:
        # an agent holding a chief-of-staff token could otherwise write its own instructions,
        # queue them, and have the company plan and run them with nobody having asked.
        if principal.actor_type != "human":
            raise Forbidden("asking the company for work requires a registered human identity")
        if set(body) != {"goal"}:
            raise ServiceError("an idea takes exactly one field: goal")
        goal = str(body["goal"]).strip()
        if not 10 <= len(goal) <= 500:
            raise ServiceError("goal must be 10..500 characters")
        if not goal.isprintable():
            raise ServiceError("goal must be one line of printable text")
        from runtime import triggers
        try:
            row, created = triggers.enqueue(
                self.store, principal.org_id,
                triggers.intake_id("operator", request_id), "operator", request_id, goal)
        except triggers.TriggerError as error:
            raise ServiceError(str(error)) from error
        return {"intake_id": row["id"], "run_id": triggers.run_id_for(row),
                "status": row["status"], "goal": row["goal"], "created": created}

    def withdraw_idea(self, principal: Principal, intake_id: str, body: dict,
                      request_id: str) -> dict:
        """Take a queued request out of the line, as a named human, with a stated reason.

        Once a request is a run, `cancel_run` stops it. Before this there was nothing for
        the step in between: a goal typed by mistake could only leave the queue by the
        planner spending real money on it three times and giving up.

        The serial queue made that a dead end rather than an annoyance. A failure that
        looks temporary deliberately spends none of a request's three attempts, so a
        planner that stays unreachable keeps the front of the line for ever and nothing
        behind it starts. Withdrawing the head is the only thing that frees the queue.

        `settle_trigger` only matches a row that is still `queued`, so this races the
        sweeper safely: if intake started the work first, the withdrawal is refused and the
        run is there to cancel instead. An id from another organization, an id that never
        existed, and one already settled all answer the same way -- the queue cannot be
        used to find out what another company asked for.
        """
        _require(principal, "decision-owner", "chief-of-staff", "system-admin")
        if principal.actor_type != "human":
            raise Forbidden("withdrawing a request requires a registered human identity")
        if set(body) != {"reason"}:
            raise ServiceError("a withdrawal takes exactly one field: reason")
        reason = str(body["reason"]).strip()
        if not 1 <= len(reason) <= 200 or not reason.isprintable():
            raise ServiceError("reason must be 1..200 printable characters on one line")
        if not ID_RE.fullmatch(str(intake_id)):
            raise ServiceError("invalid request id")
        from runtime.db import Conflict
        who = principal.display_name or principal.actor_id
        try:
            row = self.store.settle_trigger(
                principal.org_id, intake_id, "withdrawn", None,
                f"withdrawn by {who}: {reason}", count_attempt=False)
        except Conflict as error:
            raise ServiceError("that request is not waiting in the queue -- it has already "
                               "started, been withdrawn, or never existed") from error
        return {"intake_id": row["id"], "status": row["status"], "goal": row["goal"],
                "withdrawn_by": who}

    def ideas(self, principal: Principal) -> list[dict]:
        """Everything asked for that is not yet visible as a run.

        A trigger is marked `started` the instant intake plans it, but the read model
        `runs()` reads is only mirrored at the end of a sweep -- which is as long as the
        slowest run in that pass. Listing queued *and* started-but-unmirrored work is what
        stops an idea from vanishing for minutes between the two lists.
        """
        from runtime import triggers
        from runtime.backends import is_transient
        rows = []
        for row in self.store.unfinished_triggers(principal.org_id, 50):
            # The same courtesy runs get: say what is left to try. A request retrying on a
            # busy server and one that has given up look identical on a screen otherwise,
            # and they need opposite things from a person.
            if row["status"] == "failed":
                advice = ("Planning gave up. Read the error, then ask again with a shorter "
                          "or clearer goal.")
            elif row["last_error"] and is_transient(row["last_error"]):
                advice = ("The other end is busy. This retries on its own and spends none of "
                          "its attempts, so it needs nothing from you -- withdraw it only if "
                          "you need the queue free for something else.")
            elif row["last_error"]:
                advice = (f"Planning failed {row['attempts']} time(s) and will try again. "
                          "If the error names something you can fix, withdraw it and ask again.")
            else:
                advice = ""
            rows.append({"intake_id": row["id"], "source": row["source"], "goal": row["goal"],
                         "status": row["status"], "attempts": row["attempts"],
                         "last_error": row["last_error"], "next_step": advice,
                         "run_id": row["run_id"] or triggers.run_id_for(row)})
        return rows

    MAX_HISTORY_EVENTS = 200

    def run_history(self, principal: Principal, run_id: str) -> dict:
        """What actually happened to this run, in order.

        `GET /v1/runs/{id}/events` reads the store's operational events, which carry service
        actions like an approval being requested -- and are empty for a run the executor
        drove, because the run's own history lives in the append-only log instead. That log
        is the record of every stage change, who caused it and when, and nothing exposed it.
        Read-only, org-scoped exactly as `run_output` is, and capped: a long run is
        truncated from the front, because the recent end is the operational one.
        """
        from runtime import company_runtime as core
        self._run_state(principal, run_id)
        events = core.read_events(run_id)
        trimmed = events[-self.MAX_HISTORY_EVENTS:]
        entries = []
        previous: dict[str, str] = {}
        for event in trimmed:
            statuses = {step_id: step["status"]
                        for step_id, step in event.get("steps", {}).items()}
            changed = sorted(step_id for step_id, status in statuses.items()
                             if previous.get(step_id) != status)
            previous = statuses
            step = event.get("target", "")
            detail = event.get("steps", {}).get(step, {})
            entries.append({
                "seq": event.get("seq"), "at": event.get("ts", ""),
                "event": event.get("event", ""), "actor": event.get("actor", ""),
                "step": step if step in statuses else "",
                "run_status": event.get("run_status", ""),
                "step_status": detail.get("status", ""),
                "attempts": detail.get("attempts"),
                "review_cycles": detail.get("review_cycles"),
                "approver": detail.get("approver", ""),
                "approval_ref": detail.get("approval_ref", ""),
                "last_failure": (detail.get("last_failure") or "")[:400],
                "changed": changed,
            })
        return {"run_id": run_id, "truncated": len(events) > len(trimmed),
                "total_events": len(events), "entries": entries}

    MAX_EVIDENCE_BYTES = 64 * 1024

    def run_output(self, principal: Principal, run_id: str) -> dict:
        """What a run actually produced, step by step -- the evidence files the executor
        wrote, which until now existed only on disk."""
        state = self._run_state(principal, run_id)
        steps = []
        for step_id, step in sorted(state["steps"].items()):
            steps.append({"step": step_id, "status": step["status"], "owner": step["owner"],
                          "action": step["action"], "risk": step["risk"],
                          "attempts": step["attempts"], "review_cycles": step["review_cycles"],
                          # The half that was missing. A step that produced nothing showed an
                          # empty box; the reason it produced nothing was already sitting in
                          # the run state and had no way out of it.
                          "max_attempts": step.get("max_attempts"),
                          "max_review_cycles": step.get("max_review_cycles", 0),
                          "checker": step.get("checker") or "",
                          "depends_on": step.get("depends_on", []),
                          "approver": step.get("approver", ""),
                          "approval_ref": step.get("approval_ref", ""),
                          "last_failure": (step.get("last_failure") or "")[:2000],
                          "output": self._evidence_text(step.get("evidence"))})
        return {"run_id": run_id, "goal": state.get("goal", ""),
                "status": state["run_status"], "steps": steps}

    @classmethod
    def _evidence_text(cls, reference: str | None) -> str:
        """Read one evidence file, or say why not.

        The path comes out of the run log, which agents write, so it is never trusted as a
        path: it must resolve inside the runs directory. Without that check an evidence
        reference would be a file-read primitive pointed at anything this process can open.

        `company_runtime.evidence_path` stores the reference relative to the repository root,
        so that is what it is anchored on; an absolute one is accepted rather than mangled,
        because the containment test below is what decides either way.
        """
        if not reference:
            return ""
        from runtime import company_runtime as core
        try:
            candidate = Path(reference)
            resolved = (candidate if candidate.is_absolute() else core.ROOT / candidate).resolve()
            resolved.relative_to(core.RUNS.resolve())
        except (OSError, ValueError):
            return "[evidence reference points outside the runs directory]"
        try:
            data = resolved.read_bytes()[:cls.MAX_EVIDENCE_BYTES]
        except OSError:
            return "[evidence file is missing]"
        text = data.decode("utf-8", errors="replace")
        return text + ("\n[truncated]" if resolved.stat().st_size > cls.MAX_EVIDENCE_BYTES else "")
