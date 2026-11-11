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

"""FO-3, receipts are signed and (0.4) bound to what was delivered.

A receipt says what was bought, from whom, for how much and when, and names the SHA-256 of the RFC 8785 form of
the result it came with. It is signed with an EVM key (EIP-191 personal_sign over the receipt's RFC 8785 bytes),
so any EIP-191 library checks it and the signer can be the seller's pay-to address. A metered receipt (FO-9) also
names the ceiling the buyer signed for and the units delivered.

Signing needs `eth-account` (installed with the x402 SDK, or `pip install fair-meter[eth]`).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fair_meter.jcs import canonical, delivery

SIGNATURE_SCHEME = "eip191-jcs"


def build(*, receipt_id: str, resource: str, network: str, asset: str, pay_to: str, amount_atomic: int,
          result: Any, payer: Optional[str] = None, issued_at: Optional[str] = None,
          ceiling_atomic: Optional[int] = None, units: Optional[int] = None,
          unit: Optional[str] = None) -> Dict[str, Any]:
    """The receipt for one paid result. Amounts are strings of the asset's smallest units."""
    receipt: Dict[str, Any] = {
        "receipt_id": receipt_id, "resource": resource, "network": network, "asset": asset,
        "pay_to": pay_to.lower() if pay_to.lower().startswith("0x") else pay_to,
        "amount_atomic": str(int(amount_atomic)),
        "issued_at": issued_at or datetime.now(timezone.utc).isoformat(),
        "delivery": delivery(result),
    }
    if payer:
        receipt["payer"] = payer.lower() if payer.lower().startswith("0x") else payer
    if ceiling_atomic is not None:
        if int(amount_atomic) > int(ceiling_atomic):
            raise ValueError("a metered charge is never more than the ceiling the buyer signed (FO-9)")
        receipt["ceiling_atomic"] = str(int(ceiling_atomic))
        if units is not None:
            receipt["units"] = int(units)
        if unit:
            receipt["unit"] = unit
    return receipt


def sign(receipt: Dict[str, Any], private_key: Any) -> Dict[str, Any]:
    """The signature block for a receipt, by an EVM key (a hex string or an eth-account LocalAccount)."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    account = private_key if hasattr(private_key, "sign_message") else Account.from_key(private_key)
    signed = account.sign_message(encode_defunct(primitive=canonical(receipt)))
    return {"scheme": SIGNATURE_SCHEME, "signer": account.address.lower(),
            "value": "0x" + bytes(signed.signature).hex()}


def attach(result: Any, receipt: Dict[str, Any], private_key: Any) -> Dict[str, Any]:
    """The paid body: the result, its receipt and the receipt's signature."""
    return {"result": result, "receipt": receipt, "signature": sign(receipt, private_key)}


def verify(body: Dict[str, Any], signer: Optional[str] = None) -> bool:
    """A paid body's receipt is signed by its stated signer (and by `signer`, when given), and bound to its
    result. What a buyer runs on what it received."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    receipt, signature = body.get("receipt") or {}, body.get("signature") or {}
    if signature.get("scheme") != SIGNATURE_SCHEME or not bound(body.get("result"), receipt):
        return False
    try:
        found = Account.recover_message(encode_defunct(primitive=canonical(receipt)),
                                        signature=str(signature.get("value", "")))
    except Exception:
        return False
    claimed = str(signature.get("signer") or "").lower()
    return found.lower() == claimed and (signer is None or claimed == signer.lower())


def bound(result: Any, receipt: Dict[str, Any]) -> bool:
    """The receipt names the hash of this very result."""
    return (receipt.get("delivery") or {}).get("sha256") == delivery(result)["sha256"]
