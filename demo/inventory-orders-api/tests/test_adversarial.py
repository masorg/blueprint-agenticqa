"""Layer 5 -- adversarial / safety tests against the HTTP layer.

7 tests hitting the API with malformed / hostile input. All 7 pass against the
clean app AND against the bug variant -- the demo bug is a business-rule
off-by-one, not a safety issue, so this layer correctly does not flag it.
"""

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def _seed_item(sku="sku", stock=5):
    client.post("/items", json={"sku": sku, "name": "Item", "stock": stock})


def test_negative_quantity_rejected():
    _seed_item()
    resp = client.post("/orders", json={"sku": "sku", "quantity": -5})
    assert resp.status_code == 400


def test_huge_quantity_rejected():
    _seed_item(stock=5)
    resp = client.post("/orders", json={"sku": "sku", "quantity": 10**18})
    # Far more than stock -> insufficient stock in both variants.
    assert resp.status_code == 400


def test_sql_injection_style_sku_is_safe():
    resp = client.post("/orders", json={"sku": "'; DROP TABLE orders; --", "quantity": 1})
    # Unknown SKU -> a clean 404, never a 500 / crash.
    assert resp.status_code == 404


def test_type_mismatch_quantity_rejected():
    _seed_item()
    resp = client.post("/orders", json={"sku": "sku", "quantity": "not-a-number"})
    assert resp.status_code == 422  # pydantic validation


def test_oversized_name_rejected():
    resp = client.post("/items", json={"sku": "big", "name": "A" * 5000, "stock": 1})
    assert resp.status_code == 422  # exceeds max_length


def test_missing_required_field_rejected():
    resp = client.post("/orders", json={"sku": "sku"})  # no quantity
    assert resp.status_code == 422


def test_get_nonexistent_item_returns_404():
    resp = client.get("/items/does-not-exist")
    assert resp.status_code == 404
