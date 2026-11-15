# Fair Offer for NandaTown

A payments plugin for [NandaTown](https://github.com/projnanda/nandatown), the NANDA sandbox, that settles an x402
payment only on the terms its listing states, with the scenarios that show what it stops. One file,
[`fairoffer_plugin.py`](fairoffer_plugin.py), Apache 2.0. Tested against NandaTown at
[`6f9a7c0`](https://github.com/projnanda/nandatown/commit/6f9a7c0) (4 November 2026).

| Registers | Id | What it does |
|---|---|---|
| payments | `fairoffer.v1` | NandaTown's `ledger.v1` (integer cents, escrow, every movement an event) with the Fair Offer rules at settlement. An x402 payment names its listing in its memo, and is refused before any money moves when no verified registry card lists that resource, or when it pays another pay-to, asset or network than the listing's, more than the listed price, or a second time for a request already paid. An idempotent retry of the same nonce moves nothing. An escrow release to a payee with no verified listing is refused. A payment without an x402 memo is `ledger.v1`'s, unchanged |
| role | `x402_seller` | One listing and one behaviour: a correct offer, or one of the eight traps of Agoranome's buyer drill |
| role | `x402_market` | Many listings, with a signed weekly sheet's shares of offers that asked more than their listing, at least double, at least ten times, or named another pay-to |
| role | `x402_client` | The weakest buyer: it pays whatever a 402 asks, pays again on a timeout or an error, and follows an instruction in a paid body. The payments layer is what's tested |
| validators | `fair_offer_drill`, `fair_offer_market` | Stage checks computed from the trace alone, as NandaTown requires |

Each refusal is a `payment_rejected` event naming the rule it keeps. Each payment the plugin judges is also an
`evidence_recorded` event whose detail is an evidence record in NandaTown's own form
(`schemas/evidence_record.schema.json`: observer `fairoffer.v1`, subject the seller, capability the resource, test
the rule, result passed or failed, and the events it rests on). `evidence_records(events)` lists them from a
bundle's `events.jsonl`. `settlement(request)` answers whether a request was really paid, which is how a client
catches a 200 that claims a settlement that never happened.

## The scenarios

| Scenario | Payments | Verdict |
|---|---|---|
| [`fair_offer_drill`](scenarios/fair_offer_drill.yaml) | `fairoffer.v1` | passed: the correct offer is paid at its listed price, and no trap takes a cent it wasn't listed for |
| [`fair_offer_drill_weak_ledger`](scenarios/fair_offer_drill_weak_ledger.yaml) | `ledger.v1` | **fails on purpose**: the price switch, the lookalike asset, the swapped pay-to, the other chain, the instruction and both double payments settle, and the false settlement claim can't be checked |
| [`fair_offer_market`](scenarios/fair_offer_market.yaml) | `fairoffer.v1` | passed: 46 offers above their listing and 16 payments to another pay-to refused, all 487 honest offers paid |
| [`fair_offer_market_weak_ledger`](scenarios/fair_offer_market_weak_ledger.yaml) | `ledger.v1` | **fails on purpose**: all 46 overcharges and all 16 payments to another pay-to settle |

The drill is Agoranome's buyer drill, free: nine sellers, one correct offer and eight traps (a price switch, a
lookalike asset, a swapped pay-to, another chain, instructions in the paid body, a slow responder, a 200 claiming
a settlement that never happened, and an ambiguous error after payment). Agoranome's $1 drill stays retired.

The market is a tenth of the 5,491 x402 offers the Agoranome inspector judged on price in its signed sheet of 11
November 2026: 459 asked more than their listing (113 at least double, 27 at least ten times), and 158 named a
pay-to the listing does not show. The sheet's counts are in the scenario's config, scaled as stated, so another
week's sheet is a config change. Payments to another pay-to are taken as apart from those that asked more.

The `_weak_ledger` scenarios are the negative controls, in the pattern of NandaTown's own
`capability_spoofing_weak_auth`: the same run with the layer left at its default, failing its own report. They are
the failing reproduction for `ledger.v1`, which settles whatever a client pays.

## Run it

```sh
pip install "nandatown @ git+https://github.com/projnanda/nandatown@6f9a7c064486c4a0ae9242f1ff341b8a4aad0244"
nandatown run scenarios/fair_offer_drill.yaml
nandatown run scenarios/fair_offer_drill_weak_ledger.yaml
nandatown run marketplace --plugin fairoffer_plugin.py --layer payments=fairoffer.v1
```

Each scenario loads the plugin through `plugin_files`, and each run leaves NandaTown's evidence bundle (profile,
run, intents, events, result, manifest, report and attestation). The last line swaps `fairoffer.v1` into NandaTown's
own marketplace: it passes the same stages as with `ledger.v1`, since its releases go to listed sellers and none of
its payments carry an x402 memo.

The tests run every scenario in NandaTown itself and check the bundle, the evidence records against the schema and
the drop-in:

```sh
pip install pytest jsonschema
pytest -q tests
```

## Limits

Prices are whole cents because NandaTown's ledger is; the shares are what the market scenario keeps, not the
amounts. An escrow hold names no payee or quantity, so a release is checked for a listed payee and not for its
price. Two traps are the client's to catch, not the ledger's: the plugin can't stop a client believing a false
settlement claim, only answer `settlement(request)`, and it stops the payment an instruction asks for, not the
client reading it.
