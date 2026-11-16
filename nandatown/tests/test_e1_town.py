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

"""x402-offer, the stub seller and the adapter against NandaTown itself (pip install the commit named in
../README.md). These pin what E1 found at that commit: NandaTown reads x402-offer's bundle, and its own agent test
scores the honest stub and all five defects alike, without reading the offer."""

import json
import sys
from pathlib import Path

import pytest

nandatown = pytest.importorskip("nandatown")

from nandatown.bundle import load_bundle, verify_bundle  # noqa: E402
from nandatown.path_runner import run_path_test  # noqa: E402
from nandatown.records import EvidenceRecord  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import town_adapter  # noqa: E402
import x402_offer as xo  # noqa: E402
import x402_stub as stub  # noqa: E402

TOWN_PROFILE = "a2a-capability-fulfillment@0.3"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    # NandaTown keeps an operator key under NANDATOWN_HOME.
    monkeypatch.setenv("NANDATOWN_HOME", str(tmp_path / "home"))


@pytest.fixture
def serve():
    servers = []

    def start(defect="honest", shape="town"):
        server, seller, base = stub.serve(defect, shape=shape)
        servers.append(server)
        return seller, base

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_nandatown_reads_the_bundle_and_has_no_evaluator_to_replay(tmp_path, serve):
    _, base = serve()
    directory, result = xo.run_check(base, stub.SKILL, out_dir=str(tmp_path / "runs"), repeat_delay=0)
    assert result["verdict"] == "passed"
    # Every hash and binding holds for NandaTown; the one thing it can't do is replay an evaluator it doesn't have.
    assert verify_bundle(directory) == [f"unsupported path evaluator {xo.EVALUATOR!r}"]
    bundle = load_bundle(directory)
    assert bundle["profile"].ref == xo.PROFILE_REF
    assert bundle["result"].verdict == "passed"
    assert [s.name for s in bundle["result"].stages] == xo.STAGE_ORDER
    records = json.loads(Path(directory, "evidence.json").read_text())["records"]
    assert [EvidenceRecord.model_validate(r).test for r in records] == xo.STAGE_ORDER


def test_nandatowns_agent_test_scores_every_variant_alike(tmp_path, serve):
    outcomes = {}
    for variant in stub.VARIANTS:
        _, base = serve(variant)
        bundle, result = run_path_test(base, str(tmp_path / variant), profile_ref=TOWN_PROFILE)
        assert verify_bundle(bundle) == []
        events = Path(bundle, "events.jsonl").read_text()
        assert "x402.payment" not in events and "payTo" not in events  # no stage holds the offer
        outcomes[variant] = (result.verdict, tuple((s.name, s.status) for s in result.stages))
    assert len(set(outcomes.values())) == 1  # the five defects look exactly like the honest seller
    verdict, stages = outcomes["honest"]
    assert verdict == "failed"
    assert [name for name, status in stages if status == "failed"] == ["protocol_invocation"]


def test_the_adapter_lets_nandatown_reach_a_routed_skill(tmp_path, serve):
    _, base = serve("honest", "routed")
    seen = {}
    for mode in town_adapter.MODES:
        adapter = town_adapter.Adapter(base, mode, stub.SKILL if mode == "skill" else None)
        proxy, url = town_adapter.serve(adapter)
        try:
            _, result = run_path_test(url, str(tmp_path / mode), profile_ref=TOWN_PROFILE)
        finally:
            proxy.shutdown()
            proxy.server_close()
        stage = next(s for s in result.stages if s.name == "protocol_invocation")
        seen[mode] = (stage.status, stage.note, [e.get("x402_status") for e in adapter.log if e["method"] == "POST"])
    # As NandaTown posts it, the request never reaches the skill; through the adapter it reaches the skill, which
    # asks to be paid, and NandaTown reads that as a failed invocation.
    assert seen["passthrough"][:2] == ("failed", "a2a_http_status_405")
    assert seen["skill"][0] == "failed" and "observed 'input-required'" in seen["skill"][1]
    assert set(seen["skill"][2]) == {"payment-required"}
