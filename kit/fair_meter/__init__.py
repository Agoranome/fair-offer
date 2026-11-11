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

"""The fair meter kit: what an x402 seller needs to keep the Fair Offer Standard 0.4.

- `declare`: the fair-offer block for the 402 (every rule it names, FO-2 to FO-10).
- `meter`: FO-9, the charge under the buyer's ceiling with x402's `upto`.
- `receipt`: FO-3, a signed receipt bound to the hash of what was delivered.
- `guard`: FO-6, a check that a response carries no instructions aimed at the buying agent.
- `ledger`: FO-10, a receipts ledger that only grows, with monthly Merkle roots and a proof per receipt.
- `pledge`: FO-7, a public share paid monthly: the pledge ledger page from the receipts ledger, and the carry-1 rule
  for small sums.
- `jcs`: RFC 8785, the canonical form the delivery hash is taken over.

No dependencies beyond the standard library; signing receipts uses `eth-account`.
"""

__version__ = "0.2.0"
STANDARD_VERSION = "0.4"
