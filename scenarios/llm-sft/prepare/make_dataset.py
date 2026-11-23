"""TDP side: generate the provider's synthetic instruction data (example_id/prompt/completion JSONL).

Both providers contribute examples of one task - turning a free-text note into a JSON record:
* ``bank_a``: payment notes -> {amount, currency, merchant, date, method};
* ``shop_b``: order notes -> {order_id, quantity, item, city, date}.
The data is fully synthetic (deterministic per seed); it contains no real people or businesses.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MERCHANTS = [
    "Sharma Stores",
    "Green Leaf Cafe",
    "City Pharmacy",
    "Metro Fuels",
    "Lotus Books",
    "Blue Bay Grocers",
    "Sunrise Bakery",
    "Northwind Electronics",
    "Ganga Textiles",
    "Hilltop Hardware",
]
METHODS = ["UPI", "debit card", "credit card", "net banking"]
ITEMS = [
    "blue cotton shirt",
    "steel water bottle",
    "wireless mouse",
    "yoga mat",
    "ceramic mug",
    "running shoes",
    "desk lamp",
    "backpack",
    "notebook set",
    "phone charger",
]
CITIES = ["Pune", "Chennai", "Jaipur", "Kochi", "Lucknow", "Indore", "Guwahati", "Mysuru"]


def _date(rng: random.Random) -> date:
    return date(2025, 1, 1) + timedelta(days=rng.randrange(600))


def bank_note(rng: random.Random) -> dict:
    amount = round(rng.uniform(20, 25_000), 2)
    d, merchant, method = _date(rng), rng.choice(MERCHANTS), rng.choice(METHODS)
    templates = [
        f"Paid INR {amount:.2f} to {merchant} on {d:%d %B %Y} via {method}.",
        f"{method.upper()} payment of Rs {amount:.2f} at {merchant}, {d:%d/%m/%Y}.",
        f"Debit: {amount:.2f} INR - {merchant} - {d:%Y-%m-%d} ({method})",
    ]
    record = {
        "amount": amount,
        "currency": "INR",
        "merchant": merchant,
        "date": d.isoformat(),
        "method": method,
    }
    return {"prompt": rng.choice(templates), "completion": json.dumps(record)}


def shop_note(rng: random.Random) -> dict:
    order = f"A{rng.randrange(10_000, 99_999)}"
    qty, item, city, d = rng.randrange(1, 9), rng.choice(ITEMS), rng.choice(CITIES), _date(rng)
    templates = [
        f"Order #{order}: {qty} x {item}, delivered to {city} on {d:%d %b %Y}.",
        f"Shipped {qty} {item}(s) to {city} ({d:%Y-%m-%d}), order {order}.",
        f"{city} delivery {d:%d/%m/%Y} - order {order} - qty {qty} - {item}",
    ]
    record = {"order_id": order, "quantity": qty, "item": item, "city": city, "date": d.isoformat()}
    return {"prompt": rng.choice(templates), "completion": json.dumps(record)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tdp", required=True, choices=["bank_a", "shop_b"])
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--out", help="dataset directory (default: ../data/<tdp>/preprocessed)")
    args = parser.parse_args()
    rng = random.Random(f"llm-sft/{args.tdp}")  # noqa: S311 - deterministic synthetic data, not crypto
    make = bank_note if args.tdp == "bank_a" else shop_note
    out = Path(args.out) if args.out else DATA / args.tdp / "preprocessed"
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{args.tdp}_notes.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(args.rows):
            fh.write(json.dumps({"example_id": f"{args.tdp}-{i:06d}", **make(rng)}) + "\n")
    print(f"wrote {args.rows} examples to {out}")


if __name__ == "__main__":
    main()
