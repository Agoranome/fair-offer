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

"""FO-9, the price follows what was measured: the charge under the buyer's ceiling.

With x402's `upto` scheme the buyer signs a ceiling (a Permit2 authorization of at most that much). After the
work is done the seller settles only what was delivered: units times the one public unit price, never more than
the ceiling, and nothing when nothing was delivered. In the x402 Python SDK (2.25) the route handler tells the
middleware what to settle with the `Settlement-Overrides` response header; an amount of 0 settles nothing.
"""

import json
from decimal import Decimal
from typing import Optional, Tuple

OVERRIDES_HEADER = "Settlement-Overrides"


def unit_atomic(unit_price_usd: str, decimals: int = 6) -> int:
    """The unit price in the asset's smallest units (USDC has 6 decimals): "0.0002" is 200. A price that doesn't
    divide exactly is refused, since every buyer must pay the same for a unit."""
    scaled = Decimal(unit_price_usd) * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value() or scaled < 0:
        raise ValueError(f"{unit_price_usd} is not a whole number of the asset's smallest units")
    return int(scaled)


def charge(units: int, unit_price_atomic: int, ceiling_atomic: int) -> int:
    """What was delivered at the unit price, capped at the ceiling the buyer signed."""
    if units < 0 or unit_price_atomic < 0 or ceiling_atomic < 0:
        raise ValueError("units, prices and ceilings are never negative")
    return min(units * unit_price_atomic, ceiling_atomic)


def overrides(charged_atomic: int) -> Tuple[str, str]:
    """The response header that settles only the measured charge."""
    return OVERRIDES_HEADER, json.dumps({"amount": str(int(charged_atomic))})


def fits(units: int, unit_price_atomic: int, ceiling_atomic: int) -> Optional[int]:
    """How many units the ceiling pays for when `units` would cost more, else None. A seller that would deliver
    more than the buyer signed for delivers this many instead, so the charge always matches the delivery."""
    if unit_price_atomic and units * unit_price_atomic > ceiling_atomic:
        return ceiling_atomic // unit_price_atomic
    return None
