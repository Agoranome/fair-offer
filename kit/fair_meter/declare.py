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

"""The Fair Offer 0.4 block: what a route declares in its x402 402, under the extension key `fair-offer`.

Declare only what the server really does. Each field names a rule a buyer can check:
FO-2 settlement, FO-3 receipts, FO-4 sample, FO-5 listing price, FO-6 data not instructions, FO-7 pledge,
FO-8 one offer, FO-9 metered, FO-10 book. The block must be the same in the unpaid 402 and on the paid retry, so it
holds no counts or timestamps.
"""

import re
from typing import Any, Dict, Optional

VERSION = "0.4"
EXTENSION_KEY = "fair-offer"
STANDARD = "https://agoranome.com/standard"
SCHEMA = "https://agoranome.com/standard/fair-offer-0.4.schema.json"
METERED_SCHEME = "upto"

_UNIT = re.compile(r"^[a-z][a-z_]{0,31}$")
_PRICE = re.compile(r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$")
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BASES = ("settled_revenue", "gross_revenue", "profit")


def _url(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 2048 or not (text.startswith("https://") or text.startswith("/")):
        raise ValueError(f"{name} must be an https URL or a path relative to the resource URL")
    return text


def metered(unit: str, unit_price_usd: str) -> Dict[str, str]:
    """FO-9: one public unit price, the same at every size and for every buyer, charged under x402's `upto`."""
    if not _UNIT.match(unit or ""):
        raise ValueError("the unit is a lower-case word, such as row, page or token")
    if not _PRICE.match(unit_price_usd or ""):
        raise ValueError("the unit price is a decimal string in dollars, such as \"0.0002\"")
    return {"unit": unit, "unit_price_usd": unit_price_usd, "scheme": METERED_SCHEME}


def block(*, failed_calls_enforced_by: str = "seller", signer: Optional[str] = None,
          receipts_verify: Optional[str] = None, sample: Optional[str] = None,
          listing_price_matches_offer: bool = True, data_not_instructions: bool = True, one_offer: bool = True,
          meter: Optional[Dict[str, str]] = None, pledge_share: Optional[float] = None,
          pledge_basis: str = "settled_revenue", pledge_ledger: Optional[str] = None,
          ledger: Optional[str] = None, reports: Optional[str] = None,
          standard: str = STANDARD) -> Dict[str, Any]:
    """The block's info. `signer` is the address that signs receipts (FO-3); `meter` comes from `metered()` and is
    given only on a route that offers `upto` (FO-9); `ledger` and `reports` are the public receipts ledger and the
    buyers' report listing (FO-10)."""
    if failed_calls_enforced_by not in ("seller", "rail"):
        raise ValueError("failed calls are enforced by the seller or by the rail")
    settlement: Dict[str, Any] = {"failed_calls": "not_charged", "enforced_by": failed_calls_enforced_by}
    if failed_calls_enforced_by == "rail":
        settlement["capture_on"] = "2xx"
    receipts: Dict[str, Any] = {"signed": True, "delivery_hash": "sha256-jcs"}
    if signer is not None:
        if not _ADDRESS.match(signer):
            raise ValueError("the signer is a 0x address")
        receipts["signer"] = signer
    if receipts_verify:
        receipts["verify"] = _url(receipts_verify, "receipts_verify")
    info: Dict[str, Any] = {"version": VERSION, "standard": standard, "settlement": settlement,
                            "receipts": receipts, "listing_price_matches_offer": bool(listing_price_matches_offer),
                            "data_not_instructions": bool(data_not_instructions), "one_offer": bool(one_offer)}
    if sample:
        info["sample"] = {"url": _url(sample, "sample")}
    if meter is not None:
        info["metered"] = metered(meter["unit"], meter["unit_price_usd"])
    if pledge_share is not None:
        if not 0 < float(pledge_share) <= 1 or pledge_basis not in _BASES or not pledge_ledger:
            raise ValueError("a pledge names a share in (0, 1], a basis and its public ledger (FO-7)")
        info["pledge"] = {"share": pledge_share, "basis": pledge_basis,
                          "ledger": _url(pledge_ledger, "pledge_ledger")}
    if ledger:
        info["book"] = {"ledger": _url(ledger, "ledger")}
        if reports:
            info["book"]["reports"] = _url(reports, "reports")
    elif reports:
        raise ValueError("the book names its receipts ledger before its reports (FO-10)")
    return info


def extension(info: Dict[str, Any]) -> Dict[str, Any]:
    """The entry for a route's x402 `extensions`: the info, and the schema by reference."""
    return {EXTENSION_KEY: {"info": info, "schema": {"$ref": SCHEMA}}}
