import os, re, json, uuid
from datetime import datetime

import razorpay
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

AUDIT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.log")
CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")

groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
razor = razorpay.Client(auth=(
    os.getenv("RAZORPAY_KEY_ID"),
    os.getenv("RAZORPAY_KEY_SECRET")
))


def audit(action, outcome):
    """Append-only audit trail. One line per step, never rewritten."""
    line = f"{datetime.now().isoformat(timespec='seconds')}\t{action}\t{outcome}\n"
    with open(AUDIT_LOG, "a") as f:
        f.write(line)
        f.flush()


def load_catalog():
    with open(CATALOG) as f:
        return json.load(f)


def ask_groq(prompt):
    resp = groq.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def catalog_lines(items):
    return "\n".join(
        f'{it["id"]}: {it["name"]} - Rs.{it["price_inr"]} - {it["description"]}'
        for it in items
    )


# What SHAPE of question the buyer asked. Deliberately says nothing about
# whether the shop stocks anything - that is the resolver's job, and keeping
# the two apart is what makes a self-contradicting answer unrepresentable.
ASKS = ("buy", "question", "category", "catalog", "vague")

INTENTS = (
    "product_request",
    "catalog_question",
    "product_question",
    "category_request",
    "unclear",
    "not_stocked",
)


# ---------------------------------------------------------------------------
# Resolution: the single place the catalog is matched.
# ---------------------------------------------------------------------------

class Resolution:
    """What the buyer's words point to in the catalog. Resolved exactly once.

    Two guarantees hold by construction, and every branch downstream relies on
    them instead of re-deciding for itself:

      1. `matches` and `suggestions` are disjoint. An item the buyer asked for
         can never be offered back to them as a consolation.
      2. `intent` is DERIVED from `matches`, never supplied by the model. So
         "not_stocked" is reachable only when `matches` is empty - i.e. only
         when the resolver genuinely found nothing.
    """

    def __init__(self, matches, suggestions, ask, answer, clarify, reason):
        self.matches = matches
        self.suggestions = suggestions
        self.ask = ask
        self.answer = answer
        self.clarify = clarify
        self.reason = reason

    @property
    def matched_ids(self):
        return {it["id"] for it in self.matches}

    @property
    def intent(self):
        """Derived, not declared. This is the whole fix."""
        if self.ask == "catalog" and not self.matches:
            return "catalog_question"
        if self.matches:
            # A match at ANY confidence means the shop has a candidate, so the
            # buyer gets a stock check - never a refusal.
            if self.ask == "question":
                return "product_question"
            if self.ask == "category" and len(self.matches) > 1:
                return "category_request"
            return "product_request"
        if self.ask == "vague":
            return "unclear"
        return "not_stocked"

    def best_suggestion(self, exclude=()):
        """Closest adjacent item, never one the buyer actually asked for."""
        blocked = self.matched_ids | set(exclude)
        return next((it for it in self.suggestions if it["id"] not in blocked), None)


def _tokens(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4}


def _singular(word):
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def lexical_matches(catalog, request):
    """Deterministic backstop: catalog items the buyer's own words name outright.

    Only tokens that identify exactly ONE item are trusted, so this fires on
    "earbuds" or "muesli" but not on "case" or "home". It exists because the
    model will occasionally file a genuine match under related_ids instead -
    which would print "we don't stock that" next to the very item asked for.
    """
    index = {}
    for it in catalog:
        for tok in _tokens(it["name"]):
            index.setdefault(_singular(tok), set()).add(it["id"])

    by_id = {it["id"]: it for it in catalog}
    hits, seen = [], set()
    for word in sorted(_tokens(request)):
        owners = index.get(_singular(word), ())
        if len(owners) == 1:
            (cid,) = owners
            if cid not in seen:
                seen.add(cid)
                hits.append(by_id[cid])
    return hits


