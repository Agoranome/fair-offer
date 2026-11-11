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

"""RFC 8785 (JCS): the one canonical form of a JSON value, and the delivery hash FO-3 (0.4) names.

A receipt bound to what was delivered names `sha256(jcs(result))`. Any JCS library in any language gives the same
bytes for the JSON the buyer received, so a buyer can show what it was sold and anyone can recompute the hash.
"""

import hashlib
import json
import math
from decimal import Decimal
from typing import Any, Dict

DELIVERY_HASH = "sha256-jcs"
DELIVERY_OF = "the RFC 8785 (JCS) form of result"


def _number(value: Any) -> str:
    """An ECMAScript Number's text (RFC 8785 section 3.2.2.3)."""
    if isinstance(value, int) and abs(value) < 2 ** 53:
        return str(value)
    x = float(value)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("JSON has no NaN or Infinity")
    if x == 0:
        return "0"
    if x < 0:
        return "-" + _number(-x)
    _, digits, exp = Decimal(repr(x)).as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    d = "".join(map(str, digits))
    k, n = len(d), exp + len(d)
    if k <= n <= 21:
        return d + "0" * (n - k)
    if 0 < n <= 21:
        return d[:n] + "." + d[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + d
    e = n - 1
    return (d if k == 1 else d[0] + "." + d[1:]) + "e" + ("+" if e >= 0 else "-") + str(abs(e))


def _jcs(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_jcs(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted((str(k) for k in value), key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _jcs(value[k]) for k in keys) + "}"
    raise TypeError(f"not JSON: {type(value).__name__}")


def canonical(value: Any) -> bytes:
    """The RFC 8785 bytes of a JSON value (dicts, lists, strings, numbers, booleans and None)."""
    return _jcs(value).encode("utf-8")


def delivery(result: Any) -> Dict[str, str]:
    """The receipt's `delivery` block for the result it comes with."""
    return {"sha256": "0x" + hashlib.sha256(canonical(result)).hexdigest(), "of": DELIVERY_OF}
