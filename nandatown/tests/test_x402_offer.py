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

"""x402-offer, the stub seller, the adapter and the E1 harness, without NandaTown (test_e1_town.py runs them against
NandaTown itself). Nothing here leaves the machine: the stub answers in process, or on 127.0.0.1."""

import copy
import json
import os
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import e1  # noqa: E402
import town_adapter  # noqa: E402
import x402_offer as xo  # noqa: E402
import x402_stub as stub  # noqa: E402

BASE = "http://stub.test"
SOLANA = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_PAY_TO = "So1anaPayTo11111111111111111111111111111111"


def _check(seller, tmp_path=None, **kw):
    """One run of the check against a seller answering in process."""
    url = kw.pop("url", BASE)
    kw.setdefault("repeat_delay", 0.0)
    client = httpx.Client(transport=seller.transport())
    out = str(tmp_path / "runs") if tmp_path is not None else "unused"
    return xo.run_check(url, stub.SKILL, http=client, out_dir=out, write=tmp_path is not None, **kw)


def _statuses(result):
    return {s["name"]: s["status"] for s in result["stages"]}


def _failed(result):
    return [s["name"] for s in result["stages"] if s["status"] in ("failed", "error")]


def _note(result, name):
    return next(s["note"] for s in result["stages"] if s["name"] == name)


class _Seller(stub.StubSeller):
    """The honest stub with its offer rewritten by a test."""

    def __init__(self, rewrite, defect="honest", shape="town"):
        super().__init__(BASE, defect, shape)
        self.rewrite = rewrite

    def offer(self, ask):
        return self.rewrite(super().offer(ask), ask)


def _with_solana(offer, ask):
    offer["accepts"].append({"scheme": "exact", "network": SOLANA, "amount": str(stub.PRICE_ATOMIC),
                             "asset": xo.USDC[SOLANA], "payTo": SOLANA_PAY_TO, "maxTimeoutSeconds": 60})
    return offer


# --- The stub: honest passes, and each defect fails its own stage --------------------------------------------------

@pytest.mark.parametrize("shape", stub.SHAPES)
def test_the_honest_stub_passes_every_stage(shape):
    _, result = _check(stub.StubSeller(BASE, "honest", shape))
    assert [s["name"] for s in result["stages"]] == xo.STAGE_ORDER
    assert set(_statuses(result).values()) == {"passed"}
    assert result["verdict"] == "passed"


@pytest.mark.parametrize("defect", stub.DEFECTS)
def test_each_defect_fails_its_own_stage_and_no_other(defect):
    _, result = _check(stub.StubSeller(BASE, defect))
    assert result["verdict"] == "failed"
    assert _failed(result) == [stub.EXPECTED_STAGE[defect]]
    assert {status for name, status in _statuses(result).items() if name != stub.EXPECTED_STAGE[defect]} \
        == {"passed"}


def test_every_variant_publishes_the_same_card():
    cards = {xo.fingerprint(stub.StubSeller(BASE, v).card()) for v in stub.VARIANTS}
    assert len(cards) == 1


def test_the_notes_name_what_differs():
    _, result = _check(stub.StubSeller(BASE, "price_above_listing"))
    assert "asks 2000, listed 1000 (2.00x)" in _note(result, "price_within_listing")
    _, result = _check(stub.StubSeller(BASE, "lookalike_asset"))
    assert stub.LOOKALIKE_ASSET in _note(result, "asset_matches_listing")
    _, result = _check(stub.StubSeller(BASE, "terms_changed_on_retry"))
    assert "1000" in _note(result, "repeat_same_terms") and "10000" in _note(result, "repeat_same_terms")


# --- The bundle -------------------------------------------------------------------------------------------------------

