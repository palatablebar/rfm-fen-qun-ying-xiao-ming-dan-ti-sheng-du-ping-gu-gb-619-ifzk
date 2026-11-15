"""算法 C（城市独立试算 + 发布前调剂）单测：本地基线、配额隔离、调剂留痕、确定性。"""
from __future__ import annotations

import pytest

from src.services import city_lift_service
from src.utils.errors import AppError


def _seg(city, code, count, rate, coupon=6.0, ticket=30.0, share=0.6):
    return {
        "code": code,
        "name": code.upper(),
        "member_count": count,
        "purchase_rate": rate,
        "coupon_face": coupon,
        "core_share": share,
        "avg_ticket": ticket,
    }


def test_city_baseline_is_local_not_global():
    # 上海整体高响应、成都整体低响应：同一个绝对购买率在两城的 lift 不同
    shanghai = [_seg("上海", "a", 1000, 0.060), _seg("上海", "b", 1000, 0.030)]
    chengdu = [_seg("成都", "a", 1000, 0.030), _seg("成都", "b", 1000, 0.015)]
    sh = city_lift_service.allocate_city("上海", shanghai, budget=100000)
    cd = city_lift_service.allocate_city("成都", chengdu, budget=100000)
    # 两城大盘率分别为 0.045 与 0.0225，a 群 lift 都为 1.333…，基线独立计算
    assert sh["global_rate"] == pytest.approx(0.045)
    assert cd["global_rate"] == pytest.approx(0.0225)
    assert sh["segments"][0]["lift"] == pytest.approx(cd["segments"][0]["lift"])


def test_city_quota_isolation_keeps_idle_budget_local():
    # 成都人不够（高响应群只有 50 人），大配额花不完；闲置余额在试算阶段留在本城
    chengdu = [
        _seg("成都", "a", 50, 0.050, coupon=5.0),
        _seg("成都", "b", 50, 0.010, coupon=5.0),
    ]
    result = city_lift_service.allocate_city("成都", chengdu, budget=5000)
    allocated = [row for row in result["segments"] if row["status"] == "allocated"]
    assert sum(row["allocated"] for row in allocated) == 50
    assert result["totals"]["idle_budget"] > 0
    assert result["totals"]["idle_budget"] == pytest.approx(5000 - result["totals"]["spend"])
    # 试算阶段没有任何跨城动作，闲置就是闲置
    assert result["totals"]["spend"] < 5000


def test_invalid_city_budget_raises():
    with pytest.raises(AppError):
        city_lift_service.allocate_city("武汉", [_seg("武汉", "a", 10, 0.05)], budget=0)


def _two_city_results():
    # 成都：人少配额大 → 产生闲置；武汉：人多配额小 → 有名额缺口
    chengdu = city_lift_service.allocate_city(
        "成都",
        [_seg("成都", "a", 50, 0.050, coupon=5.0), _seg("成都", "b", 50, 0.010, coupon=5.0)],
        budget=5000,
    )
    wuhan = city_lift_service.allocate_city(
        "武汉",
        [_seg("武汉", "a", 2000, 0.050, coupon=5.0), _seg("武汉", "b", 2000, 0.010, coupon=5.0)],
        budget=200,
    )
    return [chengdu, wuhan]


def test_reallocation_moves_idle_pool_with_deterministic_trace():
    first = city_lift_service.reallocate_idle_budget(_two_city_results())
    second = city_lift_service.reallocate_idle_budget(_two_city_results())
    assert first == second  # 确定性：同输入同输出（含资金来源留痕）

    summaries = {row["city"]: row for row in first["city_summaries"]}
    # 成都的闲置被调出，武汉调入余额承接了更多人
    assert summaries["成都"]["transferred_out"] > 0
    assert summaries["武汉"]["received_budget"] > 0
    assert summaries["武汉"]["received_members"] > 0
    # 调剂后武汉该群人数超过原配额可覆盖的规模，但不超过分群总人数
    wuhan_row = next(row for row in first["cities"] if row["city"] == "武汉")["segments"][0]
    assert wuhan_row["allocated"] <= 2000
    assert wuhan_row["reallocation"] == "received"
    assert wuhan_row["funded_by"], "跨城调剂必须记录资金来源城市"
    # 总花费不超过两城配额之和
    assert first["totals"]["spend"] <= first["totals"]["budget"] + 1e-6


def test_reallocation_never_exceeds_pool_or_headcount():
    result = city_lift_service.reallocate_idle_budget(_two_city_results())
    pool = result["totals"]["pool_initial"]
    transferred = sum(row["transferred_out"] for row in result["city_summaries"])
    received = sum(row["received_budget"] for row in result["city_summaries"])
    assert transferred == pytest.approx(received)
    assert transferred <= pool + 1e-6
    for city_result in result["cities"]:
        for row in city_result["segments"]:
            assert row["allocated"] <= row["member_count"]


def test_empty_city_is_skipped_safely():
    result = city_lift_service.reallocate_idle_budget(
        [
            {
                "city": "重庆",
                "quota_budget": 800.0,
                "global_rate": 0.0,
                "segments": [],
                "totals": {"audience": 0, "spend": 0.0, "idle_budget": 800.0,
                           "inc_orders": 0, "inc_gmv": 0.0, "roi": 0.0, "headroom": 0,
                           "segments_allocated": 0, "segments_deferred": 0, "segments_ineligible": 0},
                "remaining": 800.0,
            }
        ]
    )
    assert result["totals"]["audience"] == 0
    assert result["totals"]["idle_budget"] == pytest.approx(800.0)
