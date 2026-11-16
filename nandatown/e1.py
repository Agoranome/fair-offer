# Copyright 2026 Kamkar Foundation, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""E1 of the Nanda Town experiment plan (16 Nov 2026): the x402-offer check and NandaTown's own agent test, each run
again and again against a stub seller (honest, and with each of five planted defects) and against Agoranome's paid A2A
skill. Nothing is signed and no money moves.

  python e1.py --repeats 20 --out e1-out
  python e1.py --repeats 20 --agoranome https://api.agoranome.com --skill price_position --out e1-out

The groups, each run --repeats times:
  x402-offer   on the stub, one group per variant (honest and the five defects); on the honest stub in the routed
               shape, Agoranome's (the control for reading a routed agent); and on Agoranome's skill
  nandatown    a2a-capability-fulfillment@0.3, NandaTown's own test, unmodified, with its own HTTP client: on the
               same stub variants; on the honest stub in the routed shape, through town_adapter.py in passthrough and
               in skill mode (the control for the adapter); and on Agoranome through the adapter, in both modes.
               Runs only when nandatown is installed (pinned at 6f9a7c0 in CI).

Writes results.json (every run: verdict, each stage's status, the failed stages and their notes, the bundle's
fingerprint), summary.md, and every bundle under bundles/. With --emit, it also prints the summary and a base64
tar.gz of the first bundle of each Agoranome group, so a reader of the CI log can rebuild and verify them.

Exit status 0 when the stub results hold (the honest stub passes every stage of x402-offer in both shapes, and each
defect fails exactly the stage named for it), 1 when they don't, 2 on a harness error. NandaTown's verdicts and
Agoranome's are reported, never gated: the job measures them.
"""

from __future__ import annotations

import argparse
import base64
import collections
import io
import json
import math
import os
import platform
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import x402_offer  # noqa: E402
import x402_stub  # noqa: E402
import town_adapter  # noqa: E402

TOWN_PROFILE = "a2a-capability-fulfillment@0.3"
PLAN = "https://claude.ai/artifact/WNp2MtKou1mXd4QT5GiDSd"


# --- Exact binomial intervals ---------------------------------------------------------------------------------------

def _cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def _solve(f: Callable[[float], float]) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """The exact (Clopper-Pearson) 1 - alpha interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    lower = 0.0 if k == 0 else _solve(lambda p: (1 - _cdf(k - 1, n, p)) - alpha / 2)
    upper = 1.0 if k == n else _solve(lambda p: alpha / 2 - _cdf(k, n, p))
    return lower, upper


def share(k: int, n: int) -> dict[str, Any]:
    lower, upper = clopper_pearson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": [round(lower, 4), round(upper, 4)]}


def share_text(s: dict[str, Any]) -> str:
    return f"{s['k']}/{s['n']} (95% CI {100 * s['ci95'][0]:.1f} to {100 * s['ci95'][1]:.1f}%)"


# --- One run, as a row ----------------------------------------------------------------------------------------------

def _row(i: int, result: dict[str, Any], bundle: str) -> dict[str, Any]:
    stages = {s["name"]: s["status"] for s in result["stages"]}
    failed = [s["name"] for s in result["stages"] if s["status"] in ("failed", "error")]
    with open(os.path.join(bundle, "manifest.json"), encoding="utf-8") as f:
        fp = json.load(f)["bundle_fingerprint"]
    return {"run": i, "run_id": result["run_id"], "verdict": result["verdict"], "stages": stages, "failed": failed,
            "first_failed": failed[0] if failed else None,
            "notes": {s["name"]: s["note"] for s in result["stages"] if s["status"] in ("failed", "error")},
            "bundle": os.path.relpath(bundle), "bundle_fingerprint": fp}


def _town_row(i: int, result: Any, bundle: str) -> dict[str, Any]:
    row = _row(i, result.model_dump(), bundle)
    with open(os.path.join(bundle, "events.jsonl"), encoding="utf-8") as f:
        text = f.read()
    # Whether NandaTown recorded anything of the offer: a stage can only judge what the events hold.
    row["offer_recorded"] = any(key in text for key in ("x402.payment", "payTo", "accepts", "maxAmountRequired"))
    return row


# --- The groups ------------------------------------------------------------------------------------------------------

def run_x402_offer(url: str, skill: str, repeats: int, out: str, delay: float, between: float) -> list[dict]:
    rows = []
    for i in range(1, repeats + 1):
        bundle, result = x402_offer.run_check(url, skill, out_dir=out, repeat_delay=delay)
        rows.append(_row(i, result, bundle))
        if between and i < repeats:
            time.sleep(between)
    return rows


