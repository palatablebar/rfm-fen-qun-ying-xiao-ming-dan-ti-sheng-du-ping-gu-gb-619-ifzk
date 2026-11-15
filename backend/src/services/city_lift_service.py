"""算法 C｜城市独立试算与发布前跨城调剂（纯 Python，确定性）。

与算法 B（``lift_service.allocate``，全量一套 lift/一个总预算）的关键区别：

1. **每城独立基线**：lift 的分母是「本城大盘购买率」，上海的高响应分群不再
   抬高成都、武汉的基线；每个城市用自己的人群表现独立排序、独立贪心。
2. **城市配额隔离**：每城只在自己的预算配额内分配，城市之间不抢占，
   人不够的城市花不完的钱先记为「本城闲置」，不会被别的城市当场拿走。
3. **发布前才调剂**：所有城市试算完成后，统一把闲置余额按全局效率
   （lift/人均成本）补给「人还够、钱不够」的城市，调剂过程留痕、可核对。
4. 输出每城的预算、人数、增量、未用余额，页面与导出按城市核对。

本模块不碰数据库，输入是 ``city_segments``（城市 × 分群的聚合字典），
便于单测与中断续跑（每个城市单独调一次 :func:`allocate_city`，结果可固化）。
"""
from __future__ import annotations

import math

from src.constants.enums import (
    CITY_ORDER,
    REALLOCATION_STATUS_LABELS,
    ReallocationStatus,
)
from src.constants.limits import (
    DEFAULT_CORE_SHARE,
    DEFAULT_ORDERS_PER_CONVERTER,
    DEFAULT_REACH_COST,
    DEFAULT_TAIL_FACTOR,
    LIFT_ELIGIBLE_MIN,
)
from src.services.lift_service import _round
from src.utils.errors import AppError


def _city_global_rate(rows: list[dict]) -> float:
    """本城大盘购买率：按各分群人数加权的会员历史响应率均值。"""
    total = sum(int(item.get("member_count") or 0) for item in rows)
    if total <= 0:
        return 0.0
    return sum(
        float(item.get("purchase_rate") or 0.0) * int(item.get("member_count") or 0)
        for item in rows
    ) / total