def test_the_bundle_verifies_and_replays(tmp_path):
    directory, result = _check(stub.StubSeller(BASE, "lookalike_asset"), tmp_path)
    assert xo.verify_bundle(directory) == []
    assert sorted(os.listdir(directory)) == sorted(xo.RECORD_FILES + ["manifest.json", "evidence.json", "report.md"])
    evidence = json.loads(Path(directory, "evidence.json").read_text())
    assert [r["test"] for r in evidence["records"]] == xo.STAGE_ORDER
    assert [r["result"] for r in evidence["records"]] == [s["status"] for s in result["stages"]]
    run = json.loads(Path(directory, "run.json").read_text())
    assert run["config"]["payment"] == "none: nothing signed or paid"
    assert not any(i["payload"].get("payment") for i in xo._jsonl(Path(directory, "intents.jsonl").read_text()))


def test_a_changed_record_or_a_forged_result_is_caught(tmp_path):
    directory, _ = _check(stub.StubSeller(BASE, "price_above_listing"), tmp_path)
    result_path, manifest_path = Path(directory, "result.json"), Path(directory, "manifest.json")
    forged = json.loads(result_path.read_text())
    forged["verdict"] = "passed"
    result_path.write_text(json.dumps(forged, indent=2))
    assert "result.json hash mismatch" in xo.verify_bundle(directory)
    # With the manifest rehashed too, the replay still catches it: the recorded events give "failed".
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["result.json"] = xo._sha256_file(str(result_path))
    manifest["bundle_fingerprint"] = xo.fingerprint(manifest["files"])
    manifest_path.write_text(json.dumps(manifest, indent=2))
    assert any("replay mismatch" in p for p in xo.verify_bundle(directory))


def test_credentials_in_the_url_never_reach_the_bundle(tmp_path):
    directory, result = _check(stub.StubSeller(BASE), tmp_path, url="http://buyer:hunter2@stub.test")
    assert result["verdict"] == "passed"
    for name in os.listdir(directory):
        assert "hunter2" not in Path(directory, name).read_text(), name


# --- Offers in other forms ---------------------------------------------------------------------------------------------

def test_one_unit_over_the_listed_price_fails_and_under_passes():
    def ask(amount):
        def rewrite(offer, _):
            offer["accepts"][0]["amount"] = str(amount)
            return offer
        return rewrite

    _, result = _check(_Seller(ask(stub.PRICE_ATOMIC + 1)))
    assert _failed(result) == ["price_within_listing"]
    _, result = _check(_Seller(ask(stub.PRICE_ATOMIC - 1)))
    assert result["verdict"] == "passed"


def test_an_x402_v1_offer_is_read():
    def v1(offer, _):
        option = offer["accepts"][0]
        return {"x402Version": 1, "error": "Payment required", "accepts": [{
            "scheme": "exact", "network": "base", "maxAmountRequired": option["amount"], "asset": option["asset"],
            "payTo": option["payTo"], "resource": f"{BASE}/skills/quote", "maxTimeoutSeconds": 60}]}

    _, result = _check(_Seller(v1))
    assert result["verdict"] == "passed"


def test_an_a2a_1_0_result_is_read():
    class A2A10(stub.StubSeller):
        def rpc(self, body):
            answer = super().rpc(body)
            task = answer.get("result")
            if isinstance(task, dict) and task.get("kind") == "task":
                task = copy.deepcopy(task)
                del task["kind"]
                task["status"]["state"] = "TASK_STATE_" + task["status"]["state"].upper().replace("-", "_")
                answer["result"] = {"task": task}
            return answer

    _, result = _check(A2A10(BASE))
    assert result["verdict"] == "passed"


def test_card_params_in_each_form():
    usdc_base, usdc_solana = xo.USDC["eip155:8453"], xo.USDC[SOLANA]
    one = {"uri": stub.X402_V01, "params": {"networks": ["eip155:8453"], "assets": ["USDC"], "payTo": stub.PAY_TO}}
    assert xo.card_options([one]) == [{"network": "eip155:8453", "asset": usdc_base, "pay_to": stub.PAY_TO}]
    per_network = {"uri": stub.X402_V01, "params": {
        "networks": ["eip155:8453", SOLANA], "assets": ["USDC"],
        "payTo": {"eip155:8453": stub.PAY_TO, SOLANA: SOLANA_PAY_TO}}}
    assert xo.card_options([per_network]) == [{"network": "eip155:8453", "asset": usdc_base, "pay_to": stub.PAY_TO},
                                              {"network": SOLANA, "asset": usdc_solana, "pay_to": SOLANA_PAY_TO}]
    accepts = {"uri": stub.X402_V01, "params": {"accepts": [{"network": "base", "asset": "USDC",
                                                             "payTo": stub.PAY_TO}]}}
    assert xo.card_options([accepts]) == [{"network": "eip155:8453", "asset": usdc_base, "pay_to": stub.PAY_TO}]


