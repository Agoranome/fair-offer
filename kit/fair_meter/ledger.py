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

"""FO-10, the book is open: a receipts ledger that only grows, with one Merkle root a month and a proof per receipt.

Only settled receipts go in (a payment with a transaction on the chain); a metered call that charged nothing has
no transaction and no entry. Nothing is ever edited or removed. Each month's root is published, and each receipt's
proof folds into it, so a buyer can match its own payments against the book, and an outside inspector that copies
the roots (Agoranome's inspector: `anchor-lab`, then `verify-receipt`) can show a past month was never changed.

The construction is Agoranome's lab ledger's, so the same tools check both:
  leaf = sha256(0x00 || canonical_json({receipt_id, payer, amount_atomic, tx_hash}))
  node = sha256(0x01 || min(a, b) || max(a, b))    (sorted pairs, no positions)
  Leaves are sorted; an odd node is promoted unchanged to the next level.
`canonical_json` is JSON with sorted keys and no spaces; `amount_atomic` is an integer; EVM addresses lower case.
"""

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LEAF = "sha256(0x00 || canonical_json({receipt_id, payer, amount_atomic, tx_hash}))"
NODE = "sha256(0x01 || min(a, b) || max(a, b)); leaves sorted; an odd node is promoted"
FIELDS = ("receipt_id", "payer", "amount_atomic", "tx_hash")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    amount_atomic INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    period TEXT NOT NULL,
    settled_at TEXT NOT NULL,
    resource TEXT,
    delivery_sha256 TEXT
);
CREATE TRIGGER IF NOT EXISTS receipts_never_change BEFORE UPDATE ON receipts
BEGIN SELECT RAISE(ABORT, 'the book only grows'); END;
CREATE TRIGGER IF NOT EXISTS receipts_never_leave BEFORE DELETE ON receipts
BEGIN SELECT RAISE(ABORT, 'the book only grows'); END;
"""


def _h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _address(text: str) -> str:
    text = (text or "").strip()
    return text.lower() if text.lower().startswith("0x") else text


def leaf_hash(entry: Dict[str, Any]) -> str:
    fields = {k: entry[k] for k in FIELDS}
    return "0x" + _h(b"\x00" + json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hex()


def _node(a: bytes, b: bytes) -> bytes:
    lo, hi = sorted((a, b))
    return _h(b"\x01" + lo + hi)


def _levels(leaves: List[str]) -> List[List[bytes]]:
    level = sorted(bytes.fromhex(x[2:]) for x in leaves)
    levels = [level]
    while len(level) > 1:
        nxt = [_node(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        levels.append(nxt)
        level = nxt
    return levels


def merkle_root(leaves: List[str]) -> Optional[str]:
    return "0x" + _levels(leaves)[-1][0].hex() if leaves else None


def merkle_proof(leaves: List[str], leaf: str) -> List[str]:
    levels = _levels(leaves)
    index = levels[0].index(bytes.fromhex(leaf[2:]))
    proof = []
    for level in levels[:-1]:
        if index ^ 1 < len(level):
            proof.append("0x" + level[index ^ 1].hex())
        index //= 2
    return proof


def verify_proof(leaf: str, proof: List[str], root: str) -> bool:
    node = bytes.fromhex(leaf[2:])
    for sibling in proof:
        node = _node(node, bytes.fromhex(sibling[2:]))
    return "0x" + node.hex() == (root or "").lower()


class Ledger:
    """A seller's receipts ledger in one SQLite file (":memory:" for tests)."""

    def __init__(self, path: str = "receipts.db"):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def record(self, receipt_id: str, *, payer: str, amount_atomic: int, tx_hash: str,
               settled_at: Optional[datetime] = None, resource: Optional[str] = None,
               delivery_sha256: Optional[str] = None) -> bool:
        """Add a settled receipt. True when added; False when the same receipt is already in (a retried call).
        A different receipt under the same id is refused: the book never changes an entry."""
        if not tx_hash or int(amount_atomic) <= 0:
            raise ValueError("only a settled payment, with its transaction, goes in the book")
        moment = (settled_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        row = (receipt_id, _address(payer), int(amount_atomic), tx_hash.lower(), moment.strftime("%Y-%m"),
               moment.isoformat(), resource, delivery_sha256)
        with self._lock:
            held = self._db.execute("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
            if held is not None:
                if (held["payer"], held["amount_atomic"], held["tx_hash"]) != row[1:4]:
                    raise ValueError(f"receipt {receipt_id} is already in the book with other figures")
                return False
            with self._db:
                self._db.execute("INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
        return True

    def _month(self, period: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM receipts WHERE period = ? ORDER BY receipt_id", (period,))]

    def periods(self) -> List[str]:
        return [r[0] for r in self._db.execute("SELECT DISTINCT period FROM receipts ORDER BY period")]

    def view(self) -> Dict[str, Any]:
        """The public ledger page: one root a month, how many receipts it holds and what they total."""
        months = []
        for period in self.periods():
            rows = self._month(period)
            months.append({"period": period, "receipts": len(rows),
                           "settled_atomic": str(sum(r["amount_atomic"] for r in rows)),
                           "merkle_root": merkle_root([leaf_hash(r) for r in rows])})
        return {"months": months, "merkle": {"leaf": LEAF, "node": NODE},
                "how": "Ask for a receipt's proof, hash its entry into the leaf, fold the proof and compare with the "
                       "month's merkle_root."}

    def proof(self, receipt_id: str) -> Optional[Dict[str, Any]]:
        """A receipt's entry, leaf and proof against its month's root, with the delivery hash beside it (outside
        the leaf)."""
        row = self._db.execute("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if row is None:
            return None
        leaves = [leaf_hash(r) for r in self._month(row["period"])]
        entry = {k: row[k] for k in FIELDS}
        leaf = leaf_hash(entry)
        return {"period": row["period"], "entry": entry, "leaf": leaf, "proof": merkle_proof(leaves, leaf),
                "merkle_root": merkle_root(leaves), "delivery_sha256": row["delivery_sha256"]}

    def matches(self, payer: str) -> List[Dict[str, Any]]:
        """Every receipt one buyer paid, so it can match its own payments against the book."""
        return [{k: r[k] for k in (*FIELDS, "period")} for r in self._db.execute(
            "SELECT * FROM receipts WHERE payer = ? ORDER BY settled_at", (_address(payer),))]