def run_town(url: str, repeats: int, out: str, between: float) -> list[dict]:
    from nandatown.path_runner import run_path_test

    rows = []
    for i in range(1, repeats + 1):
        bundle, result = run_path_test(url, out, profile_ref=TOWN_PROFILE)
        rows.append(_town_row(i, result, bundle))
        if between and i < repeats:
            time.sleep(between)
    return rows


def _stub(defect: str, shape: str = "town"):
    server, seller, base = x402_stub.serve(defect, shape=shape)
    return server, seller, base


def _stop(server) -> None:
    server.shutdown()
    server.server_close()


def summarize(group: dict[str, Any], honest_key: Any = None) -> dict[str, Any]:
    rows = group["runs"]
    n = len(rows)
    vectors = [json.dumps([r["verdict"], r["stages"]], sort_keys=True) for r in rows]
    summary: dict[str, Any] = {
        "n": n,
        "verdicts": dict(collections.Counter(r["verdict"] for r in rows)),
        "first_failed": dict(collections.Counter(r["first_failed"] or "none" for r in rows)),
        "identical_runs": len(set(vectors)) == 1,
        "distinct_outcomes": len(set(vectors)),
        "stage_status": {name: dict(collections.Counter(r["stages"].get(name) for r in rows))
                         for name in (rows[0]["stages"] if rows else {})},
        "passed": share(sum(r["verdict"] == "passed" for r in rows), n),
    }
    expected = group.get("expected_stage")
    if expected:
        summary["detected_at_named_stage"] = share(sum(r["failed"] == [expected] for r in rows), n)
    if honest_key is not None:
        summary["differs_from_honest"] = share(sum(v != honest_key for v in vectors), n)
    if group["tool"] == "nandatown":
        summary["offer_recorded"] = sum(bool(r.get("offer_recorded")) for r in rows)
    return summary


def _modal(group: dict[str, Any]) -> str:
    vectors = [json.dumps([r["verdict"], r["stages"]], sort_keys=True) for r in group["runs"]]
    return collections.Counter(vectors).most_common(1)[0][0]


def _tarball(directory: str) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(directory, arcname=os.path.basename(directory))
    return base64.b64encode(buffer.getvalue()).decode()


# --- Summary ---------------------------------------------------------------------------------------------------------

def render_summary(results: dict[str, Any]) -> str:
    groups = results["groups"]
    by = {(g["tool"], g["subject"], g["variant"]): g for g in groups}
    n = results["repeats"]
    lines = [f"# E1: the x402-offer check and NandaTown's agent test, {n} repeats each", "",
             f"Generated {results['generated_at']} ({results['environment']['where']}). Nothing was signed or paid."
             f" Plan: {PLAN}", ""]
    lines += ["## The stub's five planted defects", "",
              "| Variant | Stage named for it | x402-offer: failed exactly there | NandaTown: told apart from"
              " honest | NandaTown verdict, first failed stage |", "|---|---|---|---|---|"]
    for variant in x402_stub.VARIANTS:
        ours = by.get(("x402-offer", "stub", variant))
        town = by.get(("nandatown", "stub", variant))
        expected = x402_stub.EXPECTED_STAGE.get(variant, "none (control)")
        if variant == "honest":
            mine = "passed every stage: " + share_text(ours["summary"]["passed"]) if ours else "not run"
        else:
            mine = share_text(ours["summary"]["detected_at_named_stage"]) if ours else "not run"
        if town:
            told = share_text(town["summary"]["differs_from_honest"]) if variant != "honest" else "control"
            verdicts = ", ".join(f"{k} {v}" for k, v in town["summary"]["verdicts"].items())
            first = ", ".join(f"{k} {v}" for k, v in town["summary"]["first_failed"].items())
            town_text = f"{verdicts}; {first}"
        else:
            told, town_text = "not run", "not run"
        lines.append(f"| {variant} | {expected} | {mine} | {told} | {town_text} |")
    lines.append("")
    ag = [g for g in groups if g["subject"] == "agoranome"]
    if ag:
        lines += ["## Agoranome", "", "| Tool | Variant | Verdicts | First failed stage | Identical runs | Note |",
                  "|---|---|---|---|---|---|"]
        for g in ag:
            s = g["summary"]
            notes = sorted({note for r in g["runs"] for note in r["notes"].values()})
            note = (notes[0][:300] + (f" (+{len(notes) - 1} other)" if len(notes) > 1 else "")) if notes else ""
            lines.append(f"| {g['tool']} | {g['variant']} | "
                         + ", ".join(f"{k} {v}" for k, v in s["verdicts"].items())
                         + f" | {', '.join(f'{k} {v}' for k, v in s['first_failed'].items())}"
                         f" | {s['identical_runs']} | {note.replace('|', '/')} |")
        lines.append("")
    routed = [g for g in groups if g["subject"] == "stub-routed"]
    if routed:
        lines += ["## Controls: the honest stub in Agoranome's routed shape", "",
                  "JSON-RPC only at /a2a, and a message must name its skill. x402-offer reads it as it is; NandaTown"
                  " reaches it through town_adapter.py.", "",
                  "| Tool | How | Verdicts | First failed stage | Note |", "|---|---|---|---|---|"]
        for g in routed:
            s = g["summary"]
            notes = sorted({note for r in g["runs"] for note in r["notes"].values()})
            how = "direct" if g["tool"] == "x402-offer" else f"adapter, {g['variant']}"
            lines.append(f"| {g['tool']} | {how} | " + ", ".join(f"{k} {v}" for k, v in s["verdicts"].items())
                         + f" | {', '.join(f'{k} {v}' for k, v in s['first_failed'].items())}"
                         f" | {(notes[0][:200] if notes else '').replace('|', '/')} |")
        lines.append("")
    lines += ["## Determinism", "", "| Tool | Subject | Variant | Runs | Distinct outcomes |", "|---|---|---|---|---|"]
    for g in groups:
        lines.append(f"| {g['tool']} | {g['subject']} | {g['variant']} | {g['summary']['n']}"
                     f" | {g['summary']['distinct_outcomes']} |")
    town_groups = [g for g in groups if g["tool"] == "nandatown"]
    if town_groups:
        recorded = sum(g["summary"]["offer_recorded"] for g in town_groups)
        runs = sum(g["summary"]["n"] for g in town_groups)
        lines += ["", f"NandaTown recorded anything of an x402 offer in {recorded} of its {runs} runs' events."]
    return "\n".join(lines) + "\n"


