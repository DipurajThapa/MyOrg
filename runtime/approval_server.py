#!/usr/bin/env python3
"""A local approval console: the one place a human decides what the company may do.

Binds to localhost only. Every decision needs a name and a reason, and both are written
into the run's own event chain by the runtime -- this server never edits state itself.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.approvals import Decision, decide, pending  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8787

STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#12161c;--muted:#5b6472;--line:#dfe3e8;
--yellow:#9a6b00;--yellowbg:#fff6e0;--red:#a11;--redbg:#fdecec;--ok:#0a6b3d}
@media(prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1a1f27;--ink:#e8ecf1;
--muted:#98a2b3;--line:#2b323d;--yellowbg:#2a2210;--yellow:#e3b341;--redbg:#2a1414;
--red:#f47c7c;--ok:#3fb950}}
*{box-sizing:border-box}body{margin:0;padding:2rem 1rem;background:var(--bg);
color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:60rem;margin:0 auto}h1{font-size:1.4rem;margin:0 0 .25rem}
.sub{color:var(--muted);margin:0 0 2rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:1.25rem;margin-bottom:1.25rem}
.tag{display:inline-block;font-size:.75rem;font-weight:600;padding:.15rem .5rem;
border-radius:99px;text-transform:uppercase;letter-spacing:.03em}
.yellow{background:var(--yellowbg);color:var(--yellow)}
.red{background:var(--redbg);color:var(--red)}
h2{font-size:1.05rem;margin:.6rem 0 .2rem}
dl{display:grid;grid-template-columns:auto 1fr;gap:.3rem .9rem;margin:.8rem 0}
dt{color:var(--muted);font-size:.85rem}dd{margin:0;font-size:.9rem}
details{margin:.6rem 0}summary{cursor:pointer;color:var(--muted);font-size:.85rem}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:.8rem;
overflow-x:auto;font-size:.8rem;white-space:pre-wrap;max-height:22rem}
form{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:1rem;
padding-top:1rem;border-top:1px solid var(--line)}
input{flex:1 1 12rem;min-width:9rem;padding:.5rem .6rem;border:1px solid var(--line);
border-radius:6px;background:var(--bg);color:var(--ink);font:inherit;font-size:.9rem}
button{padding:.5rem 1.1rem;border:0;border-radius:6px;font:inherit;font-weight:600;
font-size:.9rem;cursor:pointer;color:#fff}
button.yes{background:var(--ok)}button.no{background:var(--red)}
.empty{text-align:center;color:var(--muted);padding:3rem 1rem}
.who{float:right;color:var(--muted);font-size:.78rem}
.ask{font-size:1.02rem;margin:.5rem 0 .9rem}
ul.findings{margin:.2rem 0 .9rem;padding-left:1.1rem}
ul.findings li{font-size:.9rem;margin:.15rem 0}
.rec{font-weight:600;font-size:.9rem;padding:.5rem .8rem;border-radius:6px;margin:.2rem 0}
.rec.yes{background:var(--yellowbg);color:var(--ok)}
.rec.no{background:var(--redbg);color:var(--red)}
.nobrief{color:var(--muted);font-size:.9rem}
h1.second{font-size:1.2rem;margin:2.5rem 0 .25rem}
.card.mem{border-left:3px solid var(--muted)}
.mtag{background:var(--bg);color:var(--muted);border:1px solid var(--line)}
.order{float:left;margin-right:.6rem;color:var(--muted);font-size:.75rem;font-weight:600}
.impact{color:var(--muted);font-size:.82rem;margin:.1rem 0 .6rem}
h3{font-size:.85rem;color:var(--muted);margin:.9rem 0 .3rem}
.flash{background:var(--card);border-left:3px solid var(--ok);padding:.8rem 1rem;
border-radius:6px;margin-bottom:1.5rem;font-size:.9rem}
.handback{background:var(--redbg);color:var(--red);padding:.7rem .9rem;
border-radius:6px;font-size:.85rem;margin-top:1rem}
"""