def allocate_city(
    city: str,
    segments: list[dict],
    *,
    budget: float,
    reach_cost: float = DEFAULT_REACH_COST,
    orders_per_converter: float = DEFAULT_ORDERS_PER_CONVERTER,
    tail_factor: float = DEFAULT_TAIL_FACTOR,
    eligible_min: float = LIFT_ELIGIBLE_MIN,
) -> dict:
    """单城独立试算：本地基线 lift + 本地配额内贪心分配。

    ``segments`` 内每项需要 ``code/name/member_count/purchase_rate/coupon_face/
    core_share/avg_ticket``（券面额已按城市覆盖过）。
    """
    if budget is None or not math.isfinite(float(budget)) or float(budget) <= 0:
        raise AppError("VALIDATION", 422, {"reason": f"{city} 城市配额必须大于 0"})
    budget = float(budget)
    usable = [item for item in segments if int(item.get("member_count") or 0) > 0]
    global_rate = _city_global_rate(usable)

    rows: list[dict] = []
    for item in usable:
        rate = float(item.get("purchase_rate") or 0.0)
        coupon_face = float(item.get("coupon_face") or 0.0)
        lift = (rate / global_rate) if global_rate > 0 else 0.0
        unit_cost = reach_cost + rate * coupon_face
        efficiency = (lift / unit_cost) if unit_cost > 0 else 0.0
        rows.append(
            {
                "city": city,
                "segment_code": item.get("code"),
                "segment_name": item.get("name"),
                "member_count": int(item.get("member_count") or 0),
                "purchase_rate": _round(rate, 6),
                "lift": _round(lift, 4),
                "coupon_face": _round(coupon_face, 2),
                "unit_cost": _round(unit_cost, 4),
                "efficiency": _round(efficiency, 4),
                "core_share": _round(float(item.get("core_share") or DEFAULT_CORE_SHARE), 4),
                "avg_ticket": _round(float(item.get("avg_ticket") or 0.0), 2),
                "_rate": rate,
                "_unit_cost": unit_cost,
                "_lift": lift,
            }
        )

    ordered = sorted(
        rows,
        key=lambda row: (-row["efficiency"], -row["lift"], row["segment_code"] or ""),
    )

    remaining = budget
    priority = 0
    for row in ordered:
        if row["_lift"] <= eligible_min:
            row.update(
                {
                    "status": "ineligible",
                    "priority": None,
                    "allocated": 0,
                    "budget": 0.0,
                    "inc_orders": 0,
                    "inc_gmv": 0.0,
                    "roi": 0.0,
                    "core_allocated": 0,
                    "tail_allocated": 0,
                    "reason": f"本城 lift {row['lift']:.2f} ≤ {eligible_min:.2f}，投券不产生增量",
                }
            )
            continue
        unit_cost = row["_unit_cost"]
        affordable = int(remaining / unit_cost) if unit_cost > 0 else row["member_count"]
        while unit_cost > 0 and (affordable + 1) * unit_cost <= remaining + 1e-6:
            affordable += 1
        while unit_cost > 0 and affordable > 0 and affordable * unit_cost > remaining + 1e-6:
            affordable -= 1
        allocated = min(row["member_count"], max(0, affordable))
        if allocated <= 0:
            need_full = _round(row["member_count"] * unit_cost, 2)
            row.update(
                {
                    "status": "deferred",
                    "priority": None,
                    "allocated": 0,
                    "budget": 0.0,
                    "inc_orders": 0,
                    "inc_gmv": 0.0,
                    "roi": 0.0,
                    "core_allocated": 0,
                    "tail_allocated": 0,
                    "reason": f"本城配额不足，覆盖该群全量还需 ¥{need_full:,.2f}",
                }
            )
            continue

        priority += 1
        spend = _round(allocated * unit_cost, 2)
        remaining = _round(remaining - spend, 2)
        core_cap = int(math.ceil(row["member_count"] * row["core_share"]))
        core_allocated = min(allocated, core_cap)
        tail_allocated = max(0, allocated - core_cap)
        effective_people = core_allocated + tail_allocated * tail_factor
        delta = max(0.0, row["_rate"] - global_rate)
        inc_converters = effective_people * delta
        inc_orders = int(round(inc_converters * orders_per_converter))
        inc_gmv = _round(inc_orders * row["avg_ticket"], 2)
        roi = _round(inc_gmv / spend, 2) if spend > 0 else 0.0
        row.update(
            {
                "status": "allocated",
                "priority": priority,
                "allocated": allocated,
                "budget": spend,
                "inc_orders": inc_orders,
                "inc_gmv": inc_gmv,
                "roi": roi,
                "core_allocated": core_allocated,
                "tail_allocated": tail_allocated,
                "effective_people": _round(effective_people, 2),
                "inc_converters": _round(inc_converters, 2),
                "reason": (
                    f"核心盘 {core_allocated} 人全效 + 溢出 {tail_allocated} 人 ×{tail_factor:.1f} 折减"
                    if tail_allocated
                    else f"核心盘 {core_allocated} 人全效（未触及折减）"
                ),
            }
        )

    return _build_city_result(city, ordered, budget, global_rate, remaining)


def _build_city_result(city: str, ordered: list[dict], budget: float,
                       global_rate: float, remaining: float) -> dict:
    allocated_rows = [row for row in ordered if row["status"] == "allocated"]
    audience_total = sum(row["allocated"] for row in allocated_rows)
    spend_total = _round(sum(row["budget"] for row in allocated_rows), 2)
    inc_orders_total = sum(row["inc_orders"] for row in allocated_rows)
    inc_gmv_total = _round(sum(row["inc_gmv"] for row in allocated_rows), 2)
    roi_total = _round(inc_gmv_total / spend_total, 2) if spend_total > 0 else 0.0
    clean_rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in ordered]
    # 该城可承接的增量名额：分群里还没被本城配额覆盖的人数（deferred + allocated 剩余容量）
    headroom = sum(
        max(0, int(row["member_count"]) - int(row["allocated"]))
        for row in ordered
        if row["status"] in {"allocated", "deferred"} and row["_lift"] > LIFT_ELIGIBLE_MIN
    )
    return {
        "city": city,
        "quota_budget": _round(budget, 2),
        "global_rate": _round(global_rate, 6),
        "segments": clean_rows,
        "totals": {
            "audience": audience_total,
            "spend": spend_total,
            # 城市配额内花不完的钱：发布前进入统一调剂池
            "idle_budget": _round(max(0.0, budget - spend_total), 2),
            "inc_orders": inc_orders_total,
            "inc_gmv": inc_gmv_total,
            "roi": roi_total,
            "headroom": headroom,
            "segments_allocated": len(allocated_rows),
            "segments_deferred": len([row for row in ordered if row["status"] == "deferred"]),
            "segments_ineligible": len([row for row in ordered if row["status"] == "ineligible"]),
        },
        "remaining": _round(remaining, 2),
    }


