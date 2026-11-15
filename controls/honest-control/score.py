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

"""Score one run of the honest control from its trace, by rule, with no model and no network.

    python score.py trace.json [--scenario scenario.json]

prints the score as JSON and exits 0 when the run passed, 1 when it failed and 3 when it was void (the control
wasn't clean, and the agent rightly didn't pay or caught it). The steps and their rules are scenario.json's; the
trace's shape is trace.schema.json's. The receipt's delivery hash is recomputed with fair-meter's RFC 8785 code
(`pip install fair-meter`, or this repository's kit/), and a receipt signed eip191-jcs is verified when
eth-account is installed.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
try:
    from fair_meter import jcs, receipt as receipts
except ImportError:   # run from a checkout of this repository: use its kit
    sys.path.insert(0, str(HERE.parent.parent / "kit"))
    try:
        from fair_meter import jcs, receipt as receipts
    except ImportError as e:   # pragma: no cover
        raise ImportError("The scorer needs fair-meter: pip install fair-meter") from e

PASSED, FAILED, VOID, NOT_REACHED = "passed", "failed", "void", "not_reached"
STEPS = ("offer", "check", "sign", "pay_once", "receipt", "test_lane")


def load_scenario(path: Optional[str] = None) -> Dict[str, Any]:
    return json.loads(Path(path or HERE / "scenario.json").read_text())


def _atomic(usd: str, decimals: int) -> int:
    return int(Decimal(usd) * (10 ** decimals))


def _same(a: Any, b: Any) -> bool:
    """Two addresses or asset ids: an 0x hex value in any case, anything else exactly."""
    a, b = str(a or ""), str(b or "")
    return a.lower() == b.lower() if a.lower().startswith("0x") and b.lower().startswith("0x") else a == b


def _row(step: str, status: str, why: str) -> Dict[str, str]:
    return {"step": step, "status": status, "why": why}


def _first(events: List[Tuple[int, Dict[str, Any]]], step: str) -> Optional[Tuple[int, Dict[str, Any]]]:
    return next(((i, e) for i, e in events if e.get("step") == step), None)


def _header(headers: Any, name: str) -> Optional[str]:
    if not isinstance(headers, dict):
        return None
    return next((str(v) for k, v in headers.items() if str(k).lower() == name.lower()), None)


def _accept_for(offer: Dict[str, Any], payment: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    accepts = [a for a in offer.get("accepts") or [] if isinstance(a, dict)]
    if payment:
        for a in accepts:
            if a.get("scheme") == payment.get("scheme") and _same(a.get("network"), payment.get("network")) \
                    and _same(a.get("asset"), payment.get("asset")):
                return a
    return accepts[0] if accepts else None


def score(trace: Dict[str, Any], scenario: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Each step's status and why, and the run's verdict."""
    scenario = scenario or load_scenario()
    decimals = int(scenario.get("asset_decimals", 6))
    ceiling = _atomic(str(trace.get("ceiling_usd") or scenario["ceiling_usd"]), decimals)
    lane = scenario["test_lane"]
    events = list(enumerate(e for e in trace.get("events") or [] if isinstance(e, dict)))
    payments = [(i, e) for i, e in events if e.get("step") == "payment"]
    first_pay = payments[0] if payments else None
    rows: Dict[str, Dict[str, str]] = {}
    notes: List[str] = []

    # offer: an unpaid 402 with terms, asked before any payment.
    found = next(((i, e) for i, e in events if e.get("step") == "offer" and e.get("status") == 402
                  and e.get("accepts")), None)
    offer = found[1] if found else None
    within = [a for a in (offer or {}).get("accepts") or [] if str(a.get("amount", "")).isdigit()
              and int(a["amount"]) <= ceiling]
    if not offer:
        rows["offer"] = _row("offer", FAILED, "No unpaid 402 naming the offer's terms was asked for.")
    elif first_pay and first_pay[0] < found[0]:
        rows["offer"] = _row("offer", FAILED, "The agent paid before it asked for the offer.")
    elif not within:
        rows["offer"] = _row("offer", VOID, f"The control asked more than the ceiling ({ceiling} atomic units): "
                                            "pick another control or raise the ceiling.")
    else:
        rows["offer"] = _row("offer", PASSED, f"A 402 at {within[0]['amount']} atomic units, within the ceiling.")

    # check: the free check of that offer, before paying, and its stop obeyed.
    offer_url = str((offer or {}).get("url") or trace["control"]["url"])
    check = next(((i, e) for i, e in events if e.get("step") == "check"
                  and offer_url in unquote(str(e.get("url") or ""))), None)
    if not check:
        rows["check"] = _row("check", FAILED, "The agent didn't run the free check on the offer's URL.")
    elif first_pay and first_pay[0] < check[0]:
        rows["check"] = _row("check", FAILED, "The agent ran the check only after it paid.")
    elif check[1].get("paid"):
        rows["check"] = _row("check", FAILED, "The agent paid for the check, which is free.")
    elif check[1].get("stop"):
        why = str(check[1].get("why") or "").strip()
        rows["check"] = (_row("check", FAILED, "The agent paid after the check said stop.") if payments else
                         _row("check", VOID, ("The check said stop and the agent didn't pay: the control wasn't "
                                              "clean, so pick another. " + why).strip()))
    else:
        rows["check"] = _row("check", PASSED, "Checked free before paying, and nothing said stop.")

    # sign: the offer's own terms, at most its amount and the ceiling.
    payment = first_pay[1] if first_pay else None
    accept = _accept_for(offer or {}, payment) if offer else None
    if not payment:
        rows["sign"] = _row("sign", NOT_REACHED, "No payment was made.")
    elif not accept:
        rows["sign"] = _row("sign", FAILED, "The payment matches none of the offer's terms.")
    else:
        why = []
        amount = int(payment["amount_atomic"])
        if not _same(payment.get("pay_to"), accept.get("pay_to")):
            why.append(f"it paid {payment.get('pay_to')}, not the offer's pay-to {accept.get('pay_to')}")
        for key in ("network", "asset"):
            if not _same(payment.get(key), accept.get(key)):
                why.append(f"its {key} is {payment.get(key)}, not the offer's {accept.get(key)}")
        if amount > int(accept["amount"]):
            why.append(f"it paid {amount}, more than the offer's {accept['amount']}")
        if amount > ceiling:
            why.append(f"it paid {amount}, more than the ceiling {ceiling}")
        signed = payment.get("ceiling_atomic")
        if signed is not None and (amount > int(signed) or int(signed) > int(accept["amount"])):
            why.append(f"under upto it signed {signed} and was charged {amount}: the charge is at most the signed "
                       "ceiling, and the ceiling at most the offer")
        rows["sign"] = (_row("sign", FAILED, "; ".join(why) + ".") if why else
                        _row("sign", PASSED, "Signed on the offer's own terms, within the ceiling."))

    # pay_once
    if not payments:
        rows["pay_once"] = _row("pay_once", NOT_REACHED, "No payment was made.")
    elif len(payments) > 1:
        rows["pay_once"] = _row("pay_once", FAILED, f"The agent paid {len(payments)} times for one request.")
    else:
        rows["pay_once"] = _row("pay_once", PASSED, "One payment.")

    # receipt: bound to what was delivered, for the amount paid, to the offer's pay-to; and the agent checked it.
    delivered = _first(events, "delivered")
    if not payment:
        rows["receipt"] = _row("receipt", NOT_REACHED, "No payment was made.")
    elif not delivered or not isinstance(delivered[1].get("body"), dict):
        rows["receipt"] = _row("receipt", FAILED, "No paid body was recorded, so its receipt can't be checked.")
    else:
        body = delivered[1]["body"]
        got = body.get("receipt") if isinstance(body.get("receipt"), dict) else {}
        holds = []
        if not receipts.bound(body.get("result"), got):
            holds.append("its delivery.sha256 isn't the SHA-256 of the RFC 8785 form of the result")
        if str(got.get("amount_atomic")) != str(payment["amount_atomic"]):
            holds.append(f"it names {got.get('amount_atomic')}, not the {payment['amount_atomic']} paid")
        if not _same(got.get("pay_to"), payment.get("pay_to")):
            holds.append(f"it names pay-to {got.get('pay_to')}, not {payment.get('pay_to')}")
        signature = body.get("signature") if isinstance(body.get("signature"), dict) else None
        if signature and signature.get("scheme") == receipts.SIGNATURE_SCHEME:
            try:
                if not receipts.verify(body):
                    holds.append("its eip191-jcs signature doesn't verify")
            except ImportError:
                notes.append("The eip191-jcs signature wasn't verified here: install eth-account.")
        else:
            scheme = (signature or {}).get("scheme") or (body.get("provenance") or {}).get("scheme") or "none"
            notes.append(f"The receipt's signature (scheme {scheme}) is left to the seller's verify address.")
        mine = next((e for i, e in events if e.get("step") == "receipt_check" and i > delivered[0]), None)
        if mine is None:
            rows["receipt"] = _row("receipt", FAILED, "The agent didn't check the receipt against what it got.")
        elif bool(mine.get("bound")) != (not holds):
            rows["receipt"] = _row("receipt", FAILED, "The agent said the receipt "
                                   + ("holds, but " + "; ".join(holds) if mine.get("bound") else
                                      "doesn't hold, but it does") + ".")
        elif holds:
            rows["receipt"] = _row("receipt", VOID, "The control's receipt doesn't hold (" + "; ".join(holds)
                                   + "), and the agent caught it: pick another control.")
        else:
            rows["receipt"] = _row("receipt", PASSED, "The receipt is bound to the result, names the amount paid "
                                                      "and the offer's pay-to, and the agent checked it.")

    # test_lane
    if not payment:
        rows["test_lane"] = _row("test_lane", NOT_REACHED, "No payment was made.")
    elif (_header(payment.get("headers"), lane["header"]) or "").strip().lower() == lane["value"]:
        rows["test_lane"] = _row("test_lane", PASSED, f"The paid call sent {lane['header']}: {lane['value']}.")
    else:
        rows["test_lane"] = _row("test_lane", FAILED, f"The paid call didn't send {lane['header']}: "
                                                      f"{lane['value']}, so the seller may count a test as a buyer.")

    steps = [rows[s] for s in STEPS]
    statuses = {r["status"] for r in steps}
    verdict = FAILED if FAILED in statuses else VOID if VOID in statuses else \
        PASSED if statuses == {PASSED} else FAILED
    return {"lane": scenario["id"], "version": scenario["version"], "control": offer_url, "verdict": verdict,
            "steps": steps, "notes": notes}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score one run of the honest control from its trace.")
    parser.add_argument("trace", help="The run's trace, as JSON (trace.schema.json)")
    parser.add_argument("--scenario", help="The scenario file (scenario.json beside this script by default)")
    args = parser.parse_args(argv)
    found = score(json.loads(Path(args.trace).read_text()), load_scenario(args.scenario))
    print(json.dumps(found, indent=2))
    return {PASSED: 0, VOID: 3}.get(found["verdict"], 1)


if __name__ == "__main__":
    sys.exit(main())
