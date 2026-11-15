"""城市维度的数据聚合、配额管理与发布前核对。

- 城市 × 分群的人群表现直接从会员事实表聚合（人数、本地购买率、本地客单价），
  券面额取分群快照并按 ``CITY_COUPON_FACE`` 做城市覆盖。
- 城市配额按活动预算 × ``CITY_BUDGET_WEIGHTS`` 切分并持久化到 ``city_quotas``，
  试算只读取、不占用；发布成功才回写 committed_*。
- :func:`reconciliation` 给出页面与导出共用的城市核对口径。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    CITY_BUDGET_WEIGHTS,
    CITY_COUPON_FACE,
    CITY_ORDER,
)
from src.models.city_quota import CityQuota
from src.models.member import Member
from src.models.segment import Segment


def list_cities() -> list[str]:
    """系统覆盖的城市（固定顺序）。"""
    return list(CITY_ORDER)


def ensure_city_quotas(db: Session, campaign) -> list[CityQuota]:
    """按权重把活动预算切成城市配额（幂等：已存在则原样返回，不覆盖运营调整）。"""
    existing = {row.city: row for row in db.scalars(
        select(CityQuota).where(CityQuota.campaign_id == campaign.id)
    ).all()}
    if existing:
        return [existing[city] for city in CITY_ORDER if city in existing]

    weight_sum = sum(CITY_BUDGET_WEIGHTS.values())
    rows: list[CityQuota] = []
    allocated = 0.0
    for index, city in enumerate(CITY_ORDER):
        weight = CITY_BUDGET_WEIGHTS[city] / weight_sum
        if index == len(CITY_ORDER) - 1:
            # 末城兜底吸收四舍五入误差，保证配额之和精确等于活动预算
            budget = round(float(campaign.budget) - allocated, 2)
        else:
            budget = round(float(campaign.budget) * weight, 2)
            allocated = round(allocated + budget, 2)
        quota = CityQuota(
            campaign_id=campaign.id,
            city=city,
            budget_weight=round(weight, 6),
            budget=max(0.0, budget),
        )
        db.add(quota)
        rows.append(quota)
    db.flush()
    return rows


def _segment_meta(db: Session) -> dict[str, Segment]:
    return {row.code: row for row in db.scalars(select(Segment)).all()}


def city_segment_matrix(db: Session, *, only_enabled: bool = True) -> dict[str, list[dict]]:
    """聚合 城市 × 分群 的本地人群表现（算法 C 的事实输入）。

    购买率与客单价取该城该群会员的均值（同一份会员事实，不写死），
    人数为 0 的分群不输出，避免空群参与排序。
    """
    segment_meta = _segment_meta(db)
    enabled_codes = {code for code, row in segment_meta.items() if row.enabled} if only_enabled else set(segment_meta)

    rows = db.execute(
        select(
            Member.city,
            Member.segment_code,
            func.count(Member.id),
            func.avg(Member.purchase_rate),
            func.avg(Member.avg_ticket),
        )
        .where(Member.segment_code.is_not(None))
        .group_by(Member.city, Member.segment_code)
    ).all()

    matrix: dict[str, list[dict]] = {city: [] for city in CITY_ORDER}
    for city, code, count, avg_rate, avg_ticket in rows:
        if city not in matrix or code not in enabled_codes or not count:
            continue
        meta = segment_meta.get(code)
        if meta is None:
            continue
        coupon_face = float(meta.coupon_face or 0.0)
        if city in CITY_COUPON_FACE and code in CITY_COUPON_FACE[city]:
            coupon_face = float(CITY_COUPON_FACE[city][code])
        matrix[city].append(
            {
                "code": code,
                "name": meta.name,
                "member_count": int(count),
                "purchase_rate": round(float(avg_rate or 0.0), 6),
                "coupon_face": round(coupon_face, 2),
                "core_share": float(meta.core_share or 0.6),
                "avg_ticket": round(float(avg_ticket or 0.0), 2),
            }
        )
    for city in matrix:
        matrix[city].sort(key=lambda item: item["code"])
    return matrix


def city_member_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Member.city, func.count(Member.id)).group_by(Member.city)
    ).all()
    counts = {city: 0 for city in CITY_ORDER}
    for city, count in rows:
        if city in counts:
            counts[city] = int(count)
    return counts


def reconcile_quotas(db: Session, campaign) -> list[dict]:
    """页面与导出共用的城市核对口径：配额 / 已发布占用 / 未用余额。"""
    quotas = ensure_city_quotas(db, campaign)
    counts = city_member_counts(db)
    return [
        {
            "city": quota.city,
            "budget_weight": round(quota.budget_weight or 0.0, 4),
            "quota_budget": round(quota.budget or 0.0, 2),
            "committed_spend": round(quota.committed_spend or 0.0, 2),
            "committed_members": int(quota.committed_members or 0),
            "unused_budget": round((quota.budget or 0.0) - (quota.committed_spend or 0.0), 2),
            "member_pool": counts.get(quota.city, 0),
        }
        for quota in quotas
    ]
