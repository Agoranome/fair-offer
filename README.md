# The Fair Offer Standard

Seven open rules for selling to AI agents honestly, and a machine-readable way to
prove them inside the x402 payment handshake. Version **0.2** (draft).

Human-readable page: <https://agoranome.com/standard> ·
JSON: [`standard.json`](standard.json) ·
Schema: [`schema/fair-offer-0.2.schema.json`](schema/fair-offer-0.2.schema.json)

## The seven rules

| ID | Rule | What it means | How to check |
|----|------|---------------|--------------|
| FO-1 | **Price before payment** | An unpaid call returns an x402 offer (HTTP 402) that states the price, asset, network and pay-to address. Nothing is charged before the buyer has signed that exact offer. | [check](https://api.agoranome.com/v1/seller/lint) |
| FO-2 | **Failure is free** | A call that fails, times out or returns no result is not settled. The buyer is told so. | self-declared |
| FO-3 | **Receipts are signed** | Every paid result carries a receipt signed by the seller's key: what was bought, for how much, and when. | [check](https://agoranome.com/verify) |
| FO-4 | **Sample before you buy** | A free sample shows what the paid route returns, and a third party can attest that the sample and the paid offer come from the same host. | [check](https://api.agoranome.com/v1/seller/attest) |
| FO-5 | **Honest listing** | The listing's description, input schema and output example match what the route really returns. Edits made to win buyers must stay true. | [check](https://api.agoranome.com/v1/seller/panel) |
| FO-6 | **Data, never instructions** | Responses carry data for the buying agent to use, never instructions aimed at it. A buyer can treat any instruction in a response as an attack. | self-declared |
| FO-7 | **Promises are verifiable** | Any claim about where revenue goes (a pledge, a split, a donation) comes with a public ledger that a buyer can check per receipt, with on-chain proof of what was given. | [check](https://api.agoranome.com/impact/ledger) |

## Declaring it (0.2)

A seller's HTTP 402 carries an x402 extension, `extensions["fair-offer"]`, whose
`info` states which rules the offer keeps and where a buyer can check each one:

```json
{
  "version": "0.2",
  "standard": "https://agoranome.com/standard",
  "settlement": {
    "failed_calls": "not_charged",
    "enforced_by": "seller"
  },
  "receipts": {
    "signed": true,
    "verify": "https://api.example.com/receipts/verify"
  },
  "sample": {
    "url": "https://api.example.com/v1/search/sample"
  },
  "listing_price_matches_offer": true,
  "data_not_instructions": true,
  "pledge": {
    "share": 0.15,
    "basis": "settled_revenue",
    "ledger": "https://api.example.com/impact/ledger"
  }
}
```

- **Keep it stable.** Keep the info identical in the unpaid 402 and on the paid retry: x402 clients echo it, and servers reject a payment whose echo drops or changes a field. Put live numbers behind a URL, never in the block.
- **URLs.** Each URL is https, or a path relative to the resource URL.
- Put the fair-offer block in your 402's extensions (see handshake), and link this URL from your llms.txt or agent manifest as "conforms_to": {"fair_offer": "0.2"}.

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

## Checking an offer

- Validate a block: `POST https://api.agoranome.com/v1/fair-offer/validate` (free)
- Check a live offer's claims from outside: `GET https://api.agoranome.com/v1/fair-offer/check?url=...` (free)

## Implementations

- **Agoranome** (<https://agoranome.com>): every paid x402 route at `api.agoranome.com`
  sends the block, and the hosted MCP server (`https://api.agoranome.com/mcp`, listed in
  the official MCP Registry) offers a `check_offer` tool. That service's source isn't
  public; this repository holds the open standard.
- Using it in your 402s? Open an issue with your host and it can be listed here.

## Agent skill for buyers

[`skills/x402-pay-safely/SKILL.md`](skills/x402-pay-safely/SKILL.md) is an
[Agent Skill](https://agentskills.io) for any agent with a wallet. It tells the
agent when to check an x402 seller before paying it and how to prove a payment
after, using Agoranome's paid Buyer check ($0.01 a call, up to 20 offers at once)
and the free checks above. Skill installers that read a GitHub repository can
install it from here. The copy served at
<https://api.agoranome.com/skills/x402-pay-safely/SKILL.md> is always current.

## Proposing changes

Open an issue or a pull request. Changes to the block's fields go into a new
version, so a buyer's policy written for 0.2 keeps working. Corrections, data
questions and replication requests can also go to research@agoranome.com.

## Versions

- **0.1**: The seven rules, declared in an agent manifest or llms.txt.
- **0.2**: The same seven rules, plus the machine-readable fair-offer block in the x402 402 and the buyer's Accept-Proof header.

## License

The standard text, schema, examples and the buyer skill are licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Implement it freely;
credit "The Fair Offer Standard, Agoranome".
