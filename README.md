# Kirana Shop - making a small shop buyable by an AI

Razorpay AI Buildathon, Track 01: AI Growth & Agentic Commerce.

A shop owner photographs his shelf. Two minutes later his shop exists in a form an AI buyer can search and purchase from - with no website, no developer, and no money moving until he confirms the item is actually in stock.

---

## The problem

Tell an AI assistant "order me a packet of Good Day biscuits" and it can do that from Amazon, Blinkit or Zepto. It cannot do it from the kirana shop at the end of the street.

It isn't a payments problem - UPI already works there. The shop's stock doesn't exist anywhere a computer can read. It's photos on the owner's phone, WhatsApp forwards, and things he keeps in his head. To an AI, that shop isn't there.

Razorpay's own agentic UPI pilot launched with Zomato, Swiggy and Zepto - companies that already have digital inventory. The shops that have none are the gap this closes.

---

## How it works

**1. Photos become a catalog.** `build_catalog.py` sends 8 product photos to Gemini vision and writes out name, description and price for each. That file is the shop.

**2. A buyer asks for something.** `shop.py` takes plain-language input and resolves it against the catalog - brand names, misspellings, plurals, categories, or questions about the shop itself.

**3. Stock is confirmed before money moves.** A photo tells you what a shop *sells*. It can never tell you what's *left on the shelf*. So before anything is charged, the owner is asked: in stock, y or n.

- **y** → a Razorpay payment link is created and printed
- **n** → the sale is cancelled, nothing is charged, and an alternative from the same catalog is offered

Every step - resolutions, stock checks, payment links, refusals - is appended to `audit.log`.

---

## Decisions, and why

**No money moves before confirmation.** The stock check is the gate, not a formality. The out-of-stock path produces no payment link at all and logs "nothing charged" explicitly. This is the whole point of the design: the failure path was built first, not bolted on.

**The audit log is append-only.** Lines are added, never edited or deleted. An audit trail you can rewrite isn't an audit trail. It also means the printed result can be checked against the record rather than taken on trust.

**Two models, each doing what it's good at.** Gemini handles vision in `build_catalog.py` because Groq doesn't serve vision models. Groq (`openai/gpt-oss-120b`) handles all conversational work in `shop.py`. The split wasn't planned - it came out of hitting Gemini's free tier cap, described below.

**Intent is derived, never declared.** The resolver returns matched items; the intent is computed from whether that set is empty. The model is never asked "do you stock this?" - only "which items do these words refer to?". This makes the contradiction bug below structurally impossible rather than merely fixed.

**No database.** The catalog is 8 products read once at startup and the audit trail is a log file. At this size a database would be setup cost with no benefit. In production this would be Postgres per merchant, with the audit log in append-only storage.

---

## What went wrong

Everything here actually happened during the build.

**Catalog extraction: 5 of 8, then 8 of 8.** The first full run read only 5 photos successfully. The three failures were `ServerError` and `ReadError` from Google's side, not model errors - the same photo had succeeded moments earlier in isolation. Adding three attempts with a 4-second wait took it to 8 of 8.

**Strict name matching missed real products.** Asking for "soaps" returned "we don't stock anything like that" while Vim dishwash gel sat in the catalog. The matcher was comparing product names only, ignoring category and use.

**A refusal and a suggestion for the same item could coexist.** Asking for "truke earphones" returned *"Sorry, we don't stock that. Closest we have: Truke True Wireless Earbuds."* - refusing the exact item it was naming. The root cause was that the model returned intent, matches and suggestion as three independent fields, and the handler trusted all three.

Patching the prompt would have moved the bug rather than removed it, which is what happened on the first attempt: the fix held for "truke earphones" but "earbuds" failed the same way through a different field. The real fix was structural - intent derived from the match set, matches and suggestions made disjoint before the result object is built, and a single refusal site that drops any suggestion whose id is in the refused set.

A deterministic backstop (`lexical_matches()`) promotes a catalog item when the buyer's own words name exactly one product and the model found nothing. "earbuds" and "sugar" resolve through it; "chocolates" and "a pen" stay correctly unstocked.

**Gemini free tier caps at 20 requests per day.** Live verification stopped mid-testing and wouldn't reset until the next day. Rather than wait, the conversational path moved to Groq, whose free tier is far more generous. Gemini stayed on vision because Groq has no vision model.

---

## Tests

`test_shop.py` runs 35 inputs through the full flow with the owner prompt stubbed to **out of stock** - deliberately the harshest setting, since it drives both refusal paths and makes nearly every case emit a refusal plus a suggestion. No real payment links are created.

Each case asserts that refused and suggested sets never intersect, that `not_stocked` never coexists with a match, and that items the catalog genuinely carries are never refused as unstocked.

Coverage: bare brand names (`truke`, `amul`, `vim`), misspellings (`truk earphone`, `penut buttter`, `mosqito spray`), plurals and partials (`earbuds`, `dishwash`), categories (`soap`, `breakfast`, `something for cleaning`), vague asks, questions, and four genuinely unstocked items.

**Currently passing: 35 of 35.**

**Known limitation:** the resolver is model-backed, so suggestion quality can vary between runs. The invariant assertions are deterministic; the stocked/unstocked expectations depend on the model plus the lexical backstop. A run could differ.

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

Razorpay runs in test mode throughout (`rzp_test_` keys). Payment links are real and can be paid with test cards, but no real money moves. Two test payments were captured during development - ₹899 and ₹135 - visible in the Razorpay test dashboard.

---

## Files

| File | What it does |
|---|---|
| `build_catalog.py` | Reads product photos with Gemini vision, writes `catalog.json` |
| `catalog.json` | The 8 products - name, description, price |
| `shop.py` | The shop: resolve input, check stock, create payment link or refuse |
| `test_shop.py` | 35 inputs through the full flow, asserting the no-contradiction invariant |
| `audit.log` | Every action, appended in order, never edited |
| `test_connection.py` | Minimal Razorpay connectivity check |
| `.env.example` | Key names required, no values |

`.env` is gitignored and contains four keys - Razorpay id and secret, Gemini, Groq.

---

## What I'd do next

Move the owner's stock check from a terminal prompt to WhatsApp, since that's where these merchants already are. Add a timeout so an unanswered check expires rather than hanging - silence should default to no sale, never to a charge. And replace the single-shop JSON file with per-merchant storage.