def test_an_option_the_card_never_lists_fails_network_pay_to_and_asset():
    _, result = _check(_Seller(_with_solana))
    assert _failed(result) == ["pay_to_matches_listing", "network_matches_listing", "asset_matches_listing"]
    assert _statuses(result)["price_within_listing"] == "passed"
    assert _statuses(result)["repeat_same_terms"] == "passed"
    assert "option 2 (solana:" in _note(result, "network_matches_listing")


def test_the_same_option_passes_once_the_card_lists_it():
    class ListsSolana(_Seller):
        def card(self):
            card = super().card()
            params = card["capabilities"]["extensions"][0]["params"]
            params["networks"] = [stub.NETWORK, SOLANA]
            params["payTo"] = {stub.NETWORK: stub.PAY_TO, SOLANA: SOLANA_PAY_TO}
            return card

    _, result = _check(ListsSolana(_with_solana))
    assert result["verdict"] == "passed"


# --- Agents that don't ask, or don't answer -----------------------------------------------------------------------

def test_a_paid_skill_that_completes_unpaid_fails_payment_request():
    class Free(stub.StubSeller):
        def _send(self, params):
            task = super()._send(params)
            task["status"] = {"state": "completed", "timestamp": stub._now()}
            task["artifacts"] = [{"artifactId": "a-1", "parts": [{"kind": "data", "data": {"total_cents": 3990}}]}]
            return task

    _, result = _check(Free(BASE))
    assert _failed(result) == ["payment_request"]
    assert "completed without asking for payment" in _note(result, "payment_request")
    assert _statuses(result)["price_within_listing"] == "not_tested"
    assert _statuses(result)["repeat_same_terms"] == "not_tested"


def test_a_message_instead_of_a_task_fails_payment_request():
    class Talks(stub.StubSeller):
        def _send(self, params):
            return {"kind": "message", "messageId": "m-1", "role": "agent", "parts": [{"kind": "text", "text": "hi"}]}

    _, result = _check(Talks(BASE))
    assert _failed(result) == ["payment_request"]
    assert "answered with a 'message', not a task" in _note(result, "payment_request")


def test_no_listed_price_fails_listing_retrieval():
    class NoPrice(stub.StubSeller):
        def card(self):
            card = super().card()
            card["skills"][0]["description"] = "Given JSON {sku, quantity, unit_price_cents}, returns JSON."
            return card

        def handle(self, method, path, body):
            if method == "GET" and path.endswith("/skills/" + stub.SKILL):
                return 404, {"error": "not found"}
            return super().handle(method, path, body)

    _, result = _check(NoPrice(BASE))
    assert _failed(result) == ["listing_retrieval"]
    assert "no listed price" in _note(result, "listing_retrieval")


def test_a_card_without_the_extension_fails():
    class Plain(stub.StubSeller):
        def card(self):
            card = super().card()
            card["capabilities"]["extensions"] = []
            return card

    _, result = _check(Plain(BASE))
    assert _failed(result) == ["x402_extension_declared", "listing_retrieval"]


def test_a_missing_card_or_a_bad_url_fails_early():
    _, result = xo.run_check("not a url", stub.SKILL, write=False)
    assert _failed(result) == ["resolution"] and result["verdict"] == "failed"

    class Gone(stub.StubSeller):
        def handle(self, method, path, body):
            return 404, {"error": "not found"}

    _, result = _check(Gone(BASE))
    assert _failed(result) == ["agent_card_retrieval"]
    assert "HTTP 404" in _note(result, "agent_card_retrieval")


