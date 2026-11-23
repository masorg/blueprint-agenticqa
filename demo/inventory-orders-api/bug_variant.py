"""Documented reference for the injected demo bug -- NOT imported by the app.

The Agentic QA demo tells a story: a PR claims "no behavior change intended"
while actually introducing an off-by-one in the stock check. This file records
that exact change so the demo is reproducible and reviewable.

The mutation, in ``create_order`` (``app.py``):

    # before (correct): ordering exactly the remaining stock is allowed
    if item["stock"] < quantity:
        raise ValueError("insufficient stock")

    # after (buggy): ordering exactly the remaining stock is now rejected
    if item["stock"] <= quantity:
        raise ValueError("insufficient stock")

Expected effect on the test pyramid:
  * Unit layer      : 6/7 pass, 1 fails  -> test_order_exact_remaining_stock_succeeds
  * Property layer  : 3/4 pass, 1 fails  -> test_property_any_valid_order_must_succeed
  * Adversarial     : 7/7 still pass      -> the bug is a business-rule issue,
                                             not a safety issue, so this layer
                                             correctly does NOT flag it.

To reproduce without editing code, run the suite with ``INVENTORY_ORDERS_BUG=1``
(see ``app.py``). In a real PR the change would arrive as the diff above.


Two more toggles, added for the evaluation harness's benchmark corpus
(``agenticqa.eval.corpus``) so the pyramid's "different bug classes get caught
by different layers" claim has more than one data point:

INVENTORY_ORDERS_BUG_PROPERTY=1 -- stale-bound stock check
--------------------------------------------------------------------------
    # before (correct): checked against the LIVE remaining stock
    insufficient = item["stock"] < quantity

    # after (buggy): checked against the stock captured at item-creation
    insufficient = item.get("_original_stock", item["stock"]) < quantity

Right for a single order; wrong from the second order onward against the
same item (a classic "checked a cached/original limit instead of the current
remaining balance" bug). Every unit test in this suite places exactly one
order per item, so this bug is structurally invisible to unit tests, not
just unlucky with the chosen numbers. Hypothesis' sequential-order property
test (``test_property_stock_never_negative``, which places a *list* of
orders against one item) finds it every run and shrinks to a 2-order case.

  Expected effect on the test pyramid:
    * Unit layer      : 7/7 pass -- structurally cannot see it (one order each)
    * Property layer  : 3/4 pass, 1 fails -> test_property_stock_never_negative
    * Adversarial     : 7/7 still pass -- not a safety/input-validation issue

INVENTORY_ORDERS_BUG_ADVERSARIAL=1 -- HTTP-layer input-validation regression
--------------------------------------------------------------------------
    # before (correct): AddItemRequest.name capped at 256 chars
    name: str = Field(min_length=1, max_length=256)

    # after (buggy): cap effectively removed (100_000)
    name: str = Field(min_length=1, max_length=100_000)

The domain functions unit/property tests call directly (``add_item`` etc.)
never enforce a name-length bound -- that's an HTTP-boundary concern -- so
this bug is invisible to both of those layers by construction. Only the
adversarial layer's HTTP test sees it.

  Expected effect on the test pyramid:
    * Unit layer      : 7/7 pass -- doesn't go through HTTP validation
    * Property layer  : 4/4 pass -- same reason
    * Adversarial     : 6/7 pass, 1 fails -> test_oversized_name_rejected
"""
