# Seller status credential 0.1 (draft)

A draft for the NANDA trust and identity working group: a signed, short-lived statement of whether one x402
seller's live offer kept its listing when it was read without paying. A registry, resolver, gateway or agent host
can attach it to the seller's record and refresh it cheaply; a buying agent can check it before it pays. This is
a draft for discussion, not part of the Fair Offer Standard.

Schema: [`seller-status-0.1.schema.json`](seller-status-0.1.schema.json) · Example:
[`example.json`](example.json) (made in a test, with a test key: its seller and signature are not real) ·
Registry pointer: [`example-attestation.json`](example-attestation.json)

## What it states

One credential is about one seller, by host, at one read:

| Field | What it holds |
|---|---|
| `credentialSubject.listing` | The seller's busiest listing on the newest weekly Bazaar snapshot: its resource, its listed USD price and its pay-to |
| `credentialSubject.asked` | What the listing's resource asked when read once without paying: the HTTP status, the lowest price, the pay-to addresses and the networks |
| `credentialSubject.status` | The first of these that holds: `sanctioned_pay_to` (a pay-to named is on the US Treasury's OFAC SDN list), `no_answer` (no payable x402 offer), `asked_more` (more than listed), `other_pay_to` (a pay-to the listing doesn't show), `not_judged` (the listing states no price), `as_listed` |
| `credentialSubject.findings` | Every finding that holds, not only the first |
| `credentialSubject.status_id`, `status_since` | An id that stays the same while the status and its findings do, and since when |
| `credentialSubject.ofac` | Whether the pay-to addresses were checked against the SDN list, and when that list was read |
| `validFrom`, `validUntil` | Issued, and the read's time plus the credential's life, 24 hours by default |

It holds no grade, no payer or call count, and no model's opinion: the rule (`status-1`) is the comparison above,
and anyone can repeat the read.

## How it is signed and checked

The credential is shaped as a [W3C Verifiable Credential 2.0](https://www.w3.org/TR/vc-data-model-2.0/) of type
`AgoranomeSellerStatus`. Each entry of `signatures` is a JWS (RFC 7515: a protected header naming `alg`, `jku`
and `kid`, and a signature) over the [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) form of the credential
without `signatures`. The keys are at the issuer's `/.well-known/jwks.json`. No floating-point number is signed:
amounts are decimal text.

While it lives, the credential is served at a URI of its own as its RFC 8785 text. A registry points to it with
an entry in the shape of an ARD `trustManifest` attestation:

```json
{"type": "AgoranomeSellerStatus", "uri": "https://agoranome.com/v1/status/credentials/<id>",
 "mediaType": "application/json", "digest": "sha256:<hex of the served bytes>"}
```

A reader fetches the URI, checks the digest, checks a signature with the issuer's JWKS, and checks `validUntil`.

## Refreshing it: free when unchanged

A credential's ETag is `"st1-<id>"`. A holder that sends back the ETags it holds (in the request body's `held`, or
in `If-None-Match`) gets `304 Not Modified`, free, while every credential still holds: unexpired, with the same
`status_id`. The re-check is answered from a read at most an hour old, so a seller is read at most once an hour
whoever asks, and the free re-checks are capped per day by the seller reads they cause (2,000 at Agoranome).
A credential is charged again only when its status changed or it lapsed. Under x402's `upto` scheme only new,
changed or lapsed credentials are charged.

At Agoranome a credential costs $0.001, up to 1,000 in one request for $1 (`POST /v1/status/{1,10,100,1000}`), paid
by machines only. It is never sold to the seller it is about: a payment from a named seller's pay-to is refused
before any charge. A buyer's own checks before and after it pays stay free.

## Open questions for the group

1. Whether the `trustManifest` attestation entry is the right pointer, or an Agent Facts field beside
   `data_facts_url`.
2. Whether 24 hours is the right default life for a status read hourly, and whether a registry should be able to
   ask for a shorter one.
3. A shared vocabulary for the statuses, so credentials from more than one inspector can be compared.
4. Whether a seller should be able to attach a signed dispute to a credential, as Agoranome's sheets allow for
   their rows.
