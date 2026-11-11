# fair-meter: the Fair Offer kit for x402 sellers

What a seller needs to keep the [Fair Offer Standard 0.4](https://agoranome.com/standard), in one small Python
package with no required dependencies:

| Module | Rule | What it does |
| --- | --- | --- |
| `fair_meter.declare` | FO-2 to FO-10 | Builds the `fair-offer` block for your 402, refusing anything the 0.4 schema doesn't allow. |
| `fair_meter.meter` | FO-9 | The charge under the buyer's ceiling with x402's `upto`: units times one public unit price, never more than the ceiling, nothing when nothing was delivered. |
| `fair_meter.receipt` | FO-3 | A receipt bound to the SHA-256 of the RFC 8785 form of the result, signed with an EVM key (EIP-191). |
| `fair_meter.guard` | FO-6 | Finds text that reads as an instruction to the buying agent in any string of a response, however it is hidden. |
| `fair_meter.ledger` | FO-10 | A receipts ledger that only grows, one Merkle root a month, a proof per receipt. |
| `fair_meter.jcs` | | RFC 8785, the canonical JSON the delivery hash is taken over. |

Signing receipts needs `eth-account`, which the x402 SDK already installs. Licence: Apache-2.0 (the standard's
text, schema and examples in the rest of this repository are CC BY 4.0).

## Install

From this repository until it is published:

```
pip install "git+https://github.com/Agoranome/fair-offer.git#subdirectory=kit"
```

## A metered route with the x402 SDK (Python, 2.25)

Register x402's `upto` scheme beside `exact` (the facilitator must list `upto` on your network), offer the metered
route with both, and declare the block:

```python
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.mechanisms.evm.upto import UptoEvmServerScheme
from fair_meter import declare

server.register(NETWORK, ExactEvmServerScheme())
server.register(NETWORK, UptoEvmServerScheme())

BLOCK = declare.extension(declare.block(
    signer=RECEIPT_ADDRESS,                       # the address that signs receipts
    sample="/v1/rows/sample",
    meter=declare.metered("row", "0.0002"),       # one public price a row (FO-9)
    ledger="/receipts/ledger", reports=None,      # the open book (FO-10)
))
# On the route's config: both options at the same price (for upto it is the ceiling), and extensions=BLOCK.
```

In the handler, guard the result, settle only what was delivered, sign the receipt and put it in the book once the
payment settles:

```python
from fair_meter import guard, meter, receipt

UNIT = meter.unit_atomic("0.0002")               # 200 units of USDC

async def rows(request):
    requirements = request.state.payment_requirements
    found = guard.guard(lookup(request))          # FO-6: refuse to send instructions; the failed call isn't settled
    charged = len(found["rows"]) * UNIT
    if requirements.scheme == "upto":
        charged = meter.charge(len(found["rows"]), UNIT, int(requirements.amount))
    paid = receipt.build(receipt_id=new_id(), resource=str(request.url), network=requirements.network,
                         asset=requirements.asset, pay_to=requirements.pay_to, amount_atomic=charged,
                         result=found, ceiling_atomic=int(requirements.amount), units=len(found["rows"]),
                         unit="row")
    response = JSONResponse(receipt.attach(found, paid, RECEIPT_KEY))
    if requirements.scheme == "upto":
        name, value = meter.overrides(charged)   # the middleware settles this amount, never the ceiling
        response.headers[name] = value
    return response
```

When the facilitator returns the settlement, record it: `book.record(receipt_id, payer=..., amount_atomic=...,
tx_hash=...)`, and serve `book.view()` at your ledger path and `book.proof(receipt_id)` beside it. A metered call
that charged nothing has no transaction and stays out of the book.

Read the field names on `requirements` from your SDK version; the shape above is the 2.25 Python SDK's.

## What a buyer checks

- `receipt.verify(body)` (or any EIP-191 library over the receipt's RFC 8785 bytes): the receipt is signed by its
  signer and names the hash of the result it came with.
- `agoranome-inspector check-delivery answer.json` recomputes the delivery hash with no trust in the seller.
- The ledger's month roots fold every receipt's proof; Agoranome's inspector can copy them into its open book
  (`anchor-lab`) so a past month can't be changed quietly (`verify-receipt`).
- `https://api.agoranome.com/v1/fair-offer/check?url=` reads your live 402 against your block, free.

## Tests

```
cd kit && pip install -e ".[test]" && pytest
```

The ledger's roots are tested against the construction Agoranome's lab ledger uses, so the same tools check both.
