"""名单控制器：城市生成入口、确认与发布审计。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, audience_service, generation_service


def list_audiences(db: Session, **filters) -> dict:
    return audience_service.list_audiences(db, **filters)


def get_audience(db: Session, audience_id: int) -> dict:
    audience = audience_service.get_audience(db, audience_id)
    campaign = audience_service.get_campaign(db, audience.campaign_id)
    return audience_service.serialize_audience(audience, campaign)


def generate(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    """旧入口保留：内部改为「创建城市生成任务 → 试算 → 一次性发布」。

    新前端走 /api/generations（可续跑、可先试算再发布）；本接口保持向后兼容，
    一次请求内完成发布并返回正式名单。request_id 保证前端重试不重复占额。
    """
    campaign = audience_service.get_campaign(db, payload["campaign_id"])
    generation = generation_service.create_generation(
        db,
        campaign=campaign,
        budget=payload.get("budget"),
        operator_email=principal["email"],
        request_id=payload.get("request_id"),
    )
    generation_service.run_generation(db, generation)
    audience = generation_service.publish(
        db, generation, actor=principal["email"], note=payload.get("note")
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="audience.generate",
        entity="audience",
        entity_id=audience.id,
        detail={
            "audience_no": audience.audience_no,
            "campaign": campaign.code,
            "request_id": generation.request_id,
            "budget": audience.budget,
            "members": audience.member_count,
            "inc_orders": audience.inc_orders,
            "roi": audience.roi,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return audience_service.serialize_audience(audience, campaign)


def review(db: Session, audience_id: int, payload: dict, principal: dict, request: Request) -> dict:
    audience = audience_service.get_audience(db, audience_id)
    audience_service.confirm(
        db,
        audience,
        status=payload["status"],
        note=payload.get("note"),
        actor=principal["email"],
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="audience.confirm",
        entity="audience",
        entity_id=audience.id,
        detail={"status": audience.status, "audience_no": audience.audience_no},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    campaign = audience_service.get_campaign(db, audience.campaign_id)
    return audience_service.serialize_audience(audience, campaign)
