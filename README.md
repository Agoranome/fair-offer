# The Fair Offer Standard

Ten open rules for selling to AI agents honestly, a machine-readable way to
prove them inside the x402 payment handshake, and an annex of rules for the
directories agents search. Version **0.4** (draft).

Human-readable page: <https://agoranome.com/standard> ·
JSON: [`standard.json`](standard.json) ·
Schema: [`schema/fair-offer-0.4.schema.json`](schema/fair-offer-0.4.schema.json)
(0.3: [`schema/fair-offer-0.3.schema.json`](schema/fair-offer-0.3.schema.json),
0.2: [`schema/fair-offer-0.2.schema.json`](schema/fair-offer-0.2.schema.json)) ·
Pledge ledger: [`schema/pledge-ledger-0.1.schema.json`](schema/pledge-ledger-0.1.schema.json)

## The ten rules

| ID | Rule | What it means | How to check |
|----|------|---------------|--------------|
| FO-1 | **Price before payment** | An unpaid call returns an x402 offer (HTTP 402) that states the price, asset, network and pay-to address. Nothing is charged before the buyer has signed that exact offer. | [check](https://api.agoranome.com/v1/seller/lint) |
| FO-2 | **Failure is free** | A call that fails, times out or returns no result is not settled. The buyer is told so. | self-declared |
| FO-3 | **Receipts are signed** | Every paid result carries a receipt signed by the seller's key: what was bought, for how much, and when, and (0.4) the SHA-256 of the RFC 8785 form of the result it came with, so a receipt is bound to what was delivered. | [check](https://agoranome.com/verify) |
| FO-4 | **Sample before you buy** | A free sample shows what the paid route returns, and a third party can attest that the sample and the paid offer come from the same host. | [check](https://api.agoranome.com/v1/seller/attest) |
| FO-5 | **Honest listing** | The listing's description, input schema and output example match what the route really returns. Edits made to win buyers must stay true. | [check](https://api.agoranome.com/v1/seller/panel) |
| FO-6 | **Data, never instructions** | Responses carry data for the buying agent to use, never instructions aimed at it. A buyer can treat any instruction in a response as an attack. | self-declared |
| FO-7 | **Promises are verifiable** | Any claim about where revenue goes (a pledge, a split, a donation) comes with a public ledger that a buyer can check per receipt, with on-chain proof of what was given. | [check](https://api.agoranome.com/impact/ledger) |
| FO-8 | **Same offer to every buyer** | Every buyer that asks at the same moment gets the same offer: the same price, asset, network and pay-to, whatever client, user agent or headers it sends. A price may change over time, never by who is asking. | [check](https://api.agoranome.com/v1/fair-offer/check?url=) |
| FO-9 | **Price follows what was measured** | A metered offer states its unit and one public unit price, the same at every size and for every buyer. The buyer signs a ceiling (x402's `upto` scheme), is charged only for what was delivered, never more than the ceiling, and nothing when nothing was delivered. The receipt names the charge and the ceiling. | [check](https://api.agoranome.com/v1/fair-offer/check?url=) |
| FO-10 | **The book is open** | The seller's receipts are in a public ledger that only grows, with a proof per receipt, so a buyer can match its own payments against it and anyone can check it. Buyers' reports, each tied to a payment on the chain, are listed and never edited or quietly removed. | [check](https://api.agoranome.com/impact/ledger) |

## Declaring it (0.4)

A seller's HTTP 402 carries an x402 extension, `extensions["fair-offer"]`, whose
`info` states which rules the offer keeps and where a buyer can check each one:

```json
{
  "version": "0.4",
  "standard": "https://agoranome.com/standard",
  "settlement": {
    "failed_calls": "not_charged",
    "enforced_by": "seller"
  },
  "receipts": {
    "signed": true,
    "verify": "https://api.example.com/receipts/verify",
    "delivery_hash": "sha256-jcs"
  },
  "sample": {
    "url": "https://api.example.com/v1/search/sample"
  },
  "listing_price_matches_offer": true,
  "data_not_instructions": true,
  "one_offer": true,
  "metered": {
    "unit": "row",
    "unit_price_usd": "0.0002",
    "scheme": "upto"
  },
  "pledge": {
    "share": 0.15,
    "basis": "settled_revenue",
    "ledger": "https://api.example.com/impact/ledger"
  },
  "book": {
    "ledger": "https://api.example.com/impact/ledger",
    "reports": "https://api.example.com/reports.json"
  }
}
```

- **Keep it stable.** Keep the info identical in the unpaid 402 and on the paid retry: x402 clients echo it, and servers reject a payment whose echo drops or changes a field. Put live numbers behind a URL, never in the block.
- **URLs.** Each URL is https, or a path relative to the resource URL.
- Put the fair-offer block in your 402's extensions (see handshake), and link this URL from your llms.txt or agent manifest as "conforms_to": {"fair_offer": "0.4"}.
- **`one_offer` (FO-8, new in 0.3).** Declare it only if every client sees the same offer at the same moment. Anyone can check it by asking for the 402 twice, seconds apart, under two client profiles (a different user agent and accept header, nothing signed, nothing paid) and comparing the price, asset, network and pay-to.
- **`receipts.delivery_hash` (FO-3, new in 0.4).** `"sha256-jcs"`: each paid result's receipt names `delivery.sha256`, the SHA-256 of the RFC 8785 (JCS) form of the `result` it came with, signed with it. Anyone holding the answer can recompute it with any JCS library.
- **`metered` (FO-9, new in 0.4).** Declare it on an offer that lists x402's `upto` scheme: the price in the 402 is the ceiling the buyer signs for, and the seller settles only `unit_price_usd` times the units delivered (nothing for none). The receipt names `amount_atomic` (charged) and `ceiling_atomic`. Declare one unit price, the same at every size.
- **`book` (FO-10, new in 0.4).** `ledger` is the public receipts ledger (monthly Merkle roots, a proof per receipt); `reports`, when given, lists buyers' reports, each tied to a payment on the chain and signed by the paying wallet.
- **Older blocks stay valid.** A 0.3 block simply doesn't claim the 0.4 fields, and a 0.2 block doesn't claim FO-8.

## Asking for proof: `Accept-Proof`

A buyer says what proof it needs before it pays, in an `Accept-Proof` request
header (or an `accept_proof` field in the block it echoes in the payment payload):

```
Accept-Proof: sample=attested; price<=listing; payers_30d>=20
```

| Requirement | Met when |
|-------------|----------|
| `sample=any` | the offer declares a free sample on its own host |
| `sample=attested` | the sample has an attestation on record |
| `settlement=seller` | failed calls are not charged (the seller's promise) |
| `settlement=rail` | the payment is captured only on success (auth-capture) |
| `price<=listing` | the live price was checked against the Bazaar listing |
| `receipts=signed` | paid results carry signed receipts |
| `pledge=ledger` | a pledge with a public ledger that answers |
| `instructions=none` | declared data-not-instructions, and no instruction-like text found |
| `payers_30d>=N` | at least N distinct payers in 30 days (Bazaar's count) |
| `calls_30d>=N` | at least N paid calls in 30 days (Bazaar's count) |
| `deliveries_30d>=N` | at least N deliveries in 30 days: receipts confirmed on chain, paid by buyers (evidence commons) |
| `one_offer=checked` | declared `one_offer`, and the offer read under two x402 client profiles seconds apart was the same, with no difference confirmed in 30 days (0.3) |
| `receipts=bound` | each receipt names the SHA-256 of the result it came with (0.4) |
| `metered=ceiling` | a metered offer that lists x402's `upto`, so the buyer signs a ceiling and pays for what was delivered (0.4) |
| `book=open` | the receipts ledger the block names answers (0.4) |
| `reports=open` | the block names where buyers' reports, each tied to a payment, are listed, and it answers (0.4) |

## Pledge ledgers (FO-7)

[`schema/pledge-ledger-0.1.schema.json`](schema/pledge-ledger-0.1.schema.json) is
the shape a pledge ledger can follow so any buyer can check a pledge to the cent:
the terms, a proof URL per receipt, and month by month the Merkle root of the
receipts settled, the basis, the share, the recipient and the donation
transactions on Base. A month is checked once its grace period has passed: the
transfers to the recipient must add up to at least the share times the basis.

## Platform annex: rules for directories

A buyer agent picks from the order a directory shows it. These seven rules are for
directories, indexes and marketplaces that list x402 sellers, so that order can
be trusted. Free to adopt, like the rules above.

| ID | Rule |
|----|------|
| P-1 | **No paid placement**: rank can't be bought, and anything sponsored says so. |
| P-2 | **A published rank rule**: the signals that order results are stated, and rank follows them. |
| P-3 | **Sweeps aren't payers**: catalog sweeps aren't counted as payers. |
| P-4 | **Removal with notice**: a listing is removed only with notice and a reason. |
| P-5 | **Extensions kept**: a listing's x402 extensions, the Fair Offer block among them, are kept as sent. |
| P-6 | **Fresh listings**: listings are kept fresh, and a stale one is marked or dropped. |
| P-7 | **Marks are earned, never sold**: a badge, grade or mark shown beside a listing is computed from public checks by a published rule, and can't be bought, asked for or kept by paying. |

## Checking an offer

- Validate a block: `POST https://api.agoranome.com/v1/fair-offer/validate` (free)
- Check a live offer's claims from outside: `GET https://api.agoranome.com/v1/fair-offer/check?url=...` (free)

## Implementations

- **Agoranome** (<https://agoranome.com>): every paid x402 route at `api.agoranome.com`
  sends the block, and the hosted MCP server (`https://api.agoranome.com/mcp`, listed in
  the official MCP Registry) offers a `check_offer` tool. That service's source isn't
  public; this repository holds the open standard.
- **fair-meter** ([`kit/`](kit/README.md), Apache-2.0): a small Python package for sellers that
  builds the 0.4 block, meters a charge under the buyer's `upto` ceiling, signs receipts
  bound to the delivery hash, guards responses against instructions (FO-6) and keeps a
  receipts ledger with monthly Merkle roots that Agoranome's inspector can anchor.
- Using it in your 402s? Open an issue with your host and it can be listed here.

## Agent skill for buyers

[`skills/x402-pay-safely/SKILL.md`](skills/x402-pay-safely/SKILL.md) is an
[Agent Skill](https://agentskills.io) for any agent with a wallet. It tells the
agent when to check an x402 seller before paying it and how to prove a payment
after, using Agoranome's Buyer check (free since 11 November 2026, up to 20 offers
at once, every answer signed) and the free checks above. Skill installers that read a GitHub repository can
install it from here. The copy served at
<https://api.agoranome.com/skills/x402-pay-safely/SKILL.md> is always current.

## Proposing changes

Open an issue or a pull request. Changes to the block's fields go into a new
version, so a buyer's policy written for an older version keeps working. Corrections, data
questions and replication requests can also go to research@agoranome.com.

## Versions

- **0.1**: The seven rules, declared in an agent manifest or llms.txt.
- **0.2**: The same seven rules, plus the machine-readable fair-offer block in the x402 402 and the buyer's Accept-Proof header.
- **0.3**: Eight rules: FO-8, the same offer to every buyer (`one_offer` in the block, `one_offer=checked` in Accept-Proof); the pledge-ledger schema for FO-7; and the platform annex, six rules for directories that list sellers. 0.2 blocks stay valid.
- **0.4**: Ten rules: FO-3's receipts bound to a hash of what was delivered (`receipts.delivery_hash`), FO-9, the price follows what was measured under the buyer's ceiling (`metered`, x402's `upto`), and FO-10, the book is open (`book`); Accept-Proof `receipts=bound`, `metered=ceiling`, `book=open` and `reports=open`; and P-7, marks earned by public check and never sold. 0.2 and 0.3 blocks stay valid.

## License

The code in `kit/` is licensed under Apache-2.0 (`kit/LICENSE`).
The standard text, schema, examples and the buyer skill are licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Implement it freely;
credit "The Fair Offer Standard, Agoranome".
