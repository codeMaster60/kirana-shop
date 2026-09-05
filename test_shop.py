"""End-to-end flow tests for shop.py with the stock prompt stubbed.

Every case runs a real buyer string through handle() - resolver included - with
ask_owner() forced to "out of stock". That is the harshest setting for the bug
we're guarding: it drives BOTH refusal paths (not_stocked and out-of-stock) and
makes every run produce a refusal plus, usually, a suggestion.

The invariant under test: no single response may refuse an item and suggest that
same item.
"""

import io
import sys
import contextlib

import shop

CASES = [
    # (buyer input, expectation)
    #   "stocked"   - the catalog clearly has this; must NOT be called not_stocked
    #   "unstocked" - the shop genuinely has none of this
    #   "any"       - shape test only (question / catalog / vague)

    # --- brand names, bare ---
    ("truke earphones",                 "stocked"),
    ("truke",                           "stocked"),
    ("amul",                            "stocked"),
    ("vim",                             "stocked"),
    ("saffola",                         "stocked"),
    ("yoga bar",                        "stocked"),
    ("godrej hit",                      "stocked"),

    # --- misspellings ---
    ("truk earphone",                   "stocked"),
    ("amull ghee",                      "stocked"),
    ("penut buttter",                   "stocked"),
    ("mosqito spray",                   "stocked"),
    ("musli",                           "stocked"),

    # --- plurals / partial names ---
    ("earbuds",                         "stocked"),
    ("earphones",                       "stocked"),
    ("wireless earbuds",                "stocked"),
    ("peanut butter",                   "stocked"),
    ("dishwash",                        "stocked"),
    ("room freshener",                  "stocked"),
    ("palm sugar",                      "stocked"),

    # --- categories / uses ---
    ("soap",                            "stocked"),
    ("something for cleaning",          "stocked"),
    ("breakfast",                       "stocked"),
    ("something for mosquitoes",        "stocked"),
    ("i need something sweet",          "stocked"),

    # --- vague asks ---
    ("i need something",                "any"),
    ("hello",                           "any"),
    ("asdkjfh",                         "any"),

    # --- questions ---
    ("how much is the ghee",            "any"),
    ("what do you have",                "any"),
    ("does the peanut butter have sugar", "any"),
    ("what all do u have",              "any"),

    # --- genuinely not stocked ---
    ("chocolates",                      "unstocked"),
    ("a pen",                           "unstocked"),
    ("cigarettes",                      "unstocked"),
    ("petrol",                          "unstocked"),
]


def run_case(catalog, text):
    """Run one request with the stock prompt stubbed to 'out of stock'."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        record = shop.handle(catalog, text)
    return record, buf.getvalue()


def check(catalog, text, expectation, record, output):
    """Return a list of failure strings for this case."""
    names = {it["id"]: it["name"] for it in catalog}
    fails = []

    refused = set(record.get("refused", []))
    suggested = set(record.get("suggested", []))

    # THE invariant: a refusal and a suggestion for the same item.
    both = refused & suggested
    if both:
        fails.append(
            "refused and suggested the same item(s): "
            + ", ".join(names[i] for i in sorted(both))
        )

    # Same check at the text level, in case a branch prints outside the record.
    if "Closest we have:" in output:
        line = next(l for l in output.splitlines() if "Closest we have:" in l)
        for cid, name in names.items():
            if name in line and cid in refused:
                fails.append(f"printed a suggestion for the refused item: {name}")

    # A matched item may never be refused as "we don't stock that".
    if "we don't stock that" in output.lower() and record.get("matched"):
        fails.append(
            "said 'we don't stock that' while having matched "
            + ", ".join(names[i] for i in record["matched"])
        )

    # not_stocked must be unreachable whenever the resolver found anything.
    if record.get("intent") == "not_stocked" and record.get("matched"):
        fails.append("intent not_stocked with a non-empty match set")

    # Expectation about this particular input.
    if expectation == "stocked":
        if record.get("intent") == "not_stocked":
            fails.append("refused as not stocked, but the catalog carries it")
        elif not record.get("matched"):
            fails.append(f"resolved no catalog item (intent={record.get('intent')})")

    return fails


def main():
    catalog = shop.load_catalog()

    # Stub the one interactive prompt: always "out of stock". This keeps the
    # test from creating real payment links AND exercises the suggestion path.
    shop.ask_owner = lambda item: False

    failures = []
    for text, expectation in CASES:
        try:
            record, output = run_case(catalog, text)
        except Exception as e:
            failures.append((text, [f"raised {type(e).__name__}: {e}"]))
            print(f"ERROR  {text!r}: {type(e).__name__}: {e}")
            continue

        fails = check(catalog, text, expectation, record, output)
        status = "FAIL" if fails else "ok  "
        print(f'{status}  {text!r:42} intent={record.get("intent")} '
              f'matched={record.get("matched")} '
              f'refused={record.get("refused")} suggested={record.get("suggested")}')
        for f in fails:
            print(f"        - {f}")
        if fails:
            failures.append((text, fails))

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    if failures:
        print("\nFailing inputs:")
        for text, fails in failures:
            print(f"  {text!r}")
            for f in fails:
                print(f"      {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
