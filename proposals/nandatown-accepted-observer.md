# Proposal: an accepted outside observer for agent payments in NANDA's towns

A draft for Shariar to send, or not. Nothing here has been sent to NANDA or to either town's maintainers.

## The ask

That NANDA's towns accept the Agoranome inspector as an outside observer of agent payments: a program whose
evidence records about x402 sellers (what each listing states, and what its live offer asked when read without
paying) a town can take in and show beside its own, named as the inspector's and never as the town's verdict.

## Why the inspector fits

- **It observes; it doesn't rate.** One record holds one finding, from one observer, about one seller, at one
  read, the evidence rule both towns already keep. The inspector's weekly sheets are signed and hash-chained, its
  verifier is open, and anyone can repeat a read.
- **It takes nothing from those it observes.** Sellers can't pay to be listed, marked or left out, and Agoranome
  sells nothing to a seller it grades. A payment from a seller's own pay-to is refused before any charge.
- **No model in its verdicts.** The rules are comparisons anyone can rerun, so it fails differently from monitors
  built on one model or one threat list.
- **It already speaks NandaTown.** The `fairoffer.v1` payments plugin in this repository
  ([`nandatown/`](../nandatown/)) runs in NandaTown, writes its findings as NandaTown evidence records, and
  ships the drill's nine traps and a market scenario from the 11 November 2026 sheet as free scenarios, with
  negative controls that fail on purpose on the default ledger.

## Where it would go: two options, not yet confirmed

The research behind this choice is unconfirmed (Shariar, 15 Nov 2026: "not sure about this research. stop."), so
both options stay open until he checks them:

1. **NandaTown** (github.com/projnanda/nandatown, last commit 4 November 2026). Offer `fairoffer.v1` upstream as a
   payments plugin with its scenarios, and propose that a scenario can name accepted outside observers whose
   evidence records its validators read.
2. **Nanda Town 2** (its registry probes listed URLs every 6 hours and stores each probe with its observer; last
   commit 26 August 2026). Propose the inspector as a second observer for x402 listings, its records shown beside
   the town's own probes and never merged into them.

## What Shariar would do

Pick the option, then open the issue or pull request in that repository from his own account, and raise it in the
trust and identity working group with the status credential draft
([`drafts/status-credential/`](../drafts/status-credential/)).
