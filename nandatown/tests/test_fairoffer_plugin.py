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

"""The fairoffer.v1 payments plugin, run in NandaTown itself (pip install the commit named in ../README.md)."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

nandatown = pytest.importorskip("nandatown")

from nandatown.bundle import verify_bundle  # noqa: E402
from nandatown.sim.runner import build_engine, run_lab  # noqa: E402
from nandatown.sim.scenario import load_scenario_file  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
SCENARIOS = HERE / "scenarios"


def _plugin():
    spec = importlib.util.spec_from_file_location("fairoffer_plugin", HERE / "fairoffer_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fo = _plugin()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    # attest_bundle keeps an operator key under NANDATOWN_HOME.
    monkeypatch.setenv("NANDATOWN_HOME", str(tmp_path / "home"))


def _run(name, tmp_path, **kw):
    bundle, result = run_lab(str(SCENARIOS / f"{name}.yaml"), str(tmp_path / "runs"), **kw)
    return bundle, result, {s.name: s for s in result.stages}


def _events(bundle):
    with open(os.path.join(bundle, "events.jsonl")) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_the_drill_passes_with_fairoffer_and_every_trap_is_its_own_stage(tmp_path):
    bundle, result, stages = _run("fair_offer_drill", tmp_path)
    assert result.verdict == "passed"
    assert [s for s in stages if s.endswith(("_paid", "_capped", "_refused", "_once", "_caught"))] == [
        "control_paid", "price_switch_capped", "lookalike_asset_refused", "pay_to_swap_refused",
        "network_switch_refused", "instructions_not_paid", "slow_responder_paid_once", "unsettled_200_caught",
        "ambiguous_error_paid_once"]
    assert stages["ledger_conserved"].status == "passed"
    events = _events(bundle)
    reasons = sorted({e["detail"]["reason"] for e in events if e["kind"] == "payment_rejected"})
    assert reasons == ["above_listed_price", "asset_not_listed", "network_not_listed", "no_listing",
                       "pay_to_not_listed", "second_payment_for_request"]
    # The bundle verifies: its files match its manifest, and its result replays from its events.
    assert verify_bundle(bundle) == []


def test_the_default_ledger_fails_the_same_drill_on_purpose(tmp_path):
    _, result, stages = _run("fair_offer_drill_weak_ledger", tmp_path)
    assert result.verdict == "failed"
    assert stages["control_paid"].status == "passed"
    failed = sorted(name for name, s in stages.items() if s.status == "failed")
    assert failed == sorted(["price_switch_capped", "lookalike_asset_refused", "pay_to_swap_refused",
                             "network_switch_refused", "instructions_not_paid", "slow_responder_paid_once",
                             "unsettled_200_caught", "ambiguous_error_paid_once"])


def test_the_market_keeps_the_sheets_shares_and_fairoffer_refuses_every_bad_offer(tmp_path):
    spec = load_scenario_file(str(SCENARIOS / "fair_offer_market.yaml"))
    market = next(a for a in spec.agents if a.role == "x402_market")
    found = fo.market_listings(market.name, dict(market.config))
    kinds = {k: sum(1 for i in found if i["kind"] == k) for k in fo.MARKET_KINDS}
    # A tenth of 5,491 offers judged on price: 459 asked more (113 at least double, 27 at least ten times), 158
    # named another pay-to.
    assert len(found) == 549
    assert kinds["at_least_ten_times"] == 3 and kinds["at_least_ten_times"] + kinds["at_least_double"] == 11
    assert sum(kinds[k] for k in ("asked_more", "at_least_double", "at_least_ten_times")) == 46
    assert kinds["other_pay_to"] == 16 and kinds["honest"] == 487
    # Spread through the listings, not bunched at one end.
    assert {i["kind"] for i in found[:60]} >= {"honest", "asked_more"}
    _, result, stages = _run("fair_offer_market", tmp_path)
    assert result.verdict == "passed"
    assert stages["honest_offers_paid"].note == "all 487 honest offers paid at their listed price"
    _, weak, weak_stages = _run("fair_offer_market_weak_ledger", tmp_path)
    assert weak.verdict == "failed" and weak_stages["overcharges_refused"].note == "46 of 46 offers above their listing settled"


def test_each_judged_payment_is_an_evidence_record_in_nandatowns_own_form(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import nandatown as nt

    schema_path = Path(nt.__file__).resolve().parents[2] / "schemas" / "evidence_record.schema.json"
    if not schema_path.exists():
        from nandatown.records import EvidenceRecord

        schema = EvidenceRecord.model_json_schema()
    else:
        schema = json.loads(schema_path.read_text())
    bundle, _, _ = _run("fair_offer_drill", tmp_path)
    records = fo.evidence_records(_events(bundle))
    assert records and {r.observer for r in records} == {"fairoffer.v1"}
    for r in records:
        jsonschema.validate(r.model_dump(), schema)
    results = {(r.test, r.result) for r in records}
    assert ("offer_matches_listing", "passed") in results and ("pay_to_not_listed", "failed") in results
    swap = next(r for r in records if r.test == "pay_to_not_listed")
    assert swap.subject == "pay-to-swap" and swap.capability == "pay-to-swap" and swap.evidence


def test_fairoffer_is_a_drop_in_for_the_bundled_marketplace(tmp_path):
    # Escrow releases go to listed sellers, and no marketplace payment carries an x402 memo, so the bundled
    # scenario's verdict is the same with either payments layer.
    _, plain = run_lab("marketplace", str(tmp_path / "plain"))
    _, swapped = run_lab("marketplace", str(tmp_path / "runs"), layer_overrides={"payments": "fairoffer.v1"})
    assert swapped.verdict == plain.verdict == "passed"
    assert [(s.name, s.status) for s in swapped.stages] == [(s.name, s.status) for s in plain.stages]


def test_a_memo_without_x402_terms_is_ledger_v1s_and_an_idempotent_retry_moves_nothing(tmp_path):
    spec = load_scenario_file(str(SCENARIOS / "fair_offer_drill.yaml"))
    engine = build_engine(spec)
    engine.run()
    ledger = engine.layers["payments"]
    before = ledger.balance("buyer")
    ledger.transfer("buyer", "anyone", 5, "a plain memo")
    assert ledger.balance("buyer") == before - 5
    paid = next(iter(ledger.settled.values()))
    memo = fo.memo_of({"resource": paid["resource"], "request": paid["request"], "nonce": paid["nonce"],
                       "asset": fo.USDC_BASE, "network": fo.BASE})
    ledger.transfer("buyer", paid["to"], paid["cents"], memo)
    assert ledger.balance("buyer") == before - 5
    assert engine.events[-1].kind == "payment_duplicate_ignored"
    with pytest.raises(fo.PaymentError, match="no_listing"):
        ledger.transfer("buyer", "anyone", 1, fo.memo_of({"resource": "nothing-listed", "request": "r", "nonce": 1}))
    with pytest.raises(fo.PaymentError, match="payee_not_listed"):
        ledger.hold("buyer", 3, "order-x")
        ledger.release("order-x", "nobody")
    assert ledger.settlement("no-such-request") is None
