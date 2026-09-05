# architecture

kirana shop - how the pieces fit together.

two phases. onboarding runs once per shop. the shop loop runs per buyer.

```
ONBOARDING (once)

  photos/  ──►  build_catalog.py  ──►  catalog.json
                 gemini vision           8 products
                 3 retries on              name
                 transient error           description
                                           price


SHOP LOOP (per buyer)

  buyer input
      │
      ▼
  resolve()  ──►  groq  ──►  Resolution
      │        gpt-oss-120b      match_ids
      │                          related_ids
      │                          ask
      │                          intent  (derived, not declared)
      │
      ├─ no match ────────►  refuse()  ──►  no payment link
      │                          │          suggestion dropped if
      │                          │          its id is in refused set
      │                          │
      └─ match found ─────►  sell()
                                 │
                                 ▼
                       owner: "in stock? (y/n)"
                                 │
                        ┌────────┴────────┐
                        y                 n
                        │                 │
                  razorpay api      cancelled
                  payment link      nothing charged
                  (test mode)       alternative offered
                        │                 │
                        └────────┬────────┘
                                 ▼
                            audit.log
                          (append-only)
```

**the gate is the point.**
no path reaches the razorpay api without passing the owner's confirmation first. the refusal branch never creates a link at all, so there's nothing to reverse.

**one resolver, one refusal site.**
`resolve()` is the only place a catalog match gets decided. `refuse()` is the only place a "no" gets issued. intent is computed from whether the match set is empty rather than asked for separately.

that's what makes it structurally impossible for the shop to refuse an item and suggest that same item. a bug that did happen, back when those were independent fields.

**where each model runs.**
gemini touches the system only during onboarding. once `catalog.json` exists, the shop loop makes zero gemini calls. which matters, because gemini's free tier caps at 20 requests a day.