def test_a_listing_file_replaces_the_agents_own(tmp_path):
    listing = {"skill": stub.SKILL, "price_usd": "0.001", "source": "test",
               "options": [{"network": "base", "asset": "USDC", "pay_to": "0x" + "1" * 40}]}
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((HERE / "schemas" / "x402_offer_listing.schema.json").read_text())
    jsonschema.validate(listing, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"options": listing["options"]}, schema)
    with pytest.raises(xo.ListingError):
        xo.normalize_listing({"options": listing["options"]}, "test")
    path = tmp_path / "listing.json"
    path.write_text(json.dumps(listing))
    loaded = xo.load_listing(str(path))
    assert loaded["price_atomic"] == stub.PRICE_ATOMIC and loaded["options"][0]["network"] == "eip155:8453"
    _, result = _check(stub.StubSeller(BASE), listing=loaded)
    assert _failed(result) == ["pay_to_matches_listing"]


def test_prices_round_up_to_the_smallest_unit():
    assert xo.to_atomic("$0.001", 6) == 1000
    assert xo.to_atomic("0.0000001", 6) == 1
    assert xo.to_atomic("free", 6) is None
    assert xo.normalize_listing({"price_usd": "0", "options": [{"network": "base", "asset": "USDC",
                                                                "pay_to": stub.PAY_TO}]}, "t")["price_atomic"] == 0


# --- The adapter NandaTown's test needs for a routed agent ----------------------------------------------------------

def test_the_adapter_reaches_a_routed_agents_skill():
    seller = stub.StubSeller(BASE, shape="routed")
    client = httpx.Client(transport=seller.transport())
    town_message = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {
        "kind": "message", "role": "user", "messageId": "m-1",
        "parts": [{"kind": "text", "text": json.dumps({"sku": "widget", "quantity": 2})}]}}}
    body = json.dumps(town_message).encode()

    status, _, _ = town_adapter.Adapter(BASE, "passthrough", http=client).forward("POST", "/", body,
                                                                                  "application/json")
    assert status == 405

    adapter = town_adapter.Adapter(BASE, "skill", stub.SKILL, http=client)
    status, _, data = adapter.forward("POST", "/", body, "application/json")
    assert status == 200 and json.loads(data)["result"]["status"]["state"] == "input-required"
    entry = adapter.log[-1]
    assert entry["upstream"] == BASE + "/a2a"
    assert entry["added_part"] == {"skill": stub.SKILL, "arguments": {"sku": "widget", "quantity": 2,
                                                                      "unit_price_cents": 1995}}
    assert entry["x402_status"] == "payment-required"


# --- The harness ------------------------------------------------------------------------------------------------------

def test_exact_intervals():
    assert e1.clopper_pearson(20, 20) == pytest.approx((0.025 ** (1 / 20), 1.0), abs=1e-6)
    assert e1.clopper_pearson(0, 20) == pytest.approx((0.0, 1 - 0.025 ** (1 / 20)), abs=1e-6)
    lower, upper = e1.clopper_pearson(10, 20)
    assert lower == pytest.approx(0.2720, abs=1e-3) and upper == pytest.approx(0.7280, abs=1e-3)


def test_the_harness_holds_on_the_stub(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NANDATOWN_HOME", str(tmp_path / "home"))
    out = tmp_path / "out"
    assert e1.main(["--repeats", "2", "--no-town", "--out", str(out)]) == 0
    results = json.loads((out / "results.json").read_text())
    assert results["stub_expectations"] == {"hold": True, "problems": []}
    groups = {(g["tool"], g["subject"], g["variant"]): g["summary"] for g in results["groups"]}
    assert groups[("x402-offer", "stub", "honest")]["passed"] == e1.share(2, 2)
    assert groups[("x402-offer", "stub-routed", "honest")]["passed"] == e1.share(2, 2)
    for defect in stub.DEFECTS:
        assert groups[("x402-offer", "stub", defect)]["detected_at_named_stage"] == e1.share(2, 2)
        assert groups[("x402-offer", "stub", defect)]["identical_runs"]
    assert "failed exactly there" in (out / "summary.md").read_text()