def context_block(decision: Decision) -> str:
    if not decision.context:
        return ""
    parts = ['<details><summary>Read the full work behind this &mdash; '
             'only if something above looks wrong</summary>']
    for label, text in decision.context:
        parts.append(f"<h3>{html.escape(label)}</h3><pre>{html.escape(text)}</pre>")
    parts.append("</details>")
    return "".join(parts)


def brief_block(decision: Decision) -> str:
    """The decision itself. Five lines, no scrolling, no reading of source material."""
    brief = decision.brief
    if brief is None:
        return ('<p class="nobrief">No brief was written for this one. Open the full '
                "work below before deciding.</p>")
    findings = "".join(f"<li>{html.escape(item)}</li>" for item in brief.findings)
    verdict = "yes" if brief.recommends_approval else "no"
    return (
        f'<p class="ask">{html.escape(brief.ask)}</p>'
        f'<dl><dt>If you say yes</dt><dd>{html.escape(brief.if_yes)}</dd>'
        f'<dt>Watch out for</dt><dd>{html.escape(brief.watch)}</dd></dl>'
        + (f"<ul class=findings>{findings}</ul>" if findings else "")
        + f'<p class="rec {verdict}">Suggested: {html.escape(brief.recommend)}</p>')


def card(decision: Decision, position: int = 0, total: int = 0) -> str:
    tag = "red" if decision.risk == "red" else "yellow"
    order = f'<span class="order">{position} of {total}</span>' if total > 1 else ""
    body = [
        f'<div class="card">{order}'
        f'<span class="tag {tag}">{html.escape(decision.risk)}</span>',
        f'<span class="who">{html.escape(decision.owner)} &middot; '
        f"{html.escape(decision.run_id)}</span>",
        f"<h2>{html.escape(decision.action)} &mdash; "
        f"{html.escape(decision.step_id)}</h2>",
        f'<p class="impact">{html.escape(decision.reason)}</p>',
        f'<p class="impact">{html.escape(decision.impact)}</p>',
        brief_block(decision),
        context_block(decision),
    ]
    if decision.actionable and not local_step_decisions():
        body.append('<p class="handback">Decide this in the Control Center, where the '
                    "decision is bound to your identity and role. This console shows the "
                    "queue; it no longer takes step decisions (B-09).</p>")
    elif decision.actionable:
        body.append(
            f'<form method="post" action="/decide">'
            f'<input type="hidden" name="run_id" value="{html.escape(decision.run_id)}">'
            f'<input type="hidden" name="step" value="{html.escape(decision.step_id)}">'
            f'<input name="approver" placeholder="Your name" required>'
            f'<input name="note" placeholder="Reason (recorded)" required>'
            f'<button class="yes" name="verdict" value="approve">Approve</button>'
            f'<button class="no" name="verdict" value="reject">Reject</button>'
            f"</form>")
    else:
        body.append('<p class="handback">Handed back to you. Do this yourself, outside '
                    "the system &mdash; there is nothing here to approve.</p>")
    body.append("</div>")
    return "".join(body)


def memory_card(entry) -> str:
    """A lesson an agent wants every future agent to be told. Cheap to approve, and
    hard to undo once it starts shaping prompts -- so it gets its own yes."""
    return (
        f'<div class="card mem"><span class="tag mtag">remember?</span>'
        f'<span class="who">{html.escape(entry.author)}</span>'
        f"<h2>{html.escape(entry.subject)}</h2>"
        f'<p class="ask">{html.escape(entry.body)}</p>'
        f'<p class="impact">If you agree, every future agent doing this work is told '
        f'this. Approving is easy to undo &mdash; you can retire it later.</p>'
        f'<form method="post" action="/remember">'
        f'<input type="hidden" name="entry_id" value="{html.escape(entry.id)}">'
        f'<input name="approver" placeholder="Your name" required>'
        f'<button class="yes" name="verdict" value="approve">Remember this</button>'
        f'<button class="no" name="verdict" value="reject">Discard</button>'
        f"</form></div>")


