"""Layer 2 -- property-based tests (Hypothesis) over invariants.

4 tests. All pass against the clean app. Exactly one
(`test_property_any_valid_order_must_succeed`) fails against the bug variant,
with Hypothesis reporting the minimal failing case (quantity == stock).

Each test resets the in-memory store at the top of the example body because a
single test function runs many Hypothesis examples without re-triggering the
autouse fixture.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import app as demo_app
from app import add_item, create_order, get_item


@settings(deadline=None, max_examples=100)
@given(stock=st.integers(min_value=0, max_value=1000), quantities=st.lists(st.integers(min_value=1, max_value=200), max_size=20))
def test_property_stock_never_negative(stock, quantities):
    demo_app.reset_state()
    add_item("sku", "Item", stock)
    for q in quantities:
        try:
            create_order("sku", q)
        except ValueError:
            pass  # rejected orders are fine
        assert get_item("sku")["stock"] >= 0


@settings(deadline=None, max_examples=100)
@given(stock=st.integers(min_value=0, max_value=1000), over=st.integers(min_value=1, max_value=1000))
def test_property_orders_are_atomic(stock, over):
    # An order that cannot be fully satisfied must not change stock at all.
    demo_app.reset_state()
    add_item("sku", "Item", stock)
    quantity = stock + over  # strictly greater than stock in both variants
    with pytest.raises(ValueError):
        create_order("sku", quantity)
    assert get_item("sku")["stock"] == stock


@settings(deadline=None, max_examples=100)
@given(stock=st.integers(min_value=0, max_value=1000), quantity=st.integers(max_value=0))
def test_property_nonpositive_quantity_always_rejected(stock, quantity):
    demo_app.reset_state()
    add_item("sku", "Item", stock)
    with pytest.raises(ValueError):
        create_order("sku", quantity)


@settings(deadline=None, max_examples=100)
@given(data=st.data())
def test_property_any_valid_order_must_succeed(data):
    # For any 0 < quantity <= stock, the order must succeed. The bug variant
    # rejects the quantity == stock case, so Hypothesis finds and shrinks to it.
    demo_app.reset_state()
    stock = data.draw(st.integers(min_value=1, max_value=1000))
    quantity = data.draw(st.integers(min_value=1, max_value=stock))
    add_item("sku", "Item", stock)
    order = create_order("sku", quantity)  # must not raise
    assert order["quantity"] == quantity
    assert get_item("sku")["stock"] == stock - quantity
