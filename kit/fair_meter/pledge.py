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

"""FO-7, promises are verifiable: a public share of what a seller earns, paid once a month and proven in the open.

A seller that promises a share of its revenue to a cause publishes a pledge ledger in the standard's shape
(schema/pledge-ledger-0.1.schema.json): its terms, a proof URL per receipt, and month by month the Merkle root of the
receipts settled, the basis, the share, the recipient and the transfers made. `ledger_doc` builds that page from the
kit's receipts ledger (fair_meter.ledger), so the roots are the same ones the ledger publishes.

What a month owes is the share of its basis, rounded up to the next atomic unit, so the cause never loses a fraction.
Small sums follow the rule `carry-1`, Agoranome's: a month that owes under $1 carries over and shows as owed, and
nothing is carried past the end of a quarter. `standing` runs it over the months as a running balance of what was
owed less what was given: "kept", "carried" (under $1 left, inside the quarter) or "owed" (a transfer is due).

The transfers themselves are USDC payments on Base to the recipient; a buyer, or Agoranome's inspector, reads each
transaction on the chain. This module states the figures; it moves no money.
"""

import math
from typing import Any, Dict, Iterable, List, Optional

from fair_meter.ledger import LEAF, Ledger

KIND = "fair-offer.pledge-ledger/1"
GRACE_DAYS = 45
CARRY_UNDER = 1_000_000      # $1 in USDC's six decimals
CARRY_RULE = "carry-1"
# The schema's own words for the same construction as fair_meter.ledger's LEAF and NODE.
SCHEMA_NODE = "sha256(0x01 || min(a, b) || max(a, b)); leaves sorted, an odd node carried up unchanged"


def owed(basis_atomic: int, share_bps: int) -> int:
    """The share of `basis_atomic`, in basis points, rounded up to the next atomic unit."""
    if not 0 < share_bps <= 10_000:
        raise ValueError("share_bps must be between 1 and 10000")
    return -(-int(basis_atomic) * share_bps // 10_000)


def quarter_ends(period: str) -> bool:
    return int(period[5:7]) % 3 == 0


def standing(months: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """carry-1 over months given as {period, owed_atomic, given_atomic}, in period order."""
    balance, out = 0, []
    for m in sorted(months, key=lambda m: m["period"]):
        balance += int(m["owed_atomic"]) - int(m["given_atomic"])
        state = ("kept" if balance <= 0 else
                 "carried" if balance < CARRY_UNDER and not quarter_ends(m["period"]) else "owed")
        out.append({"period": m["period"], "owed_atomic": str(m["owed_atomic"]),
                    "given_atomic": str(m["given_atomic"]), "balance_atomic": str(max(balance, 0)),
                    "standing": state})
    return out


def ledger_doc(book: Ledger, *, share_bps: int, proof_url: str, recipient: Optional[str] = None,
               basis: str = "settled revenue", transfers: Optional[Dict[str, List[Dict[str, Any]]]] = None
               ) -> Dict[str, Any]:
    """The pledge ledger page (FO-7) from the receipts ledger. `transfers` maps a month to the transfers made for it,
    each {tx_hash, amount_atomic}: what the seller sent the recipient, which anyone can read on the chain."""
    if "{receipt_id}" not in proof_url:
        raise ValueError("proof_url must hold {receipt_id}, where each receipt's proof answers")
    if recipient is not None and not (len(recipient) == 42 and recipient.lower().startswith("0x")):
        raise ValueError("recipient must be the cause's address on Base")
    share = share_bps / 10_000
    transfers = transfers or {}
    rows = []
    for month in book.view()["months"]:
        basis_atomic = int(month["settled_atomic"])
        sent = transfers.get(month["period"]) or []
        rows.append({"period": month["period"], "merkle_root": month["merkle_root"], "receipts": month["receipts"],
                     "basis_atomic": str(basis_atomic), "share": share,
                     "recipient": recipient.lower() if recipient else None,
                     "donations": [t["tx_hash"].lower() for t in sent],
                     "owed_atomic": str(owed(basis_atomic, share_bps)),
                     "given_atomic": str(sum(int(t["amount_atomic"]) for t in sent))})
    states = {r["period"]: r for r in standing(rows)}
    for r in rows:
        r["standing"] = states[r["period"]]["standing"]
    return {"kind": KIND, "terms": {"share": share, "basis": basis,
                                    "recipient": recipient.lower() if recipient else None},
            "proof": proof_url, "leaf": LEAF, "node": SCHEMA_NODE,
            "grace_days": GRACE_DAYS, "small_sums": CARRY_RULE, "months": rows}


def kept(month: Dict[str, Any], transferred_atomic: int) -> bool:
    """Whether a month's transfers (as read on the chain) reach its share of the basis (the schema's check)."""
    share_bps = round(float(month["share"]) * 10_000)
    return transferred_atomic >= owed(int(month["basis_atomic"]), share_bps)


def at_bar(cost_usd: float, share_bps: int) -> float:
    """What a seller must earn a month to cover `cost_usd` after the share: cost / (1 - share)."""
    return math.ceil(cost_usd / (1 - share_bps / 10_000) * 100) / 100
