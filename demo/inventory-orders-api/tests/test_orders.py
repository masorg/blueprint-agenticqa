"""Layer 1 -- deterministic unit tests against the core domain functions.

7 tests. All pass against the clean app. Exactly one
(`test_order_exact_remaining_stock_succeeds`) fails against the bug variant.
"""

import pytest

from app import add_item, create_order, get_item, get_order


def test_add_item_then_get_item():
    add_item("sku-1", "Widget", 10)
    item = get_item("sku-1")
    assert item["sku"] == "sku-1"
    assert item["name"] == "Widget"
    assert item["stock"] == 10


def test_add_item_rejects_negative_stock():
    with pytest.raises(ValueError):
        add_item("sku-neg", "Bad", -1)


def test_create_order_reduces_stock():
    add_item("sku-2", "Gadget", 10)
    order = create_order("sku-2", 3)
    assert order["quantity"] == 3
    assert get_item("sku-2")["stock"] == 7


def test_create_order_insufficient_stock_rejected():
    add_item("sku-3", "Gizmo", 5)
    with pytest.raises(ValueError):
        create_order("sku-3", 6)  # strictly more than stock -> rejected in both variants


def test_order_exact_remaining_stock_succeeds():
    # Ordering exactly the remaining stock must succeed. The bug variant
    # (<= instead of <) rejects this, so this is the unit test that fails.
    add_item("sku-4", "Doohickey", 4)
    order = create_order("sku-4", 4)
    assert order["quantity"] == 4
    assert get_item("sku-4")["stock"] == 0


def test_create_order_nonpositive_quantity_rejected():
    add_item("sku-5", "Thing", 10)
    with pytest.raises(ValueError):
        create_order("sku-5", 0)


def test_get_order_returns_created_order():
    add_item("sku-6", "Contraption", 10)
    created = create_order("sku-6", 2)
    fetched = get_order(created["order_id"])
    assert fetched == created
