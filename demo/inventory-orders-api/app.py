"""In-memory Inventory & Orders REST API (FastAPI).

This is the flagship *demo target* for the Agentic QA pyramid. It is a small,
deterministic service with no external dependencies, chosen so a live demo can
show the full pyramid (unit -> property -> adversarial) light up and produce a
clear pass/fail story.

Two ways to consume it:
  * The core domain functions (`add_item`, `get_item`, `create_order`,
    `get_order`) are importable and are exercised directly by the unit and
    property layers.
  * The FastAPI `app` wraps those functions over HTTP and is exercised by the
    adversarial/safety layer.

Demo bug toggles
----------------
Three independent env-var toggles inject three DIFFERENT classes of bug, each
one structurally catchable by a different pyramid layer only -- see
``bug_variant.py`` for the full writeup used by the evaluation harness
(``agenticqa.eval.corpus``):

* ``INVENTORY_ORDERS_BUG=1`` -- off-by-one in the stock check (``<`` -> ``<=``).
  Caught by unit + property (both exercise a single order against known/random
  stock levels).
* ``INVENTORY_ORDERS_BUG_PROPERTY=1`` -- the stock check is compared against
  the ORIGINAL stock captured at item-creation time instead of the live
  remaining stock. Correct for a lone order; wrong from the second order
  onward against the same item, and can drive stock negative. Every unit test
  places exactly one order per item, so this is structurally invisible to
  them; only a property test that places a *sequence* of orders against one
  item (Hypothesis' `quantities=st.lists(...)`) can find it.
* ``INVENTORY_ORDERS_BUG_ADVERSARIAL=1`` -- removes the HTTP-layer bound on
  item name length. The domain functions (what unit/property call directly)
  never validate name length, so this is invisible to them; only the
  adversarial layer's HTTP-boundary test sees it.

In a real PR each of these would arrive as a diff, not an env var; the
toggles exist purely to make the demo/eval corpus turnkey and reproducible.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# In-memory state
# --------------------------------------------------------------------------- #
_items: dict[str, dict] = {}
_orders: dict[str, dict] = {}
_order_seq: dict[str, int] = {"n": 0}


def reset_state() -> None:
    """Clear all state. Tests call this between cases; not exposed over HTTP."""
    _items.clear()
    _orders.clear()
    _order_seq["n"] = 0


def _bug_enabled() -> bool:
    return os.environ.get("INVENTORY_ORDERS_BUG", "").strip() in {"1", "true", "yes"}


def _bug_property_enabled() -> bool:
    return os.environ.get("INVENTORY_ORDERS_BUG_PROPERTY", "").strip() in {"1", "true", "yes"}


def _bug_adversarial_enabled() -> bool:
    return os.environ.get("INVENTORY_ORDERS_BUG_ADVERSARIAL", "").strip() in {"1", "true", "yes"}


# Read once at import time: this app is always started fresh in its own
# per-run venv/subprocess, so an import-time read is equivalent to a
# per-run toggle without making the request schema dynamic per-call.
_NAME_MAX_LENGTH = 100_000 if _bug_adversarial_enabled() else 256


# --------------------------------------------------------------------------- #
# Core domain functions
# --------------------------------------------------------------------------- #
def add_item(sku: str, name: str, stock: int) -> dict:
    if not sku:
        raise ValueError("sku is required")
    if stock < 0:
        raise ValueError("stock cannot be negative")
    item = {"sku": sku, "name": name, "stock": int(stock)}
    if _bug_property_enabled():
        item["_original_stock"] = int(stock)
    _items[sku] = item
    return dict(item)


def get_item(sku: str) -> dict:
    item = _items.get(sku)
    if item is None:
        raise KeyError(sku)
    return dict(item)


def create_order(sku: str, quantity: int) -> dict:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    item = _items.get(sku)
    if item is None:
        raise KeyError(sku)

    # --- stock check: the line the demo bug toggles mutate -------------------
    # Correct behaviour: reject only when there is strictly not enough LIVE
    # remaining stock, so ordering exactly the remaining stock is allowed.
    if _bug_property_enabled():
        # BUG variant: checks against the stock captured at item-creation
        # time, not the live remaining stock. Right for a lone order; wrong
        # from the second order onward against the same item.
        insufficient = item.get("_original_stock", item["stock"]) < quantity
    elif _bug_enabled():
        insufficient = item["stock"] <= quantity  # BUG variant (off-by-one)
    else:
        insufficient = item["stock"] < quantity  # correct
    # ------------------------------------------------------------------------
    if insufficient:
        raise ValueError("insufficient stock")

    item["stock"] -= quantity
    _order_seq["n"] += 1
    order_id = f"ord-{_order_seq['n']}"
    order = {"order_id": order_id, "sku": sku, "quantity": int(quantity)}
    _orders[order_id] = order
    return dict(order)


def get_order(order_id: str) -> dict:
    order = _orders.get(order_id)
    if order is None:
        raise KeyError(order_id)
    return dict(order)


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
app = FastAPI(title="Inventory & Orders API", version="1.0.0")


class AddItemRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=_NAME_MAX_LENGTH)
    stock: int = Field(ge=0)


class CreateOrderRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/items", status_code=201)
def http_add_item(req: AddItemRequest) -> dict:
    try:
        return add_item(req.sku, req.name, req.stock)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/items/{sku}")
def http_get_item(sku: str) -> dict:
    try:
        return get_item(sku)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc


@app.post("/orders", status_code=201)
def http_create_order(req: CreateOrderRequest) -> dict:
    try:
        return create_order(req.sku, req.quantity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc


@app.get("/orders/{order_id}")
def http_get_order(order_id: str) -> dict:
    try:
        return get_order(order_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc
