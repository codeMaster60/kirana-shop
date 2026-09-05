# Kirana Shop

Making a small shop buyable by an AI.

Razorpay AI Buildathon, Track 01: AI Growth & Agentic Commerce.

A shop owner photographs his shelf. Two minutes later his shop exists in a form an AI buyer can search and purchase from. No website, no developer, and no money moves until he confirms the item is actually there.

---

## The problem

You can tell an AI assistant to order a packet of biscuits and it'll do it from Blinkit, Zepto, Amazon. No problem.

You cannot order from the kirana shop at the end of your street.

And it's not a payments problem. UPI works fine there, half these shops already have a QR code on the counter. The issue is that the shop's stock doesn't exist anywhere a computer can read it. It's photos on the owner's phone, WhatsApp forwards, things he keeps in his head.

So to an AI, that shop basically doesn't exist.

## Why hasn't this been solved

Razorpay owns the payment rail but not the catalog. That shop might already be a Razorpay merchant, but accepting a payment and being orderable are different things. A QR code on the counter works when you're standing in the shop. It does nothing for someone three streets away, and nothing at all for an AI.

Google knows the shop exists. Name, address, timings, a photo of the storefront. That's discovery. Google has no idea there's Amul ghee on that shelf right now at ₹135.

Blinkit and Zepto don't put kiranas online. They replace them with dark stores. The model is owned inventory, not aggregating what's already on the street.

And the owner isn't going to digitise it himself, because that means typing out hundreds of items that go stale within a week, with no payoff until buyers actually turn up.

So payment rails exist, discovery exists, the catalog doesn't. And the catalog is exactly what an agent needs, because an agent can't walk in and look at a shelf.

---

## How it works

**1. Photos become a catalog.** `build_catalog.py` sends 8 product photos to Gemini vision and writes out name, description and price. That file is the shop.

**2. A buyer asks for something.** `shop.py` takes plain language and resolves it against the catalog. Brand names, misspellings, plurals, categories, or questions about the shop itself.

**3. Stock is confirmed before money moves.** A photo tells you what a shop sells. It can never tell you what's left on the shelf. So before anything is charged, the owner is asked: in stock, y or n.

- **y** → a Razorpay payment link is created and printed
- **n** → the sale is cancelled, nothing is charged, and an alternative from the same catalog is offered

Every step is appended to `audit.log`. Resolutions, stock checks, payment links, refusals.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full flow diagram and the reasoning behind the structure.

---

## Storage

There is no database. Two flat files do the work.

`catalog.json` holds the 8 products. Written once by `build_catalog.py`, read once at startup by `shop.py`. Nothing writes to it at runtime.

`audit.log` is append-only. One line per action, flushed per line, never edited or deleted.

### Why no database here

8 rows read once at startup. Adding SQLite would mean a schema, a connection lifecycle and a migration path in exchange for nothing. Reaching for infrastructure you don't need is a real cost, not a neutral one.

The audit log is the more interesting half. It only ever appends, so a log file is genuinely the right shape for it, not a compromise.

### Why stock is not stored

This looks like an omission and isn't.

I could store a stock count per product. I deliberately don't.

A kirana sells over the counter all day. The moment someone walks in and buys the last packet of ghee, any number I have stored is wrong, and I have no way of knowing. So a stored stock level would be a number that looks authoritative and is routinely false. That's worse than having no number at all, because the system would start making decisions on it.

Instead, the owner's confirmation is the read. Nothing is charged until it comes back, and silence is treated as no sale rather than a charge.

In production I'd still store a stock hint with a timestamp, for ranking and for showing likely availability. But it would never gate a payment. The human confirmation stays the only thing that authorises money.

### What production would use

**Postgres.**

The data is relational. Merchants own catalogs, catalogs own items, orders reference items, events reference orders. Those are joins.

Three things force a real database once there's more than one shop:

- **Concurrent writes.** Multiple buyers hitting the same merchant at once. Flat files have no story for this.
- **Row-level locking on stock state.** Two buyers can ask for the last packet at the same moment. That transition has to be serialised, or both get a payment link.
- **Transactions.** The order and its audit entry have to commit together or not at all. A file write and a database write can't be made atomic with each other.

Rough shape: `merchants`, `catalog_items`, `orders`, `order_events`, `audit_log`.

### Why not the alternatives

**SQLite.** Single writer. Fine for one process on a laptop, but it breaks the moment several merchants transact concurrently.

**MongoDB.** The data is relational and would fight a document store on every join. Merchants to catalogs to items to orders is exactly the shape document stores handle badly.

**Firebase or Supabase.** Workable, and faster to stand up. But it adds a hard network dependency to a flow that decides whether money moves, and you inherit someone else's schema and access model. For a payments path I'd rather own that.

### The audit log in production

It wouldn't live in a table anyone can `UPDATE`.

Either append-only object storage with a retention lock, or a Postgres table with `UPDATE` and `DELETE` revoked at the role level. Same point as it is now: an audit trail you can rewrite isn't an audit trail.

---

## Decisions, and why

**No money moves before confirmation.** The stock check is the gate, not a formality. The out-of-stock path never creates a payment link at all, and logs "nothing charged" explicitly. The failure path was built first, not bolted on after.

**The audit log is append-only.** Lines get added, never edited, never deleted. It also means the printed result can be checked against the record instead of taken on trust.

**Two models, each doing what it's good at.** Gemini handles vision in `build_catalog.py` because Groq doesn't serve vision models. Groq (`openai/gpt-oss-120b`) handles every conversational call in `shop.py`. The split wasn't planned. It came out of hitting a rate limit, described below.

**Intent is derived, never declared.** The resolver returns matched items, and intent is computed from whether that set is empty. The model is never asked "do you stock this?", only "which items do these words refer to?". That's what makes the contradiction bug below structurally impossible rather than merely fixed.

---

## What went wrong

All of this actually happened while building it.

**Catalog extraction: 5 of 8, then 8 of 8.** The first full run read only 5 photos. The three failures came back as `ServerError` and `ReadError`. That was Google's side, not the model, and the same photos had succeeded seconds earlier in isolation. Three attempts with a 4 second wait took it to 8 of 8.

**Strict name matching missed real products.** Asking for "soaps" returned "we don't stock anything like that" while Vim dishwash gel sat right there in the catalog. The matcher was comparing product names and ignoring what things are used for. Fixed by resolving on category and use.

**A refusal and a suggestion for the same item could coexist.** Asking for "truke earphones" returned "sorry, we don't stock that. Closest we have: Truke True Wireless Earbuds." Refusing the exact item it was naming.

The cause was that the model returned intent, matches and suggestion as three independent fields, and the handler trusted all three.

Patching the prompt would have moved the bug instead of removing it, which is exactly what happened on the first attempt. The fix held for "truke earphones", then "earbuds" failed the same way through a different field.

The real fix was structural. Intent derived from the match set. Matches and suggestions made disjoint before the result object is built. One refusal site that drops any suggestion whose id is in the refused set.

A deterministic backstop, `lexical_matches()`, promotes a catalog item when the buyer's own words name exactly one product and the model found nothing. "earbuds" and "sugar" resolve through it. "chocolates" and "a pen" stay correctly unstocked.

**Gemini's free tier caps at 20 requests a day.** Live verification stopped mid-testing and wouldn't reset until the next day. Rather than wait, the conversational path moved to Groq, whose free tier is far more generous. Gemini stayed on vision because Groq has no vision model. The runtime shop loop now makes zero Gemini calls, so the quota can't interrupt a demo.

---

## Tests

`test_shop.py` runs 35 inputs through the full flow with the owner prompt stubbed to **out of stock**. That's deliberately the harshest setting, since it drives both refusal paths and makes nearly every case emit a refusal plus a suggestion. No real payment links are created.

Every case asserts three things. Refused and suggested never intersect. `not_stocked` never coexists with a match. Items the catalog genuinely carries are never refused as unstocked.

Coverage: bare brands (`truke`, `amul`, `vim`), misspellings (`truk earphone`, `penut buttter`, `mosqito spray`), plurals and partials (`earbuds`, `dishwash`), categories (`soap`, `breakfast`, `something for cleaning`), vague asks, questions, and four genuinely unstocked items.

**Currently passing: 35 of 35.**

**Known limitation.** The resolver is model-backed, so suggestion quality varies between runs. The invariant assertions are deterministic. The stocked and unstocked expectations depend on the model plus the lexical backstop, so a run could differ.

---

## Running it

```
python3 -m venv venv
source venv/bin/activate
pip install razorpay python-dotenv google-genai groq

cp .env.example .env      # then fill in your own keys

python3 build_catalog.py  # photos in ./photos → catalog.json
python3 shop.py           # the shop
python3 test_shop.py      # 35 test cases
```

Razorpay runs in test mode throughout, using `rzp_test_` keys. Payment links are real and can be paid with test cards, but no real money moves. Two test payments were captured during development, ₹899 and ₹135, both visible in the Razorpay test dashboard.

---

## Files

| File | What it does |
|---|---|
| `build_catalog.py` | Reads product photos with Gemini vision, writes `catalog.json` |
| `catalog.json` | The 8 products: name, description, price |
| `shop.py` | The shop. Resolve input, check stock, create payment link or refuse |
| `test_shop.py` | 35 inputs through the full flow, asserting the no-contradiction invariant |
| `webhook.py` | Receives `payment.captured` with signature verification. Written, not yet verified end to end |
| `audit.log` | Every action, appended in order, never edited |
| `test_connection.py` | Minimal Razorpay connectivity check |
| `ARCHITECTURE.md` | The flow diagram and why it's shaped that way |
| `.env.example` | Key names required, no values |

`.env` is gitignored and holds four keys: Razorpay id and secret, Gemini, Groq.

---

## What I'd do next

Move the owner's stock check off the terminal and onto WhatsApp. That's where these merchants already are.

Add a timeout so an unanswered check expires instead of hanging. Silence should default to no sale, never to a charge.

Finish the webhook. Right now the shop creates a payment link and stops, so it never learns whether the buyer actually paid.

Replace the single-shop JSON file with per-merchant Postgres storage, as described above.
