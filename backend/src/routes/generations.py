from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import AUDIENCE_GENERATE_ROLES
from src.controllers import generation_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.schemas.generation import GenerationPublishIn, GenerationStartIn
from src.utils.pagination import page_params

router = APIRouter(tags=["generations"])


@router.get("/campaigns/{campaign_id}/city-allocation")
def city_preview(
    campaign_id: int,
    budget: float | None = Query(default=None, gt=0, le=5_000_000),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    return generation_controller.city_preview(db, campaign_id, budget)


@router.get("/campaigns/{campaign_id}/city-quotas")
def city_quotas(
    campaign_id: int,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    return generation_controller.city_quotas(db, campaign_id)


@router.post("/generations", status_code=201)
def start_generation(
    payload: GenerationStartIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*AUDIENCE_GENERATE_ROLES)),
) -> dict:
    return generation_controller.start_generation(db, payload.model_dump(), principal, request)


@router.get("/generations")
def list_generations(
    campaign_id: int | None = Query(default=None, ge=1),
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return generation_controller.list_generations(
        db, campaign_id=campaign_id, limit=page.limit, offset=page.offset
    )


@router.get("/generations/{generation_id}")
def get_generation(
    generation_id: int,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    return generation_controller.get_generation(db, generation_id)


@router.post("/generations/{generation_id}/resume")
def resume_generation(
    generation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*AUDIENCE_GENERATE_ROLES)),
) -> dict:
    return generation_controller.resume_generation(db, generation_id, principal, request)


@router.post("/generations/{generation_id}/publish")
def publish_generation(
    generation_id: int,
    payload: GenerationPublishIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*AUDIENCE_GENERATE_ROLES)),
) -> dict:
    return generation_controller.publish_generation(
        db, generation_id, payload.model_dump(), principal, request
    )
