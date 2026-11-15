"""CSV 导出：会员名单 / 分群表 / 名单明细（UTF-8 BOM）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import (
    AUDIENCE_STATUS_LABELS,
    CAMPAIGN_STATUS_LABELS,
    MEMBER_TIER_LABELS,
    SEGMENT_LABELS,
)
from src.constants.limits import MAX_EXPORT_ROWS
from src.models.audience import Audience
from src.models.campaign import Campaign
from src.models.member import Member
from src.models.segment import Segment
from src.utils.csv_export import to_csv


def _stamp(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def members_csv(db: Session) -> bytes:
    rows = db.scalars(
        select(Member).order_by(Member.composite_score.desc().nullslast(), Member.id).limit(MAX_EXPORT_ROWS)
    ).all()
    headers = [
        "会员号", "姓名", "手机号", "城市", "会员等级", "入会日期", "最近下单",
        "R(天)", "窗口内单数", "窗口内金额", "R分", "F分", "M分", "综合分",
        "分群", "历史券响应率", "客单价",
    ]
    payload = [
        [
            item.member_no, item.name, item.phone, item.city,
            MEMBER_TIER_LABELS.get(item.tier, item.tier),
            item.joined_on.isoformat() if item.joined_on else "",
            _stamp(item.last_order_at),
            item.r_days if item.r_days is not None else "",
            item.orders_window,
            f"{item.amount_window or 0:.2f}",
            item.r_score if item.r_score is not None else "",
            item.f_score if item.f_score is not None else "",
            item.m_score if item.m_score is not None else "",
            f"{item.composite_score or 0:.2f}",
            SEGMENT_LABELS.get(item.segment_code or "", ""),
            f"{(item.purchase_rate or 0) * 100:.2f}%",
            f"{item.avg_ticket or 0:.2f}",
        ]
        for item in rows
    ]
    return to_csv(headers, payload)


def segments_csv(db: Session) -> bytes:
    rows = db.scalars(select(Segment).order_by(Segment.sort_order, Segment.id)).all()
    headers = [
        "分群编码", "分群", "规则", "人数", "占比", "平均R分", "平均F分", "平均M分",
        "平均R天数", "人均窗口金额", "购买率", "lift", "券面额", "核心盘占比", "启用",
    ]
    payload = [
        [
            item.code, item.name, item.rule_text, item.member_count, f"{(item.share or 0) * 100:.2f}%",
            f"{item.avg_r_score or 0:.2f}", f"{item.avg_f_score or 0:.2f}", f"{item.avg_m_score or 0:.2f}",
            f"{item.avg_r_days or 0:.1f}", f"{item.avg_amount_window or 0:.2f}",
            f"{(item.purchase_rate or 0) * 100:.2f}%", f"{item.lift or 0:.4f}",
            f"{item.coupon_face or 0:.2f}", f"{item.core_share or 0:.2f}",
            "启用" if item.enabled else "停用",
        ]
        for item in rows
    ]
    return to_csv(headers, payload)


def audience_csv(db: Session, audience_id: int | None = None) -> bytes:
    stmt = select(Audience).order_by(Audience.id.desc())
    if audience_id:
        stmt = select(Audience).where(Audience.id == audience_id)
    else:
        # 不指定名单时只导出正式版本，历史版本不进券平台
        stmt = stmt.where(Audience.current_version.is_(True))
    audiences = db.scalars(stmt).all()
    campaigns = {item.id: item for item in db.scalars(select(Campaign)).all()}
    headers = [
        "名单号", "版本", "活动", "名单状态", "城市", "分群", "分群lift", "会员号", "姓名", "手机号",
        "会员等级", "R分", "F分", "M分", "综合分", "历史券响应率", "该城该群分到人数", "该群预算",
        "该群预计增量单量", "该群ROI",
    ]
    payload: list[list] = []
    for audience in audiences:
        campaign = campaigns.get(audience.campaign_id)
        campaign_name = campaign.name if campaign else ""
        status_label = AUDIENCE_STATUS_LABELS.get(audience.status, audience.status)
        # 城市 × 分群的名额映射：从发布时固化的城市试算结果还原，保证导出与试算同源
        city_segment_quota = _city_segment_quota(audience)
        for city, seg_map in city_segment_quota.items():
            for segment_code, quota in seg_map.items():
                if quota <= 0:
                    continue
                members = db.scalars(
                    select(Member)
                    .where(Member.segment_code == segment_code, Member.city == city)
                    .order_by(Member.composite_score.desc().nullslast(), Member.id)
                    .limit(quota)
                ).all()
                seg_row = _find_segment_row(audience, segment_code)
                for member in members:
                    payload.append(
                        [
                            audience.audience_no, f"v{audience.version}", campaign_name, status_label,
                            city,
                            SEGMENT_LABELS.get(segment_code, segment_code),
                            f"{(seg_row or {}).get('lift', 0):.4f}",
                            member.member_no, member.name, member.phone,
                            MEMBER_TIER_LABELS.get(member.tier, member.tier),
                            member.r_score, member.f_score, member.m_score,
                            f"{member.composite_score or 0:.2f}",
                            f"{(member.purchase_rate or 0) * 100:.2f}%",
                            quota, f"{(seg_row or {}).get('budget', 0):.2f}",
                            (seg_row or {}).get("inc_orders", 0), f"{(seg_row or {}).get('roi', 0):.2f}",
                        ]
                    )
                    if len(payload) >= MAX_EXPORT_ROWS:
                        return to_csv(headers, payload)
    return to_csv(headers, payload)


def _city_segment_quota(audience: Audience) -> dict[str, dict[str, int]]:
    """从名单固化结果里取 城市→分群→名额（兼容旧名单：无城市结果时回退全局分群）。"""
    mapping: dict[str, dict[str, int]] = {}
    # 发布时 flatten 的 allocation 行内带 cities 明细（城市→名额），与试算同源
    for row in audience.allocation or []:
        for city_item in row.get("cities") or []:
            if city_item.get("status") != "allocated" or not city_item.get("allocated"):
                continue
            mapping.setdefault(city_item["city"], {})[row["segment_code"]] = int(city_item["allocated"])
    if mapping:
        return mapping
    # 兼容旧版名单（无城市维度）：按全局分群展开
    for row in audience.allocation or []:
        if row.get("status") != "allocated" or not row.get("allocated"):
            continue
        mapping.setdefault("全部城市", {})[row["segment_code"]] = int(row["allocated"])
    return mapping


def _find_segment_row(audience: Audience, segment_code: str) -> dict | None:
    for row in audience.allocation or []:
        if row.get("segment_code") == segment_code:
            return row
    return None


def city_reconciliation_csv(db: Session, campaign_id: int | None = None) -> bytes:
    """城市核对表：每城预算配额、人数、增量、未用余额（页面与导出同源）。"""
    from src.services import city_service

    stmt = select(Campaign).order_by(Campaign.id)
    if campaign_id:
        stmt = stmt.where(Campaign.id == campaign_id)
    campaigns = db.scalars(stmt).all()
    headers = [
        "活动", "城市", "配额权重", "预算配额", "会员池人数", "已发布占用预算", "已发布人数",
        "未用余额",
    ]
    payload: list[list] = []
    for campaign in campaigns:
        for row in city_service.reconcile_quotas(db, campaign):
            payload.append(
                [
                    campaign.name, row["city"], f"{row['budget_weight'] * 100:.2f}%",
                    f"{row['quota_budget']:.2f}", row["member_pool"],
                    f"{row['committed_spend']:.2f}", row["committed_members"],
                    f"{row['unused_budget']:.2f}",
                ]
            )
    return to_csv(headers, payload)


def campaigns_csv(db: Session) -> bytes:
    rows = db.scalars(select(Campaign).order_by(Campaign.id)).all()
    headers = ["活动编码", "活动", "渠道", "预算", "状态", "开始", "结束", "触达成本", "人均增量单量", "折减系数"]
    payload = [
        [
            item.code, item.name, item.channel, f"{item.budget:.2f}",
            CAMPAIGN_STATUS_LABELS.get(item.status, item.status),
            item.starts_on.isoformat() if item.starts_on else "",
            item.ends_on.isoformat() if item.ends_on else "",
            f"{item.reach_cost:.4f}", f"{item.orders_per_converter:.4f}", f"{item.tail_factor:.2f}",
        ]
        for item in rows
    ]
    return to_csv(headers, payload)
