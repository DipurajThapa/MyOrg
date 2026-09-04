from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"
MAKER_CHECKER = ROOT / "runtime" / "workflows" / "maker-checker-gold-run.json"


class ExecutorTest(unittest.TestCase):
    """Drives real runs with the stub backend -- no model is ever called."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("MYORG_RUNS_DIR")
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        self.addCleanup(self._restore)

        # company_runtime binds RUNS at import time, so reload under the new env var.
        import importlib
        from runtime import company_runtime, executor
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.addCleanup(lambda: importlib.reload(company_runtime))
        self.addCleanup(lambda: importlib.reload(executor))

        self.logs: list[str] = []
        self.backend = self.executor.StubBackend()
        # Evidence must live in the repo, so sweep whatever this test writes there.
        self.addCleanup(self.clear_evidence)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("auto-*.evidence"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("MYORG_RUNS_DIR", None)
        else:
            os.environ["MYORG_RUNS_DIR"] = self._previous

    def create_run(self, run_id: str, workflow: Path = WORKFLOW) -> None:
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(workflow), run_id=run_id,
            actor="chief-of-staff", request_id=f"create-{run_id}"))

    def drive(self, run_id: str) -> dict:
        return self.executor.advance(run_id, self.backend, log=self.logs.append)

    # --- the behaviour that defines autonomy -------------------------------------

    def test_green_steps_run_with_no_human_and_stop_at_the_gated_step(self):
        self.create_run("auto-one")
        state = self.drive("auto-one")

        steps = state["steps"]
        self.assertEqual(steps["frame-goal"]["status"], "completed")
        self.assertEqual(steps["produce-output"]["status"], "completed")
        self.assertEqual(steps["validate-output"]["status"], "completed")
        # `publish` is yellow: the driver must hand it back, not approve it.
        self.assertEqual(steps["release-output"]["status"], "awaiting_approval")
        self.assertEqual(state["run_status"], "active")

    def test_completed_steps_carry_the_agents_output_as_hashed_evidence(self):
        self.create_run("auto-two")
        state = self.drive("auto-two")

        step = state["steps"]["frame-goal"]
        evidence = ROOT / step["evidence"]
        self.assertTrue(evidence.is_file())
        self.assertIn("chief-of-staff", evidence.read_text(encoding="utf-8"))
        self.assertEqual(step["evidence_sha256"],
                         self.core.evidence_path(step["evidence"])[1])
        self.assertEqual(step["submissions"][-1]["maker"], "chief-of-staff")

    def test_each_step_is_owned_by_the_department_that_owns_it(self):
        self.create_run("auto-three")
        state = self.drive("auto-three")
        self.assertEqual(state["steps"]["produce-output"]["submissions"][-1]["maker"],
                         "cto-engineering")

    def test_a_red_step_blocks_the_run_for_a_human(self):
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        for step in workflow["steps"]:
            if step["action"] == "publish":
                step["action"] = "permanent_delete"
        red = Path(self._tmp.name) / "red.json"
        red.write_text(json.dumps(workflow), encoding="utf-8")

        self.create_run("auto-red", red)
        state = self.drive("auto-red")
        self.assertEqual(state["steps"]["release-output"]["status"], "blocked_human")
        self.assertEqual(state["run_status"], "blocked_human")

    def test_a_failing_agent_is_retried_then_gives_up_within_its_budget(self):
        class Failing:
            def __call__(self, request):
                raise self_executor.ExecutorError("backend is down")

        self_executor = self.executor
        self.create_run("auto-fail")
        state = self.executor.advance("auto-fail", Failing(), log=self.logs.append)

        step = state["steps"]["frame-goal"]
        self.assertEqual(step["attempts"], step["max_attempts"])
        self.assertEqual(state["run_status"], "blocked_retry_limit")

    def test_a_busy_server_does_not_spend_a_steps_retry_budget(self):
        """`request_step` spends an attempt *before* the model is called -- right when the
        call happens, wrong when it never does. A real 529 returned zero tokens twice and a
        step burned its whole budget and blocked the run without a word being generated."""
        class Busy:
            def __call__(self, request):
                raise self_executor.ExecutorError(
                    "claude exited 1: result=API Error: 529 Overloaded")

        self_executor = self.executor
        self.create_run("auto-busy")
        # The step goes back to `ready` every time, so the run never settles -- the driver
        # gives up on the pass rather than on the work. `sweep` records that and moves on.
        with self.assertRaises(self.executor.ExecutorError):
            self.executor.advance("auto-busy", Busy(), max_iterations=4, log=self.logs.append)

        state = self.executor.current_state("auto-busy")
        step = state["steps"]["frame-goal"]
        self.assertEqual(step["status"], "ready", "it must be waiting to try again")
        self.assertEqual(step["attempts"], 0, "an attempt nobody made is not spent")
        self.assertEqual(state["run_status"], "active", "a busy server never blocks a run")
        self.assertFalse(self.core.claim_is_live(step), "the claim is handed back")
        # Every dispatch that failed must have released. Counting them is the only thing
        # that catches a release being swallowed as a replay -- which it was: the request id
        # was built from the attempt number, and release puts that number back, so the
        # second release rebuilt the first one's name and did nothing at all.
        self.assertEqual(step["releases"], 4,
                         "each busy dispatch releases; a swallowed release is a live loop")

    def test_a_release_gives_every_later_mutation_a_new_name(self):
        """The regression, isolated. A release puts the attempt number back, so an id keyed
        on attempts alone rebuilds the *previous* claim's name -- and `mutate` swallows it
        as a replay, leaving the step `ready` and re-dispatched forever. Every verb keyed on
        that counter is affected, not just release, so the fix belongs in the shared id."""
        self.create_run("auto-ids")
        claim_before = self.executor.request_id("auto-ids", "frame-goal", "claim")
        self.executor.claim("auto-ids", "frame-goal", "chief-of-staff")
        release_name = self.executor.request_id("auto-ids", "frame-goal", "release")
        self.executor.release_step("auto-ids", "frame-goal", "chief-of-staff", "busy")

        claim_after = self.executor.request_id("auto-ids", "frame-goal", "claim")
        self.assertNotEqual(claim_before, claim_after,
                            "the claim after a release must not reuse the one before it")
        self.executor.claim("auto-ids", "frame-goal", "chief-of-staff")
        self.assertEqual(self.executor.current_state("auto-ids")["steps"]["frame-goal"]["status"],
                         "in_progress", "the second claim must actually take the step")
        self.assertNotEqual(release_name,
                            self.executor.request_id("auto-ids", "frame-goal", "release"))

    def test_a_real_failure_still_spends_the_budget_and_blocks(self):
        """The other half: a genuine failure is what the retry budget is for."""
        class Broken:
            def __call__(self, request):
                raise self_executor.ExecutorError("the agent produced nothing usable")

        self_executor = self.executor
        self.create_run("auto-broken")
        state = self.executor.advance("auto-broken", Broken(), log=self.logs.append)
        step = state["steps"]["frame-goal"]
        self.assertEqual(step["attempts"], step["max_attempts"])
        self.assertEqual(state["run_status"], "blocked_retry_limit")

    def test_waiting_for_a_person_never_costs_the_step_an_attempt(self):
        """A human decision is not a retry. `approve` used to spend one, so a step that had
        already reached its cap resumed at 3 of 2 -- the operator's own budget was charged
        for the time it took them to answer."""
        self.create_run("auto-approve")
        self.executor.quietly(self.core.request_step, self.executor.namespace(
            run_id="auto-approve", step="frame-goal", actor="chief-of-staff",
            holder="driver", request_id="claim-appr"))
        before = self.executor.current_state("auto-approve")["steps"]["frame-goal"]["attempts"]
        self.executor.quietly(self.core.hold, self.executor.namespace(
            run_id="auto-approve", step="frame-goal", actor="chief-of-staff",
            evidence=self.core.RUNS.__class__(__file__).as_posix(), reason="grader unavailable",
            claim_token=None, spend=0.0, request_id="hold-appr"))
        self.executor.quietly(self.core.approve, self.executor.namespace(
            run_id="auto-approve", step="frame-goal", approver="Owner", actor_id="owner",
            approval_ref="ticket-1", request_id="appr-1"))
        step = self.executor.current_state("auto-approve")["steps"]["frame-goal"]
        self.assertEqual(step["status"], "in_progress")
        self.assertEqual(step["attempts"], before, "approval must not spend an attempt")

    def test_a_step_at_its_cap_is_not_dispatched_again_while_the_run_lives(self):
        """The live failure: a step reached its cap without ever going through `fail`, so
        the run stayed active and `request_step` -- the only door the approval and
        checker-return paths use -- let it run twice more, at 3 and then 4 of a maximum 2."""
        self.create_run("auto-cap-all")
        limit = self.executor.current_state("auto-cap-all")["steps"]["frame-goal"]["max_attempts"]
        for n in range(limit):
            self.executor.quietly(self.core.request_step, self.executor.namespace(
                run_id="auto-cap-all", step="frame-goal", actor="chief-of-staff",
                holder="driver", request_id=f"claim-{n}"))
            if n < limit - 1:  # leave the last attempt in progress, so the run stays active
                self.executor.quietly(self.core.fail, self.executor.namespace(
                    run_id="auto-cap-all", step="frame-goal", actor="chief-of-staff",
                    reason="no good", claim_token=None, spend=0.0, request_id=f"fail-{n}"))

        # A human decides the ungraded work is fine. That must not spend an attempt, and it
        # leaves the step at its cap with the run still very much alive.
        self.executor.quietly(self.core.hold, self.executor.namespace(
            run_id="auto-cap-all", step="frame-goal", actor="chief-of-staff",
            evidence=__file__, reason="grader unavailable", claim_token=None,
            spend=0.0, request_id="hold-cap"))
        self.executor.quietly(self.core.approve, self.executor.namespace(
            run_id="auto-cap-all", step="frame-goal", approver="Owner", actor_id="owner",
            approval_ref="ticket-2", request_id="appr-cap"))
        state = self.executor.current_state("auto-cap-all")
        self.assertEqual(state["run_status"], "active")
        self.assertEqual(state["steps"]["frame-goal"]["attempts"], limit)

        # Now the door refuses, which is what it never did.
        self.executor.quietly(self.core.fail, self.executor.namespace(
            run_id="auto-cap-all", step="frame-goal", actor="chief-of-staff",
            reason="still no good", claim_token=None, spend=0.0, request_id="fail-last"))
        after = self.executor.current_state("auto-cap-all")["steps"]["frame-goal"]
        self.assertEqual(after["attempts"], limit, "the cap holds")
        self.assertEqual(after["status"], "blocked_retry_limit")

    def test_the_driver_never_loops_forever(self):
        # The 4-step chain needs several passes; one pass must not silently return
        # a half-finished run, it must raise.
        self.create_run("auto-cap")
        with self.assertRaises(self.executor.ExecutorError):
            self.executor.advance("auto-cap", self.backend, max_iterations=1,
                                  log=self.logs.append)
        state = self.executor.current_state("auto-cap")
        self.assertEqual(state["steps"]["frame-goal"]["status"], "completed")
        self.assertEqual(state["steps"]["produce-output"]["status"], "ready")

    def test_a_step_owned_by_nobody_never_reaches_the_executor(self):
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        workflow["steps"][0]["owner"] = "no-such-department"
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text(json.dumps(workflow), encoding="utf-8")
        # The runtime rejects unknown owners at creation, so the driver is never
        # handed a step it has no agent for.
        with self.assertRaises(SystemExit):
            self.create_run("auto-owner", bad)

    def test_a_missing_agent_definition_is_reported_not_silently_skipped(self):
        with self.assertRaises(self.executor.ExecutorError):
            self.executor.agent_brief("no-such-department")

    # --- handoffs: what one step passes to the next ------------------------------

    def recording_backend(self):
        """Stub that keeps every request it was given, so we can inspect the prompts."""
        seen: list = []

        class Recorder(self.executor.StubBackend):
            def __call__(self, request):
                seen.append(request)
                return super().__call__(request)

        return Recorder(), seen

    def test_a_step_receives_the_work_of_the_step_it_depends_on(self):
        backend, seen = self.recording_backend()
        self.create_run("auto-handoff")
        self.executor.advance("auto-handoff", backend, log=self.logs.append)

        by_step = {request.step_id: request for request in seen}
        downstream = by_step["produce-output"]
        self.assertEqual([h.step_id for h in downstream.handoffs], ["frame-goal"])
        self.assertEqual(downstream.handoffs[0].owner, "chief-of-staff")
        # The upstream deliverable itself, verbatim, is in the prompt.
        self.assertIn("frame-goal", downstream.prompt())
        self.assertIn(downstream.handoffs[0].text.strip(), downstream.prompt())

    def test_the_first_step_is_told_it_has_no_upstream_work(self):
        backend, seen = self.recording_backend()
        self.create_run("auto-first")
        self.executor.advance("auto-first", backend, log=self.logs.append)

        first = next(r for r in seen if r.step_id == "frame-goal")
        self.assertEqual(first.handoffs, ())
        self.assertIn("No upstream work", first.prompt())

    def test_only_direct_dependencies_are_handed_over(self):
        backend, seen = self.recording_backend()
        self.create_run("auto-adjacent")
        self.executor.advance("auto-adjacent", backend, log=self.logs.append)

        validate = next(r for r in seen if r.step_id == "validate-output")
        # validate-output depends on produce-output only, not on frame-goal.
        self.assertEqual([h.step_id for h in validate.handoffs], ["produce-output"])

    def test_tampered_upstream_evidence_is_refused(self):
        self.create_run("auto-tamper")
        # Let the first step finish, then rewrite its artifact behind the runtime's back.
        with self.assertRaises(self.executor.ExecutorError):
            self.executor.advance("auto-tamper", self.backend, max_iterations=1,
                                  log=self.logs.append)
        state = self.executor.current_state("auto-tamper")
        evidence = ROOT / state["steps"]["frame-goal"]["evidence"]
        evidence.write_text("forged\n", encoding="utf-8")

        self.executor.advance("auto-tamper", self.backend, log=self.logs.append)
        state = self.executor.current_state("auto-tamper")
        self.assertNotEqual(state["steps"]["produce-output"]["status"], "completed")
        self.assertTrue(any("changed after it was recorded" in line for line in self.logs))

    def test_a_huge_upstream_artifact_is_clipped(self):
        clipped = self.executor.clip("x" * (self.executor.MAX_HANDOFF_CHARS + 500))
        self.assertLess(len(clipped), self.executor.MAX_HANDOFF_CHARS + 100)
        self.assertIn("truncated", clipped.lower())
        self.assertEqual(self.executor.clip("short"), "short")

    # --- maker-checker: independent review ---------------------------------------

    def verdict_backend(self, text: str):
        """Backend whose checker always answers with `text`; work steps stay stubbed."""
        stub = self.executor.StubBackend()

        def backend(request):
            return text if request.kind == "check" else stub(request)

        return backend

    def test_a_checked_step_is_reviewed_by_its_checker_then_completes(self):
        self.create_run("auto-mc", MAKER_CHECKER)
        state = self.drive("auto-mc")

        step = state["steps"]["produce-output"]
        self.assertEqual(step["status"], "completed")
        self.assertEqual(step["checked_by"], "coo-operations")
        self.assertNotEqual(step["checked_by"], step["owner"])
        # The verdict was filed as a typed message on the maker-checker edge.
        message = state["messages"][-1]
        self.assertEqual((message["from"], message["to"], message["kind"]),
                         ("coo-operations", "cto-engineering", "decision"))

    def test_a_returned_step_goes_back_to_the_maker_and_is_redone(self):
        returns = {"count": 0}
        stub = self.executor.StubBackend()

        def backend(request):
            if request.kind != "check":
                return stub(request)
            returns["count"] += 1
            # Approve on the second look, so the run can finish.
            verdict = "RETURN" if returns["count"] == 1 else "APPROVE"
            return f"VERDICT: {verdict}\nneeds more detail\n"

        self.create_run("auto-return", MAKER_CHECKER)
        state = self.executor.advance("auto-return", backend, log=self.logs.append)

        step = state["steps"]["produce-output"]
        self.assertEqual(step["review_cycles"], 1)
        self.assertEqual(step["attempts"], 2)
        self.assertEqual(step["status"], "completed")

    def test_repeated_returns_stop_at_the_review_limit(self):
        self.create_run("auto-loop", MAKER_CHECKER)
        state = self.executor.advance(
            "auto-loop", self.verdict_backend("VERDICT: RETURN\nstill wrong\n"),
            log=self.logs.append)

        step = state["steps"]["produce-output"]
        self.assertEqual(step["review_cycles"], step["max_review_cycles"])
        self.assertEqual(state["run_status"], "blocked_review_limit")

    def test_a_rejected_step_ends_the_run(self):
        self.create_run("auto-reject", MAKER_CHECKER)
        state = self.executor.advance(
            "auto-reject", self.verdict_backend("VERDICT: REJECT\nunsalvageable\n"),
            log=self.logs.append)

        self.assertEqual(state["steps"]["produce-output"]["status"], "rejected_by_checker")
        self.assertEqual(state["run_status"], "rejected_by_checker")

    def test_an_unreadable_verdict_is_never_an_approval(self):
        self.assertEqual(self.executor.parse_verdict("looks fine to me"), "RETURN")
        self.assertEqual(self.executor.parse_verdict("VERDICT: approve\nok"), "APPROVE")
        self.assertEqual(self.executor.parse_verdict("VERDICT: REJECT"), "REJECT")

        self.create_run("auto-mush", MAKER_CHECKER)
        state = self.executor.advance("auto-mush", self.verdict_backend("no idea\n"),
                                      log=self.logs.append)
        self.assertNotEqual(state["steps"]["produce-output"]["status"], "completed")
        self.assertTrue(any("no readable verdict" in line for line in self.logs))

    def test_the_checker_sees_the_makers_actual_submission(self):
        seen = {}
        stub = self.executor.StubBackend()

        def backend(request):
            if request.kind == "check":
                seen["submission"] = request.submission
                seen["maker"] = request.maker
            return stub(request)

        self.create_run("auto-sees", MAKER_CHECKER)
        self.executor.advance("auto-sees", backend, log=self.logs.append)
        self.assertEqual(seen["maker"], "cto-engineering")
        self.assertIn("produce-output", seen["submission"])

    # --- the feedback loop: rework must be informed ------------------------------

    def test_the_maker_is_shown_why_its_work_was_returned(self):
        seen: list = []
        stub = self.executor.StubBackend()
        rounds = {"n": 0}

        def backend(request):
            if request.kind == "check":
                rounds["n"] += 1
                if rounds["n"] == 1:
                    return "VERDICT: RETURN\nthe cost table is missing\n"
                return "VERDICT: APPROVE\nfixed\n"
            seen.append(request)
            return stub(request)

        self.create_run("auto-feedback", MAKER_CHECKER)
        self.executor.advance("auto-feedback", backend, log=self.logs.append)

        attempts = [r for r in seen if r.step_id == "produce-output"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0].feedback, "")
        self.assertIn("the cost table is missing", attempts[1].feedback)
        self.assertIn("do not repeat the same mistake", attempts[1].prompt())

    def test_each_review_cycle_keeps_its_own_artifact(self):
        self.create_run("auto-rounds", MAKER_CHECKER)
        state = self.executor.advance(
            "auto-rounds", self.verdict_backend("VERDICT: RETURN\nnope\n"),
            log=self.logs.append)

        payloads = [m["payload"] for m in state["messages"]]
        self.assertEqual(len(payloads), len(set(payloads)),
                         "each review must have its own file or earlier hashes break")
        # Every message still verifies against the file it pinned.
        for message in state["messages"]:
            self.assertEqual(self.core.evidence_path(message["payload"])[1],
                             message["payload_sha256"])

    def test_a_checker_sees_the_whole_submission_not_a_clipped_one(self):
        self.assertGreater(self.executor.MAX_SUBMISSION_CHARS,
                           self.executor.MAX_HANDOFF_CHARS)
        long_text = "y" * (self.executor.MAX_HANDOFF_CHARS + 100)
        # A real deliverable longer than the handoff budget must survive intact.
        self.assertEqual(
            self.executor.clip(long_text, self.executor.MAX_SUBMISSION_CHARS), long_text)

    # --- quality gate: a non-answer must not complete a step ---------------------

    def test_a_refusal_or_question_is_not_accepted_as_a_deliverable(self):
        gate = self.executor.structural_failure
        self.assertIsNotNone(gate("Could you clarify what you want here? " * 8))
        self.assertIsNotNone(gate("I need more information about the scope. " * 8))
        self.assertIsNotNone(gate("too short"))
        self.assertIsNone(gate("A real deliverable. " * 40))

    def test_a_step_that_returns_a_question_is_retried_not_completed(self):
        stub = self.executor.StubBackend()

        def backend(request):
            if request.kind == "work" and request.step_id == "frame-goal":
                return "Could you clarify what outcome you want? " * 10
            return stub(request)

        self.create_run("auto-gate")
        state = self.executor.advance("auto-gate", backend, log=self.logs.append)

        step = state["steps"]["frame-goal"]
        self.assertEqual(step["attempts"], step["max_attempts"])
        self.assertEqual(state["run_status"], "blocked_retry_limit")
        self.assertIn("asks for clarification", step["last_failure"])

    def test_the_rejection_reason_is_shown_on_the_retry(self):
        seen: list = []
        stub = self.executor.StubBackend()
        calls = {"n": 0}

        def backend(request):
            if request.kind == "work" and request.step_id == "frame-goal":
                seen.append(request)
                calls["n"] += 1
                if calls["n"] == 1:
                    return "I am unable to do this without more detail. " * 8
            return stub(request)

        self.create_run("auto-why")
        self.executor.advance("auto-why", backend, log=self.logs.append)
        self.assertGreaterEqual(len(seen), 2)
        self.assertIn("refuses instead of delivering", seen[1].feedback)

    def test_acceptance_criteria_are_graded_when_a_step_declares_them(self):
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        workflow["steps"][0]["acceptance"] = ["names a measurable outcome",
                                              "names who is accountable"]
        graded = Path(self._tmp.name) / "graded.json"
        graded.write_text(json.dumps(workflow), encoding="utf-8")

        seen: list = []
        stub = self.executor.StubBackend()

        def backend(request):
            if request.kind == "grade":
                seen.append(request)
            return stub(request)

        self.create_run("auto-accept", graded)
        self.executor.advance("auto-accept", backend, log=self.logs.append)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].step_id, "frame-goal")
        self.assertEqual(seen[0].criteria,
                         ("names a measurable outcome", "names who is accountable"))
        self.assertIn("names who is accountable", seen[0].prompt())

    def test_work_that_fails_its_acceptance_criteria_does_not_complete(self):
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        workflow["steps"][0]["acceptance"] = ["names a measurable outcome"]
        graded = Path(self._tmp.name) / "strict.json"
        graded.write_text(json.dumps(workflow), encoding="utf-8")

        stub = self.executor.StubBackend()

        def backend(request):
            if request.kind == "grade":
                return "VERDICT: FAILS\nno measurable outcome anywhere\n"
            return stub(request)

        self.create_run("auto-strict", graded)
        state = self.executor.advance("auto-strict", backend, log=self.logs.append)

        self.assertNotEqual(state["steps"]["frame-goal"]["status"], "completed")
        self.assertIn("no measurable outcome",
                      state["steps"]["frame-goal"]["last_failure"])

    def test_steps_without_criteria_are_not_graded(self):
        seen: list = []
        stub = self.executor.StubBackend()

        def backend(request):
            if request.kind == "grade":
                seen.append(request)
            return stub(request)

        self.create_run("auto-ungraded")
        self.executor.advance("auto-ungraded", backend, log=self.logs.append)
        self.assertEqual(seen, [])

    def test_agent_brief_strips_frontmatter(self):
        brief = self.executor.agent_brief("cto-engineering")
        self.assertFalse(brief.startswith("---"))
        self.assertTrue(brief)

    # --- replay safety (A-06 / WF-13) ------------------------------------------------

    def test_the_same_mutation_asked_for_twice_has_the_same_name(self) -> None:
        """WF-13: ids were a uuid per call, so WF-04's idempotent replay could never fire
        on the autonomous path -- a driver that crashed and was swept again applied the
        mutation a second time instead of being recognised as repeating the first."""
        self.create_run("exec-replay")
        first = self.executor.request_id("exec-replay", "frame-goal", "claim")
        second = self.executor.request_id("exec-replay", "frame-goal", "claim")
        self.assertEqual(first, second)

    def test_different_transitions_on_one_attempt_are_named_differently(self) -> None:
        """The collision this scheme must not cause. Seven call sites share the helper;
        without the verb, `claim` and `complete` on one attempt would look like the same
        mutation and the second would be silently swallowed as a replay."""
        self.create_run("exec-verbs")
        names = {verb: self.executor.request_id("exec-verbs", "frame-goal", verb)
                 for verb in ("claim", "take", "complete", "fail", "hold")}
        self.assertEqual(len(set(names.values())), len(names), names)

    def test_a_later_attempt_is_a_new_mutation_not_a_replay_of_the_last(self) -> None:
        """A retry must actually apply. If attempt number were left out, the second
        attempt's completion would replay the first's and the retry would vanish."""
        self.create_run("exec-attempts")
        before = self.executor.request_id("exec-attempts", "frame-goal", "complete")
        self.executor.claim("exec-attempts", "frame-goal", "chief-of-staff")
        after = self.executor.request_id("exec-attempts", "frame-goal", "complete")
        self.assertNotEqual(before, after)

    def test_an_unreadable_step_gets_a_unique_name_rather_than_a_wrong_one(self) -> None:
        """Falling back to a shared name would swallow a real mutation. Unique is the
        safe direction when the attempt cannot be read."""
        one = self.executor.request_id("no-such-run", "no-such-step", "claim")
        two = self.executor.request_id("no-such-run", "no-such-step", "claim")
        self.assertNotEqual(one, two)

    def test_an_outside_worker_does_not_share_the_drivers_naming(self) -> None:
        """Found by a failing test: with one scheme, a worker's claim on a step the driver
        had already parked was answered with the driver's earlier result -- turning a
        refusal the gated step had earned into an apparent success."""
        from runtime import agent_api
        self.create_run("exec-actors")
        driver = self.executor.request_id("exec-actors", "frame-goal", "claim")
        worker = agent_api.request_id("frame-goal")
        self.assertNotEqual(driver, worker)
        self.assertNotEqual(worker, agent_api.request_id("frame-goal"))


class ClaudeBackendFailureIsDiagnosable(unittest.TestCase):
    """A failed dispatch must say why.

    `--output-format json` sends the CLI's own errors -- a refusal, a usage limit, a bad
    flag -- to *stdout*, leaving stderr empty. Reporting stderr alone produced the message
    `claude exited 1:` with nothing after the colon, and that is exactly what an operator
    saw when a real planned run failed: told it broke, never told what broke.
    """

    def backend_error(self, returncode=1, stdout="", stderr=""):
        from unittest.mock import patch
        from runtime import backends
        completed = type("Completed", (), {"returncode": returncode, "stdout": stdout,
                                           "stderr": stderr})()
        request = type("Request", (), {"prompt": lambda self: "do a thing",
                                       "brief": "a brief"})()
        with patch.object(backends.subprocess, "run", return_value=completed):
            with self.assertRaises(backends.ExecutorError) as caught:
                backends.ClaudeCliBackend()(request)
        return str(caught.exception)

    def test_an_error_on_stdout_reaches_the_operator(self):
        message = self.backend_error(stdout='{"error":"usage limit reached"}')
        self.assertIn("usage limit reached", message)
        self.assertIn("exited 1", message)

    def test_the_diagnosis_is_picked_out_of_the_json_envelope_not_sliced_off_it(self):
        """The envelope leads with timings and token counts and ends with `result` and
        `subtype`. Truncating the front keeps the noise and drops the answer -- which is
        what a real failure did: 400 characters of zeroes and no reason."""
        import json as json_module
        envelope = json_module.dumps({
            "duration_api_ms": 0, "session_id": "x" * 200, "total_cost_usd": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0,
                      "cache_creation": {"ephemeral_1h_input_tokens": 0}},
            "stop_reason": "stop_sequence", "is_error": True,
            "subtype": "error_during_execution", "result": "Prompt is too long",
        })
        self.assertGreater(len(envelope), 400, "the diagnosis sits past a 400-char slice")
        message = self.backend_error(stdout=envelope)
        self.assertIn("Prompt is too long", message)
        self.assertIn("error_during_execution", message)
        self.assertNotIn("session_id", message, "noise must not crowd out the reason")

    def test_shutdown_noise_never_headlines_a_failure(self):
        """A session ending abruptly cancels whatever hooks the operator's plugins
        registered, and those cancellations land on stderr. Reported first, as they were,
        they buried the real cause: an operator was told a plugin's SessionEnd hook had
        failed when the answer, further down the same message, was "529 Overloaded"."""
        import json as json_module
        hook_noise = ("SessionEnd hook [node .../worker-service.cjs hook claude-code "
                      "session-complete] failed: Hook cancelled")
        message = self.backend_error(
            stdout=json_module.dumps({"result": "API Error: 529 Overloaded", "type": "result"}),
            stderr=hook_noise)
        self.assertLess(message.index("529"), message.index("Hook cancelled"),
                        "the real cause must come first")
        self.assertIn("while shutting down", message, "the noise is labelled, not deleted")

    def test_a_genuine_stderr_message_still_leads(self):
        message = self.backend_error(stderr="unknown flag --nope")
        self.assertTrue(message.split(": ", 1)[1].startswith("stderr="), message)

    def test_zero_tokens_says_the_model_was_never_reached(self):
        """The most useful thing about a zero-token failure is that it is not the model's:
        it points the reader at flags, prompt size and session state instead."""
        import json as json_module
        message = self.backend_error(stdout=json_module.dumps(
            {"usage": {"input_tokens": 0, "output_tokens": 0}, "stop_reason": "stop_sequence"}))
        self.assertIn("stopped before calling the model", message)

    def test_an_error_on_stderr_still_reaches_the_operator(self):
        self.assertIn("unknown flag", self.backend_error(stderr="unknown flag --nope"))

    def test_both_streams_are_reported_when_both_speak(self):
        message = self.backend_error(stdout="on stdout", stderr="on stderr")
        self.assertIn("on stdout", message)
        self.assertIn("on stderr", message)

    def test_silence_on_both_streams_says_so_rather_than_trailing_a_colon(self):
        message = self.backend_error()
        self.assertIn("no output on either stream", message)
        self.assertFalse(message.rstrip().endswith(":"),
                         "a message ending in a colon tells the reader nothing")


if __name__ == "__main__":
    unittest.main()
