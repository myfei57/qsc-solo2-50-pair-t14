"""模具台账汇总。

控制台与调度都要一份按制品型号分组的模具概览：多少台在用、多少台报废、
各代际分布在哪些模具上。汇总只读台账，不改变任何状态。
"""

from __future__ import annotations

from .registry import MoldRegistry


def summarize_molds(registry: MoldRegistry) -> dict:
    """按制品型号统计模具数量与代际分布。"""

    records = registry.describe()
    by_product: dict[str, dict] = {}
    for record in records:
        bucket = by_product.setdefault(
            record["product_code"], {"active": 0, "retired": 0, "generations": []}
        )
        bucket["active" if record["status"] == "active" else "retired"] += 1
        bucket["generations"].append(record["generation"])
    return {
        "total": len(records),
        "active": sum(1 for record in records if record["status"] == "active"),
        "retired": sum(1 for record in records if record["status"] != "active"),
        "by_product": by_product,
    }