def memory_section() -> str:
    try:
        from runtime.memory import proposals
        waiting = proposals()
    except SystemExit:
        return ""
    if not waiting:
        return ""
    cards = "".join(memory_card(entry) for entry in waiting)
    return (f"<h1 class=second>Things to remember</h1>"
            f"<p class=sub>{len(waiting)} lesson(s) your agents want kept.</p>{cards}")


def apply_memory(form: dict[str, str]) -> str:
    from runtime.memory import LIVE, decide as decide_memory
    status = LIVE if form.get("verdict") == "approve" else "rejected"
    try:
        entry = decide_memory(form.get("entry_id", ""), status,
                              form.get("approver", ""))
    except SystemExit as error:
        return f"Not recorded: {error}"
    kept = "Will be remembered" if entry.live else "Discarded"
    return f"{kept}: {entry.subject}"


def page(decisions: list[Decision], flash: str = "") -> str:
    if decisions:
        cards = "".join(card(d, n, len(decisions)) for n, d in enumerate(decisions, 1))
        sub = (f"{len(decisions)} waiting on you, in the order they should be decided."
               if len(decisions) > 1 else "1 waiting on you.")
    else:
        cards = '<p class="empty">Nothing is waiting on you.</p>'
        sub = "The company is running itself."
    banner = f'<p class="flash">{html.escape(flash)}</p>' if flash else ""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            f"<title>Approvals</title><style>{STYLE}</style></head><body><main>"
            f"<h1>Approvals</h1><p class=sub>{html.escape(sub)}</p>"
            f"{banner}{cards}{memory_section()}</main></body></html>")


class Handler(BaseHTTPRequestHandler):
    server_version = "MyOrgApprovals/1.0"

    def log_message(self, *_args) -> None:  # keep the console readable
        pass

    def _send(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/":
            self._send("<h1>Not found</h1>", 404)
            return
        self._send(page(pending()))

    def do_POST(self) -> None:
        if self.path == "/decide":
            flash = apply_decision(read_form(self))
        elif self.path == "/remember":
            flash = apply_memory(read_form(self))
        else:
            self._send("<h1>Not found</h1>", 404)
            return
        self._send(page(pending(), flash))


def read_form(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length") or 0)
    parsed = parse_qs(handler.rfile.read(length).decode("utf-8"))
    return {key: values[0] for key, values in parsed.items()}


LOCAL_STEP_DECISIONS_ENV = "MYORG_LOCAL_STEP_DECISIONS"


def local_step_decisions() -> bool:
    """Whether this console may still approve or reject steps. Off by default since B-09:
    the Control Center is the canonical approval surface -- it binds a decision to a
    registered human, a role, an organization and a required reason, and this loopback
    page can do none of that. The switch is a deprecated compatibility path for a
    machine with no Control Center; it is removed in 0.6.0. Memory decisions stay here
    until the API has a route for them."""
    return os.environ.get(LOCAL_STEP_DECISIONS_ENV, "").strip() == "1"


def apply_decision(form: dict[str, str]) -> str:
    """Hand the decision to the runtime and report back in plain words."""
    if not local_step_decisions():
        return ("Not recorded: step decisions are made in the Control Center. "
                f"Set {LOCAL_STEP_DECISIONS_ENV}=1 to allow them here (deprecated, 0.6.0).")
    approve = form.get("verdict") == "approve"
    try:
        decide(form.get("run_id", ""), form.get("step", ""), approve,
               form.get("approver", ""), form.get("note", ""))
    except SystemExit as error:
        return f"Not recorded: {error}"
    verb = "Approved" if approve else "Rejected"
    return (f"{verb} {form.get('step', '')} in {form.get('run_id', '')} "
            f"as {form.get('approver', '')}.")


def serve(port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"Approvals console: http://{HOST}:{server.server_address[1]}")
    print("Only this machine can reach it. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve(parser.parse_args(argv).port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