def reallocate_idle_budget(city_results: list[dict], *, tail_factor: float = DEFAULT_TAIL_FACTOR,
                           orders_per_converter: float = DEFAULT_ORDERS_PER_CONVERTER) -> dict:
    """发布前一次性跨城调剂（试算阶段城市配额保持不动）。

    规则（确定性）：
    1. 汇总各城 ``idle_budget`` 形成调剂池（来源：本城人不够、配额花不完）。
    2. 把所有城市里「lift>1 且还没覆盖满」的分群行，按 efficiency 降序
       （同分 lift 降序、城市序、分群编码）排队。
    3. 逐行用调剂池余额承接剩余人数，按该群人均成本扣费，直到池子用完或无人可接。
    4. 每行记录调剂状态（kept/surplus/received/idle）与资金来源城市，全程留痕。
    """
    pool = _round(sum(item["totals"]["idle_budget"] for item in city_results), 2)
    pool_initial = pool

    # 城市级资金来源：先标 surplus（本城闲置），随调剂逐步转为实际调出
    city_ledger: dict[str, dict] = {}
    for result in city_results:
        city_ledger[result["city"]] = {
            "city": result["city"],
            "quota_budget": result["quota_budget"],
            "local_spend": result["totals"]["spend"],
            "idle_budget": result["totals"]["idle_budget"],
            "transferred_out": 0.0,
            "received_budget": 0.0,
            "received_members": 0,
        }

    # 可承接名额的分群行（保留对城市结果行的引用，直接在其上累加）
    candidates: list[tuple[dict, dict]] = []
    for result in city_results:
        for row in result["segments"]:
            if row["status"] == "ineligible":
                continue
            spare = int(row["member_count"]) - int(row["allocated"])
            if spare > 0 and row["lift"] > LIFT_ELIGIBLE_MIN:
                candidates.append((result, row))
    city_rank = {city: index for index, city in enumerate(CITY_ORDER)}
    candidates.sort(
        key=lambda pair: (
            -float(pair[1]["efficiency"]),
            -float(pair[1]["lift"]),
            city_rank.get(pair[0]["city"], 99),
            pair[1]["segment_code"] or "",
        )
    )

    transfers: list[dict] = []
    for result, row in candidates:
        if pool <= 1e-6:
            break
        spare = int(row["member_count"]) - int(row["allocated"])
        unit_cost = float(row["unit_cost"])
        if unit_cost <= 0 or spare <= 0:
            continue
        affordable = int(pool / unit_cost)
        take = min(spare, max(0, affordable))
        if take <= 0:
            continue
        extra_spend = _round(take * unit_cost, 2)
        pool = _round(pool - extra_spend, 2)

        # 资金来源：按城市序从有闲置余额的城市扣（确定性的 FIFO 出钱顺序）
        funded: list[dict] = []
        need = extra_spend
        for donor_city in CITY_ORDER:
            if need <= 1e-6:
                break
            ledger = city_ledger.get(donor_city)
            if not ledger:
                continue
            available = _round(
                ledger["idle_budget"] - ledger["transferred_out"], 2
            )
            if available <= 0:
                continue
            part = _round(min(need, available), 2)
            ledger["transferred_out"] = _round(ledger["transferred_out"] + part, 2)
            funded.append({"city": donor_city, "amount": part})
            need = _round(need - part, 2)

        # 在该分群行上叠加调剂名额与增量（沿用边际折进口径）
        old_alloc = int(row["allocated"])
        new_alloc = old_alloc + take
        core_cap = int(math.ceil(int(row["member_count"]) * float(row["core_share"])))
        core_allocated = min(new_alloc, core_cap)
        tail_allocated = max(0, new_alloc - core_cap)
        effective_people = core_allocated + tail_allocated * tail_factor
        delta = max(0.0, float(row["purchase_rate"]) - float(result["global_rate"]))
        inc_converters = effective_people * delta
        inc_orders = int(round(inc_converters * orders_per_converter))
        inc_gmv = _round(inc_orders * float(row["avg_ticket"]), 2)
        new_spend = _round(float(row["budget"]) + extra_spend, 2)
        row["allocated"] = new_alloc
        row["budget"] = new_spend
        row["core_allocated"] = core_allocated
        row["tail_allocated"] = tail_allocated
        row["effective_people"] = _round(effective_people, 2)
        row["inc_converters"] = _round(inc_converters, 2)
        row["inc_orders"] = inc_orders
        row["inc_gmv"] = inc_gmv
        row["roi"] = _round(inc_gmv / new_spend, 2) if new_spend > 0 else 0.0
        row["reallocation"] = ReallocationStatus.RECEIVED.value
        row["reallocation_status_label"] = REALLOCATION_STATUS_LABELS[ReallocationStatus.RECEIVED.value]
        row["received_members"] = take
        row["received_budget"] = extra_spend
        row["funded_by"] = funded
        transfers.append(
            {
                "city": result["city"],
                "segment_code": row["segment_code"],
                "segment_name": row["segment_name"],
                "received_members": take,
                "received_budget": extra_spend,
                "funded_by": funded,
            }
        )

    # 没接收过调剂的已分配行标记 kept
    for result in city_results:
        for row in result["segments"]:
            if row["status"] == "allocated" and "reallocation" not in row:
                row["reallocation"] = ReallocationStatus.KEPT.value
                row["reallocation_status_label"] = REALLOCATION_STATUS_LABELS[ReallocationStatus.KEPT.value]
                row["received_members"] = 0
                row["received_budget"] = 0.0

    # 重算每城汇总（含调剂后），并给出城市级未用余额
    city_summaries = []
    for result in city_results:
        allocated_rows = [row for row in result["segments"] if row["status"] == "allocated"]
        spend_after = _round(sum(float(row["budget"]) for row in allocated_rows), 2)
        audience_after = sum(int(row["allocated"]) for row in allocated_rows)
        inc_orders_after = sum(int(row["inc_orders"]) for row in allocated_rows)
        inc_gmv_after = _round(sum(float(row["inc_gmv"]) for row in allocated_rows), 2)
        ledger = city_ledger[result["city"]]
        ledger["received_budget"] = _round(
            sum(float(row.get("received_budget") or 0.0) for row in allocated_rows), 2
        )
        ledger["received_members"] = sum(int(row.get("received_members") or 0) for row in allocated_rows)
        # 城市最终未用余额 = 配额 - 本地花费 - 调出给其它城市的金额
        # （调入资金已含在 spend_after 里，花在本城会员身上，不再从本城配额扣）
        unused = _round(
            result["quota_budget"] - ledger["local_spend"] - ledger["transferred_out"], 2
        )
        unused = max(0.0, unused)
        ledger["unused_budget"] = unused
        ledger["spend"] = spend_after
        ledger["audience"] = audience_after
        ledger["inc_orders"] = inc_orders_after
        ledger["inc_gmv"] = inc_gmv_after
        ledger["roi"] = _round(inc_gmv_after / spend_after, 2) if spend_after > 0 else 0.0
        if ledger["idle_budget"] > 0 and ledger["transferred_out"] > 0:
            ledger["status_label"] = "部分闲置已调出"
        elif ledger["idle_budget"] > 0:
            ledger["status_label"] = REALLOCATION_STATUS_LABELS[ReallocationStatus.SURPLUS.value]
        elif ledger["received_budget"] > 0:
            ledger["status_label"] = REALLOCATION_STATUS_LABELS[ReallocationStatus.RECEIVED.value]
        else:
            ledger["status_label"] = REALLOCATION_STATUS_LABELS[ReallocationStatus.KEPT.value]
        city_summaries.append(ledger)

    totals = {
        "budget": _round(sum(item["quota_budget"] for item in city_results), 2),
        "spend": _round(sum(item["spend"] for item in city_summaries), 2),
        "audience": sum(item["audience"] for item in city_summaries),
        "inc_orders": sum(item["inc_orders"] for item in city_summaries),
        "inc_gmv": _round(sum(item["inc_gmv"] for item in city_summaries), 2),
        "idle_budget": _round(sum(item["unused_budget"] for item in city_summaries), 2),
        "pool_initial": pool_initial,
        "pool_remaining": pool,
        "transfers": len(transfers),
    }
    totals["roi"] = _round(totals["inc_gmv"] / totals["spend"], 2) if totals["spend"] > 0 else 0.0
    return {
        "cities": city_results,
        "city_summaries": city_summaries,
        "transfers": transfers,
        "totals": totals,
        "log": (
            f"发布前调剂：闲置池 ¥{pool_initial:,.2f}，实际调出 ¥{_round(pool_initial - pool, 2):,.2f}，"
            f"发生 {len(transfers)} 笔跨城名额承接，调剂后未用余额 ¥{totals['idle_budget']:,.2f}"
        ),
    }