def stub_expectations(results: dict[str, Any]) -> list[str]:
    """What must hold on the stub for the check to be right; empty when it all holds."""
    problems = []
    by = {(g["tool"], g["subject"], g["variant"]): g for g in results["groups"]}
    for subject in ("stub", "stub-routed"):
        honest = by.get(("x402-offer", subject, "honest"))
        if honest is None or honest["summary"]["passed"]["k"] != honest["summary"]["n"]:
            problems.append(f"the honest {subject} didn't pass every stage in every repeat")
    for defect, stage in x402_stub.EXPECTED_STAGE.items():
        group = by.get(("x402-offer", "stub", defect))
        if group is None or group["summary"]["detected_at_named_stage"]["k"] != group["summary"]["n"]:
            problems.append(f"{defect} wasn't caught at {stage}, and only there, in every repeat")
    return problems


# --- Main ------------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e1", description="Run E1 of the Nanda Town experiment plan.")
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--out", default="e1-out")
    parser.add_argument("--agoranome", default=None, help="Agoranome's API base URL, for the live groups")
    parser.add_argument("--skill", default="price_position", help="the paid skill to ask Agoranome for")
    parser.add_argument("--no-town", action="store_true", help="skip NandaTown's test even when it is installed")
    parser.add_argument("--emit", action="store_true", help="print the summary and the Agoranome sample bundles")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    os.environ.setdefault("NANDATOWN_HOME", tempfile.mkdtemp(prefix="nandatown-home-"))
    town = None
    if not args.no_town:
        try:
            from nandatown import __version__ as town
        except ImportError:
            town = None
    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    results: dict[str, Any] = {
        "experiment": "E1", "plan": PLAN, "repeats": args.repeats,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {"where": run_url or "local", "python": sys.version.split()[0],
                        "platform": platform.platform(), "x402_offer": x402_offer.VERSION,
                        "evaluator": x402_offer.EVALUATOR_VERSION, "nandatown": town,
                        "nandatown_profile": TOWN_PROFILE if town else None,
                        "town_adapter": town_adapter.VERSION},
        "groups": [], "adapter_logs": {},
    }
    bundles = os.path.join(args.out, "bundles")

    def group(tool: str, subject: str, variant: str, rows: list[dict], expected: str | None = None) -> None:
        results["groups"].append({"tool": tool, "subject": subject, "variant": variant, "expected_stage": expected,
                                  "runs": rows})
        print(f"{tool:10s} {subject:12s} {variant:24s} " + ", ".join(
            f"{k} {v}" for k, v in collections.Counter(r["verdict"] for r in rows).items()), flush=True)

    try:
        # The stub: x402-offer, then NandaTown's test, on each variant.
        for variant in x402_stub.VARIANTS:
            server, _, base = _stub(variant)
            try:
                rows = run_x402_offer(base, x402_stub.SKILL, args.repeats,
                                      os.path.join(bundles, "x402-offer", "stub", variant), 0.0, 0.0)
            finally:
                _stop(server)
            group("x402-offer", "stub", variant, rows, x402_stub.EXPECTED_STAGE.get(variant))
        server, _, base = _stub("honest", shape="routed")
        try:
            rows = run_x402_offer(base, x402_stub.SKILL, args.repeats,
                                  os.path.join(bundles, "x402-offer", "stub-routed", "honest"), 0.0, 0.0)
        finally:
            _stop(server)
        group("x402-offer", "stub-routed", "honest", rows)
        if town:
            for variant in x402_stub.VARIANTS:
                server, _, base = _stub(variant)
                try:
                    rows = run_town(base, args.repeats, os.path.join(bundles, "nandatown", "stub", variant), 0.0)
                finally:
                    _stop(server)
                group("nandatown", "stub", variant, rows, x402_stub.EXPECTED_STAGE.get(variant))
            # The adapter's control: the honest stub in the routed shape, as Agoranome is routed.
            for mode in town_adapter.MODES:
                server, _, base = _stub("honest", shape="routed")
                adapter = town_adapter.Adapter(base, mode, x402_stub.SKILL if mode == "skill" else None)
                proxy, url = town_adapter.serve(adapter)
                try:
                    rows = run_town(url, args.repeats, os.path.join(bundles, "nandatown", "stub-routed", mode), 0.0)
                finally:
                    _stop(proxy)
                    _stop(server)
                results["adapter_logs"][f"stub-routed/{mode}"] = adapter.log[:4]
                group("nandatown", "stub-routed", mode, rows)

        # Agoranome, live: the check, then NandaTown's test through the adapter in each mode.
        if args.agoranome:
            rows = run_x402_offer(args.agoranome, args.skill, args.repeats,
                                  os.path.join(bundles, "x402-offer", "agoranome", args.skill), 1.0, 1.0)
            group("x402-offer", "agoranome", args.skill, rows)
            if town:
                for mode in town_adapter.MODES:
                    adapter = town_adapter.Adapter(args.agoranome, mode, args.skill if mode == "skill" else None)
                    proxy, url = town_adapter.serve(adapter)
                    try:
                        rows = run_town(url, args.repeats,
                                        os.path.join(bundles, "nandatown", "agoranome", mode), 1.0)
                    finally:
                        _stop(proxy)
                    results["adapter_logs"][f"agoranome/{mode}"] = adapter.log[:4]
                    group("nandatown", "agoranome", f"{mode}" + (f":{args.skill}" if mode == "skill" else ""),
                          rows)
    except Exception as exc:  # the harness's own fault
        print(f"e1: harness error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)

    honest = {tool: _modal(g) for g in results["groups"] for tool in [g["tool"]]
              if g["subject"] == "stub" and g["variant"] == "honest"}
    for g in results["groups"]:
        key = honest.get(g["tool"]) if g["subject"] == "stub" and g["variant"] != "honest" else None
        g["summary"] = summarize(g, key)
    problems = stub_expectations(results)
    results["stub_expectations"] = {"hold": not problems, "problems": problems}

    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    summary = render_summary(results)
    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    if args.emit:
        print(summary)
        compact = {"generated_at": results["generated_at"], "environment": results["environment"],
                   "groups": [{k: g[k] for k in ("tool", "subject", "variant", "summary")}
                              | {"runs": [{k: r[k] for k in ("run", "run_id", "verdict", "failed", "notes",
                                                              "bundle_fingerprint")} for r in g["runs"]]}
                              for g in results["groups"] if g["subject"] == "agoranome"],
                   "adapter_logs": results["adapter_logs"]}
        print("E1-AGORANOME-JSON-BEGIN")
        print(json.dumps(compact, separators=(",", ":")))
        print("E1-AGORANOME-JSON-END")
        for g in results["groups"]:
            if g["subject"] == "agoranome" and g["runs"]:
                print(f"E1-BUNDLE-BEGIN {g['tool']}/{g['variant']}")
                print(_tarball(g["runs"][0]["bundle"]))
                print("E1-BUNDLE-END")
    for problem in problems:
        print(f"e1: {problem}", file=sys.stderr)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
