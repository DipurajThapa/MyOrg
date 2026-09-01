#!/usr/bin/env python3
"""A-10: is a long-lived driver cheaper than repeated cold starts?

`measure_dispatch_cost.py` found a cold dispatch costs 3.4x a warm one. That was measured
*within* one script run. The question this answers is different and load-bearing: does the
advantage survive **across runs**, the way the supervised scheduler works -- one process
driving many runs -- or does each `claude -p` invocation start cold regardless?

The answer decides whether `--supervised` is only an availability feature or also the
largest cost optimisation available.

Method: the same prompt, N times in a row, recording cost per call. If cost falls after the
first call and stays down, warmth persists across invocations and A-10 is real. If every
call costs the same as a cold one, A-10 is a mirage and the 3.4x was an artifact of
something else.

    python scripts/measure_cache_warmth.py --calls 6 [--gap-seconds 0]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ("Reply with exactly one line: the number of days in February 2026. "
          "No explanation.")


def one_call() -> dict:
    """One dispatch-shaped invocation. Same flags the runtime uses, plus JSON output."""
    result = subprocess.run(
        ["claude", "-p", PROMPT, "--output-format", "json",
         "--permission-mode", "dontAsk", "--tools", "", "--allowedTools", ""],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT), check=False)
    if result.returncode != 0:
        return {"error": f"exit {result.returncode}: {result.stderr.strip()[:200]}"}
    try:
        answer = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"unparseable: {result.stdout[:200]}"}
    usage = answer["usage"]
    return {"cost": answer["total_cost_usd"],
            "cache_create": usage["cache_creation_input_tokens"],
            "cache_read": usage["cache_read_input_tokens"],
            "out": usage["output_tokens"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=6)
    parser.add_argument("--gap-seconds", type=float, default=0.0,
                        help="pause between calls, to probe how long warmth lasts")
    args = parser.parse_args(argv)

    rows = []
    for number in range(1, args.calls + 1):
        if number > 1 and args.gap_seconds:
            time.sleep(args.gap_seconds)
        row = one_call()
        row["call"] = number
        rows.append(row)
        if "error" in row:
            print(f"call {number}: FAILED -- {row['error']}", flush=True)
            continue
        print(f"call {number}: ${row['cost']:.4f} "
              f"cache_create={row['cache_create']:>7} cache_read={row['cache_read']:>7}",
              flush=True)

    good = [r for r in rows if "error" not in r]
    if len(good) < 2:
        print("\nnot enough successful calls to judge")
        return 1

    first, rest = good[0], good[1:]
    average_rest = sum(r["cost"] for r in rest) / len(rest)
    print("\n--- A-10 ----------------------------------------------------------")
    print(f"first call      ${first['cost']:.4f}  (cache_create {first['cache_create']})")
    print(f"calls 2..{len(good)} mean  ${average_rest:.4f}  "
          f"(cache_create {sum(r['cache_create'] for r in rest) // len(rest)})")
    if average_rest > 0:
        print(f"ratio           {first['cost'] / average_rest:.2f}x")
    spread = max(r["cost"] for r in rest) - min(r["cost"] for r in rest)
    print(f"spread in 2..N  ${spread:.4f}   (small means warmth is stable, not luck)")
    print()
    # A cold call is identifiable on its own terms -- cache_read == 0 -- so say that rather
    # than inferring it from "first call vs the rest". That inference is only valid with no
    # gap; with one, call 1 may itself be warm from an earlier run, and the comparison
    # silently reverses. An earlier version of this script did exactly that and printed a
    # confident wrong verdict.
    cold = [r for r in good if r["cache_read"] == 0]
    warm = [r for r in good if r["cache_read"] > 0]
    print(f"cold calls (cache_read=0): {[r['call'] for r in cold] or 'none'}")
    print(f"warm calls:                {[r['call'] for r in warm] or 'none'}")
    if cold and warm:
        cold_mean = sum(r["cost"] for r in cold) / len(cold)
        warm_mean = sum(r["cost"] for r in warm) / len(warm)
        print(f"cold ${cold_mean:.4f} vs warm ${warm_mean:.4f} -> {cold_mean / warm_mean:.2f}x")
    if args.gap_seconds:
        went_cold = [r["call"] for r in cold if r["call"] > 1]
        if went_cold:
            print(f"\nVERDICT: warmth did NOT survive a {args.gap_seconds:g}s gap -- call "
                  f"{went_cold[0]} came back cold.")
            print(f"         The cache lifetime is under {args.gap_seconds * (went_cold[0] - 1):g}s "
                  f"of idle.")
        else:
            print(f"\nVERDICT: warmth survived every {args.gap_seconds:g}s gap in this run. "
                  f"The lifetime is longer than {args.gap_seconds * (len(good) - 1):g}s.")
    elif not cold[1:] and len(warm) >= 2 and cold:
        print("\nVERDICT: back-to-back calls stay warm; only the first pays the cold price.")
    else:
        print("\nVERDICT: no clear warm/cold split in this run -- re-run, or widen --calls.")
    json.dump(rows, open(Path(__file__).with_name("cache-warmth-results.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
