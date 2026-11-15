"""城市配额与可续跑名单生成的控制器：全部写操作落审计。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, audience_service, city_service, generation_service


def city_preview(db: Session, campaign_id: int, budget: float | None) -> dict:
    campaign = audience_service.get_campaign(db, campaign_id)
    result = audience_service.preview_city_allocation(db, campaign, budget)
    result["member_counts"] = city_service.city_member_counts(db)
    return result


def city_quotas(db: Session, campaign_id: int) -> dict:
    campaign = audience_service.get_campaign(db, campaign_id)
    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "budget": round(campaign.budget or 0.0, 2),
        "items": city_service.reconcile_quotas(db, campaign),
    }


def start_generation(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    """发起生成（幂等）：创建任务并同步跑完八城试算；中断后用同一 request_id 续跑。"""
    campaign = audience_service.get_campaign(db, payload["campaign_id"])
    request_id = payload.get("request_id")
    generation = generation_service.get_by_request_id(db, request_id) if request_id else None
    is_new = generation is None
    if generation is None:
        generation = generation_service.create_generation(
            db,
            campaign=campaign,
            budget=payload.get("budget"),
            operator_email=principal["email"],
            request_id=request_id,
        )
    try:
        generation_service.run_generation(db, generation)
    except Exception:
        db.commit()  # 保留中断现场（pending/done 城市状态），便于续跑
        raise
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="audience.generate" if is_new else "audience.resume",
        entity="generation",
        entity_id=generation.id,
        detail={
            "request_id": generation.request_id,
            "campaign": campaign.code,
            "status": generation.status,
            "attempts": generation.attempts,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return generation_service.serialize_generation(db, generation)


def resume_generation(db: Session, generation_id: int, principal: dict, request: Request) -> dict:
    generation = generation_service.get_generation(db, generation_id)
    generation_service.run_generation(db, generation)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="audience.resume",
        entity="generation",
        entity_id=generation.id,
        detail={"request_id": generation.request_id, "attempts": generation.attempts},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return generation_service.serialize_generation(db, generation)


def get_generation(db: Session, generation_id: int) -> dict:
    generation = generation_service.get_generation(db, generation_id)
    return generation_service.serialize_generation(db, generation)


def list_generations(db: Session, campaign_id: int | None, limit: int, offset: int) -> dict:
    from sqlalchemy import func, select

    from src.models.generation import AudienceGeneration

    stmt = select(AudienceGeneration)
    count_stmt = select(func.count()).select_from(AudienceGeneration)
    if campaign_id:
        stmt = stmt.where(AudienceGeneration.campaign_id == campaign_id)
        count_stmt = count_stmt.where(AudienceGeneration.campaign_id == campaign_id)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(AudienceGeneration.id.desc()).limit(limit).offset(offset)).all()
    return {
        "items": [generation_service.serialize_generation(db, row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def publish_generation(db: Session, generation_id: int, payload: dict, principal: dict,
                       request: Request) -> dict:
    """一次性切换正式名单；单事务失败回滚，旧版名单保持 current。"""
    generation = generation_service.get_generation(db, generation_id)
    campaign = audience_service.get_campaign(db, generation.campaign_id)
    audience = generation_service.publish(
        db, generation, actor=principal["email"], note=payload.get("note")
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="audience.publish",
        entity="audience",
        entity_id=audience.id,
        detail={
            "generation": generation.request_id,
            "campaign": campaign.code,
            "version": audience.version,
            "supersedes_id": audience.supersedes_id,
            "members": audience.member_count,
            "spend": audience.spend,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return audience_service.serialize_audience(audience, campaign)
