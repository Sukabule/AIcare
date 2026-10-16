import hashlib
import random
import re
from typing import Annotated

from pydantic import Field

ShortText = Annotated[str, Field(min_length=1, max_length=128)]


def extract_order_id(query: str):
    match = re.search(
        r"(?:订单(?:号|编号)?|尾号|order\s*(?:id)?)\s*[:：#]?\s*([A-Za-z]{0,12}\d[A-Za-z0-9-]*)",
        query,
        re.I,
    )
    return match[1] if match else None


def order_snapshot(order_id: str):
    rng = seeded(order_id)
    return {
        "source": "mock",
        "order_id": order_id,
        "status": "已发货",
        "amount": rng.randrange(5900, 29900) / 100,
        "created_at": "2026-10-09 10:30:00",
        "product_name": rng.choice(["猫爬架", "全价猫粮", "宠物饮水机"]),
        "tracking_no": "SF" + hashlib.sha256(order_id.encode()).hexdigest()[:14].upper(),
    }


def list_user_orders(user_id: str):
    rng = seeded(user_id)
    ids = ["1001", *map(str, rng.sample(range(2000, 9999), 2))]
    return [order_snapshot(order_id) for order_id in ids]


def seeded(value: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(value.encode()).digest(), "big"))