def resolve(catalog, request):
    """Ask Groq what the buyer means and match the catalog - once, here only.

    The model is asked to do two separate things it can't confuse:
      - `match_ids`: catalog items the request actually refers to
      - `related_ids`: merely adjacent items, for use only if there is no match

    It is never asked whether the shop stocks something. That question is
    answered by whether `match_ids` came back empty.
    """
    prompt = f"""A buyer at a small Indian kirana shop typed: "{request}"

Here is the entire shop catalog (id: name - price - description):
{catalog_lines(catalog)}

Do TWO separate jobs.

JOB 1 - "match_ids": which catalog items do the buyer's words actually refer to?
Be generous. Include an item if a shopkeeper would reasonably hand it over.
That includes:
  - a brand name, even alone, even misspelled ("truke", "amul", "vim", "saffola")
  - a part of the product name, plural or singular ("earphones", "earbuds",
    "muesli", "peanut butter", "ghee", "sugar")
  - a generic word for what the item IS ("dishwash", "mosquito spray")
Match on what each item IS and what it is USED FOR, not just its exact name:
a dishwash gel IS a soap; a dishwash gel and a room freshener are both household
cleaning products; muesli and peanut butter are both breakfast.
Best match FIRST. Leave empty ONLY if genuinely nothing in the catalog fits the
need - not merely because no product name shares words with what they typed.

JOB 2 - "related_ids": items that are NOT what they asked for but are the
closest thing the shop has, for when match_ids is empty. Never repeat an id
from match_ids here.

Also pick ONE "ask" describing the SHAPE of the request:
- "buy": they want a specific product
- "question": they are asking something ABOUT an item rather than to buy it,
    e.g. "how much is the ghee", "what size is it", "does it have sugar".
    Answer it in "answer" using ONLY the catalog data above. Never invent
    details that are not there.
- "category": they want a category or a use, not a named product,
    e.g. "soaps", "something for cleaning", "breakfast", "snacks", "gift"
- "catalog": they are asking what the shop has or sells in general,
    e.g. "what do you have", "what all do u have", "what do you sell".
    Leave match_ids empty for this - the shop lists everything itself.
- "vague": too vague, empty of intent, or not a shopping request at all.
    Put one short question back to the buyer in "clarify".

Return ONLY raw JSON:
{{"match_ids": ["p1"], "related_ids": [], "ask": "buy",
  "answer": "", "clarify": "", "reason": "one short sentence"}}"""

    result = ask_groq(prompt)

    ask = result.get("ask")
    if ask not in ASKS:
        ask = "vague"

    by_id = {it["id"]: it for it in catalog}

    def items_for(key):
        raw = result.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        out, seen = [], set()
        for i in raw:
            if i in by_id and i not in seen:
                seen.add(i)
                out.append(by_id[i])
        return out

    matches = items_for("match_ids")
    if not matches and ask != "catalog":
        # The model found nothing. Before that becomes a refusal, check whether
        # the buyer literally named something on the shelf.
        matches = lexical_matches(catalog, request)
        if matches:
            audit("resolve", "lexical backstop matched " + ",".join(i["id"] for i in matches))
    matched = {it["id"] for it in matches}
    # Enforced here, not trusted from the model: a suggestion can never be
    # something the buyer already asked for.
    suggestions = [it for it in items_for("related_ids") if it["id"] not in matched]

    if ask == "catalog" and matches:
        # They asked what the shop sells; a stray match must not turn that into
        # a one-item answer.
        matches = []

    return Resolution(
        matches=matches,
        suggestions=suggestions,
        ask=ask,
        answer=(result.get("answer") or "").strip(),
        clarify=(result.get("clarify") or "").strip(),
        reason=(result.get("reason") or "").strip(),
    )


# ---------------------------------------------------------------------------
# The one place a refusal is ever printed.
# ---------------------------------------------------------------------------

def refuse(message, refused_ids, suggestion, request, kind):
    """Print a refusal and, at most, an alternative that is NOT the refused item.

    Every "no" the buyer hears goes through here, so the contradiction the old
    code could produce has exactly one place left to be prevented.
    """
    refused_ids = set(refused_ids)
    if suggestion is not None and suggestion["id"] in refused_ids:
        # Unreachable by construction; kept so a future bug fails quietly-safe
        # rather than telling the buyer "no" and "here it is" in one breath.
        audit("invariant", f'dropped suggestion {suggestion["id"]} - same item as refusal')
        suggestion = None

    print(f"\n{message}")
    if suggestion:
        print(f'Closest we have: {suggestion["name"]} - Rs.{suggestion["price_inr"]}')
        print("(Ask for it and I'll check stock.)")
        audit("suggest", f'{suggestion["id"]} {suggestion["name"]} - closest to "{request}"')
    else:
        audit("suggest", "no alternative offered")

    audit("refusal", f'{kind} - "{request}" - nothing charged')
    return {
        "refused": sorted(refused_ids),
        "suggested": [suggestion["id"]] if suggestion else [],
    }


def suggest_alternative(catalog, rejected, request, exclude=()):
    """Owner said out of stock - ask Groq for a different catalog item."""
    blocked = {rejected["id"]} | set(exclude)
    others = [it for it in catalog if it["id"] not in blocked]
    if not others:
        return None, ""
    prompt = f"""A buyer at a small Indian shop asked for: "{request}"
They wanted "{rejected['name']}" but it is OUT OF STOCK.

Here are the other items still available:
{catalog_lines(others)}

Suggest the ONE item from this list that is the closest useful alternative.
Return ONLY raw JSON: {{"id": "p3", "pitch": "one short friendly sentence for the buyer"}}"""
    result = ask_groq(prompt)
    item = next((it for it in others if it["id"] == result.get("id")), None)
    return item, result.get("pitch", "")


def create_payment_link(item):
    link = razor.payment_link.create({
        "amount": item["price_inr"] * 100,
        "currency": "INR",
        "description": item["name"],
        "reference_id": f'{item["id"]}-{uuid.uuid4().hex[:8]}',
        "notes": {"catalog_id": item["id"]},
    })
    return link


def ask_owner(item):
    while True:
        answer = input(f'Shop owner - "{item["name"]}" in stock? (y/n): ').strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def show_all(catalog):
    print(f"\nWe stock {len(catalog)} things right now:\n")
    for it in catalog:
        print(f'  {it["name"]} - Rs.{it["price_inr"]}')
    print("\nName any of them and I'll check stock.")


def sell(catalog, item, request, res):
    """Confirmed product request - stock check, then payment link."""
    print(f'\n  {item["name"]}')
    print(f'  Rs.{item["price_inr"]}\n')

    if not ask_owner(item):
        # Owner refused the sale - stop here, take no money.
        audit("stock_check", f'{item["id"]} OUT OF STOCK - owner said no')
        try:
            alt, pitch = suggest_alternative(catalog, item, request, exclude=res.matched_ids)
        except Exception as e:
            audit("suggest", f"error {type(e).__name__}: {e}")
            alt, pitch = None, ""

        out = refuse(
            "Cancelled - that one's out of stock. Nothing was charged.",
            res.matched_ids | {item["id"]},
            alt,
            request,
            f'{item["id"]} out of stock',
        )
        if alt and pitch:
            print(f"  {pitch}")
        return out

    audit("stock_check", f'{item["id"]} IN STOCK - owner said yes')

    try:
        link = create_payment_link(item)
    except Exception as e:
        print(f"\nPayment link failed ({type(e).__name__}). Nothing was charged.")
        audit("payment_link", f'{item["id"]} error {type(e).__name__}: {e} - nothing charged')
        return {"refused": [], "suggested": []}

    print(f'\nPay here: {link["short_url"]}')
    audit("payment_link", f'{item["id"]} {link["id"]} Rs.{item["price_inr"]} {link["short_url"]}')
    return {"sold": item["id"], "refused": [], "suggested": []}


def handle(catalog, request):
    """Run one buyer request end to end.

    Returns a small record of what happened - matched / refused / suggested ids -
    so the invariant can be checked structurally, not just by reading printouts.
    """
    audit("buyer_request", f'"{request}"')

    try:
        res = resolve(catalog, request)
    except Exception as e:
        print(f"Could not read that request ({type(e).__name__}). Try again.")
        audit("resolve", f"error {type(e).__name__}: {e}")
        return {"intent": "error", "matched": [], "refused": [], "suggested": []}

    intent = res.intent
    matched = ",".join(it["id"] for it in res.matches) or "-"
    audit("resolve", f'{intent} - matched {matched} - ask={res.ask} - {res.reason}')

    record = {"intent": intent, "matched": sorted(res.matched_ids),
              "refused": [], "suggested": []}

    if intent == "catalog_question":
        show_all(catalog)
        return record

    if intent == "product_question":
        if res.answer:
            print(f'\n{res.answer}')
        else:
            for it in res.matches:
                print(f'\n  {it["name"]} - Rs.{it["price_inr"]}\n  {it["description"]}')
        if len(res.matches) == 1:
            print(f'\n(Say "{res.matches[0]["name"]}" and I\'ll check stock.)')
        return record

    if intent == "category_request":
        print("\nThese fit what you're after:\n")
        for it in res.matches:
            print(f'  {it["name"]} - Rs.{it["price_inr"]}')
        print("\nWhich one?")
        return record

    if intent == "unclear":
        question = res.clarify or "Could you say a bit more about what you're looking for?"
        print(f"\n{question}")
        audit("clarify", f'asked buyer: "{question}"')
        return record

    if intent == "not_stocked":
        # Only reachable with res.matches empty - the resolver found nothing.
        record.update(refuse(
            "Sorry, we don't stock that.",
            res.matched_ids,
            res.best_suggestion(),
            request,
            "not stocked",
        ))
        return record

    # product_request - a match exists, so it goes to the stock check.
    record.update(sell(catalog, res.matches[0], request, res))
    return record


def main():
    catalog = load_catalog()
    audit("session_start", f"{len(catalog)} items loaded from catalog.json")
    print(f"Kirana shop - {len(catalog)} items. Type what you want, or 'quit'.\n")

    while True:
        try:
            request = input("Buyer: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not request:
            continue
        if request.lower() in ("quit", "exit", "q"):
            break
        handle(catalog, request)
        print()

    audit("session_end", "buyer left")
    print("Bye.")


if __name__ == "__main__":
    main()
