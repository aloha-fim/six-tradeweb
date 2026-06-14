# SIX × Tradeweb — a two-way data loop for municipal-bond pricing

A working prototype that explores how the data relationship between **SIX** and **Tradeweb** could become a two-way street — and, just as importantly, measures whether it would actually work.

## What this is

Tradeweb produces price *estimates* for US municipal bonds (its "Ai-Price" product). Since 2022, SIX has resold those estimates to banks and investors across Switzerland and Europe that Tradeweb doesn't serve directly.

Today the data flows one way: **Tradeweb → SIX → clients.** This app models what could usefully flow *back* — signals SIX is uniquely positioned to return to Tradeweb — and builds it as a running application: a live dashboard, an honest split between what's real today and what's aspirational, and a test that checks whether the feedback genuinely improves the prices.

It runs on synthetic (made-up but realistic) data, so the whole idea can be demonstrated end to end without any real client information. **The methods are the deliverable; the specific numbers are illustrative.**

## Why it matters

The hardest problem in muni pricing is easy to state: **most municipal bonds barely trade.** On any given day roughly 98% don't change hands, so there's almost no fresh market data to check a price estimate against. That's exactly where a pricing model is weakest — and where an independent reality check is worth the most.

SIX sits *between* Tradeweb and many bank clients. That position lets it do something Tradeweb cannot do on its own: gather where several banks privately value the same non-trading bond, blend those into a single anonymous "consensus," and hand it back as a check on the estimate. No single trading venue can assemble that view — it needs the multi-source vantage SIX has as the distributor in the middle.

### The real benefits

**For Tradeweb**
- **A reality check on the bonds it can't otherwise verify.** The multi-source consensus targets the model's single weakest spot — the names that don't trade.
- **Cleaner reference data.** As SIX's clients use the estimates, errors in bond IDs, ratings and sector tags surface and can be fixed. This is about the *bond*, not the client, so there's no privacy hurdle.
- **Reach and revenue.** SIX already distributes to a European buy-side, private-bank and insurer base Tradeweb doesn't touch directly — meaning data revenue and a demand signal Tradeweb wouldn't otherwise see.

**For SIX**
- A **higher-value product** to sell its own clients (an independent benchmark and analytics layer on top of the raw estimates), and a **deeper, stickier relationship** with Tradeweb as the trusted, neutral intermediary in the middle.

### Why a working app, not a slide deck

It makes the idea **concrete and testable.** A built-in evaluation shows the feedback measurably tightening the estimates — moving them about 23% closer to where banks actually value the bonds (on synthetic data, as a proof of method). And the app is deliberately honest about what is genuinely unique (the consensus and the reference-data corrections) versus what is still aspirational (an automatic model-retraining loop). That honesty is the point: it separates the real opportunity from the hype.

## Honest about the limits

- **All market data here is synthetic.** The mechanisms are real; the numbers are illustrative placeholders, not a forecast.
- **The biggest real-world dependency is consent.** The consensus only works if banks agree to contribute where they value each bond. That's a solvable problem — contribute-to-consume pooling, strict anonymisation, and SIX acting as a neutral intermediary banks trust more than a trading venue — but it's the key thing to validate, and a core reason for testing this idea directly with SIX.

---

*This is an independent prototype built to explore product-market fit. It is not affiliated with or endorsed by SIX or Tradeweb, and nothing here is investment advice.*
