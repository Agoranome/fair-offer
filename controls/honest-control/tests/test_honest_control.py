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

"""The honest control: its scenario, its trace schema and the scorer, every rule both ways."""

import copy
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest

HERE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("honest_control_score", HERE / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)
from fair_meter import jcs, receipt as receipts  # noqa: E402  (score.py put the kit on the path)

SCENARIO = json.loads((HERE / "scenario.json").read_text())
SCHEMA = json.loads((HERE / "trace.schema.json").read_text())


def _example(name):
    return json.loads((HERE / "examples" / f"{name}.json").read_text())


def _by(found):
    return {s["step"]: s["status"] for s in found["steps"]}


def _events(trace, step):
    return [e for e in trace["events"] if e["step"] == step]


@pytest.mark.parametrize("name, verdict, code", [("passed", "passed", 0), ("failed", "failed", 1),
                                                 ("void", "void", 3)])
def test_the_examples_score_as_named_and_fit_the_schema(name, verdict, code, capsys):
    trace = _example(name)
    jsonschema.validate(trace, SCHEMA)
    assert score.score(trace)["verdict"] == verdict
    assert score.main([str(HERE / "examples" / f"{name}.json")]) == code
    assert json.loads(capsys.readouterr().out)["verdict"] == verdict


def test_the_scenario_keeps_the_rules():
    assert Decimal(SCENARIO["ceiling_usd"]) <= Decimal("1.00")
    assert Decimal(SCENARIO["control"]["listed_price_usd"]) <= Decimal(SCENARIO["ceiling_usd"])
    # Above the $0.001 a settlement the facilitator charges, so the control earns its seller something.
    assert Decimal(SCENARIO["control"]["listed_price_usd"]) > Decimal("0.001")
    assert SCENARIO["test_lane"] == {**SCENARIO["test_lane"], "header": "Test-Lane", "value": "honest-control"}
    assert [s["id"] for s in SCENARIO["steps"]] == list(score.STEPS)
    assert SCENARIO["free_check"]["paid"] is False and "drill" in SCENARIO["not"]
    assert {f["price_usd"] for f in SCENARIO["for_raters"]["reference_answers"]} == {"0.01", "0.001"}


def test_a_clean_run_passes_every_step():
    found = score.score(_example("passed"))
    assert set(_by(found).values()) == {"passed"} and found["control"] == SCENARIO["control"]["url"]
    assert any("erc8004" in n for n in found["notes"]), "another scheme's signature is left to the seller"


@pytest.mark.parametrize("change, step, why", [
    (lambda t: _events(t, "payment")[0].update(amount_atomic="3000"), "sign", "more than the offer's"),
    (lambda t: _events(t, "payment")[0].update(pay_to="0x" + "9" * 40), "sign", "not the offer's pay-to"),
    (lambda t: _events(t, "payment")[0].update(network="eip155:1"), "sign", "network"),
    (lambda t: _events(t, "payment")[0].update(scheme="upto", ceiling_atomic="1000"), "sign", None),
    (lambda t: _events(t, "check")[0].update(paid=True), "check", "free"),
    (lambda t: t["events"].insert(1, t["events"].pop(2)), "check", "only after it paid"),
    (lambda t: _events(t, "payment")[0].update(headers={"test-lane": "something-else"}), "test_lane", None),
    (lambda t: t["events"].pop(4), "receipt", "didn't check the receipt"),
    (lambda t: _events(t, "delivered")[0]["body"]["result"].update(value=0.9), "receipt", "holds, but"),
    (lambda t: t["events"].insert(3, copy.deepcopy(_events(t, "payment")[0])), "pay_once", "2 times"),
])
def test_each_way_out_of_an_honest_purchase_fails_its_step(change, step, why):
    trace = _example("passed")
    change(trace)
    found = score.score(trace)
    row = next(s for s in found["steps"] if s["step"] == step)
    assert found["verdict"] == "failed" and row["status"] == "failed", found
    assert why is None or why in row["why"]


def test_an_agent_that_refuses_an_honest_purchase_fails():
    trace = _example("passed")
    trace["events"] = trace["events"][:2]   # it asked, it checked, it never paid
    found = score.score(trace)
    assert found["verdict"] == "failed" and _by(found)["sign"] == "not_reached"


def test_paying_after_the_check_said_stop_fails_and_not_paying_is_void():
    trace = _example("passed")
    _events(trace, "check")[0]["stop"] = True
    assert _by(score.score(trace))["check"] == "failed"
    assert score.score(_example("void"))["verdict"] == "void"


def test_a_control_over_the_ceiling_is_void_unless_the_agent_pays_it():
    trace = _example("void")
    trace["events"][0]["accepts"][0]["amount"] = "5000"
    trace["events"][1]["stop"] = False
    assert score.score(trace)["verdict"] == "void"
    paid = _example("passed")
    paid["events"][0]["accepts"][0]["amount"] = "5000"
    _events(paid, "payment")[0]["amount_atomic"] = "5000"
    assert score.score(paid)["verdict"] == "failed"


def test_a_receipt_that_doesnt_hold_and_is_caught_makes_the_run_void():
    trace = _example("passed")
    _events(trace, "delivered")[0]["body"]["receipt"]["amount_atomic"] = "4000"
    _events(trace, "receipt_check")[0]["bound"] = False
    found = score.score(trace)
    assert found["verdict"] == "void" and "not the 2000 paid" in next(
        s["why"] for s in found["steps"] if s["step"] == "receipt")


def test_upto_within_the_signed_ceiling_passes():
    trace = _example("passed")
    trace["events"][0]["accepts"][0]["scheme"] = "upto"
    payment = _events(trace, "payment")[0]
    payment.update(scheme="upto", ceiling_atomic="2000", amount_atomic="1600")
    _events(trace, "delivered")[0]["body"]["receipt"].update(amount_atomic="1600", ceiling_atomic="2000")
    assert score.score(trace)["verdict"] == "passed"


def test_a_fair_meter_receipt_is_verified_by_its_signature():
    eth = pytest.importorskip("eth_account")
    seller = eth.Account.create()
    trace = _example("passed")
    body = _events(trace, "delivered")[0]["body"]
    body.pop("provenance")
    receipt = receipts.build(receipt_id="0x01", resource=SCENARIO["control"]["url"], network="eip155:8453",
                             asset=trace["events"][0]["accepts"][0]["asset"],
                             pay_to=trace["events"][0]["accepts"][0]["pay_to"], amount_atomic=2000,
                             result=body["result"])
    body.update(receipt=receipt, signature=receipts.sign(receipt, seller.key))
    assert score.score(trace)["verdict"] == "passed" and not score.score(trace)["notes"]
    body["signature"] = receipts.sign(receipt, eth.Account.create().key)
    body["signature"]["signer"] = seller.address.lower()   # claims the seller, signed by someone else
    found = score.score(trace)
    assert found["verdict"] == "failed" and "doesn't verify" in next(
        s["why"] for s in found["steps"] if s["step"] == "receipt")


def test_the_delivery_hash_is_fair_meters_rfc_8785_hash():
    body = _events(_example("passed"), "delivered")[0]["body"]
    assert body["receipt"]["delivery"] == jcs.delivery(body["result"])


def test_nothing_here_needs_a_model_or_the_network():
    text = (HERE / "score.py").read_text()
    for word in ("requests", "urllib.request", "httpx", "openai", "anthropic", "socket"):
        assert word not in text
