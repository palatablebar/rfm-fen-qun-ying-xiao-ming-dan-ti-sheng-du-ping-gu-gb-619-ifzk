"""名单生成任务编排：城市独立试算、中断续跑、发布原子切换。

核心保证
--------
1. **幂等不重复占额**：``request_id`` 唯一。同一次请求的重试/续跑拿到同一个
   ``AudienceGeneration``；城市子任务 ``done`` 后结果固化，续跑只处理 ``pending``，
   不会把同一城市的配额算两遍。
2. **城市配额隔离**：试算阶段每个城市只花自己的 ``CityQuota.budget``，
   上海的高响应群不会抢走成都、武汉的钱；人不够的城市只产生 idle_budget。
3. **发布前调剂**：八城全部跑完后，统一执行一次跨城闲置余额调剂（算法 C），
   调剂结果与资金来源逐行留痕。
4. **一次性切换 + 失败保留旧版**：发布在单个事务里把新版本置 current、
   旧版本降为历史版本并回写城市配额占用；任何一步抛错整体回滚，正式名单不变。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import (
    CITY_ORDER,
    CITY_TASK_STATUS_LABELS,
    GENERATION_STATUS_LABELS,
    GenerationStatus,
    CityTaskStatus,
)
from src.models.audience import Audience
from src.models.city_quota import CityQuota
from src.models.generation import AudienceGeneration, CityGenerationTask
from src.services import city_lift_service, city_service
from src.utils.errors import AppError, not_found
from src.utils.timeutil import iso_utc, local_now


# ---------------------------------------------------------------------------
# 任务创建与查询
# ---------------------------------------------------------------------------

def _new_request_id() -> str:
    return f"REQ-{local_now():%Y%m%d}-{uuid.uuid4().hex[:12].upper()}"


def get_generation(db: Session, generation_id: int) -> AudienceGeneration:
    row = db.get(AudienceGeneration, generation_id)
    if row is None:
        raise not_found("generation", generation_id)
    return row


def get_by_request_id(db: Session, request_id: str) -> AudienceGeneration | None:
    return db.scalar(
        select(AudienceGeneration).where(AudienceGeneration.request_id == request_id)
    )


def create_generation(
    db: Session,
    *,
    campaign,
    budget: float | None,
    operator_email: str,
    request_id: str | None = None,
    seed_key: str | None = None,
) -> AudienceGeneration:
    """创建生成任务并初始化八城子任务（幂等：request_id / seed_key 命中即复用）。"""
    if seed_key:
        existing = db.scalar(
            select(AudienceGeneration).where(AudienceGeneration.seed_key == seed_key)
        )
        if existing is not None:
            return existing
    if request_id:
        existing = get_by_request_id(db, request_id)
        if existing is not None:
            return existing

    city_service.ensure_city_quotas(db, campaign)
    generation = AudienceGeneration(
        request_id=request_id or _new_request_id(),
        campaign_id=campaign.id,
        budget=round(float(budget if budget is not None else campaign.budget), 2),
        status=GenerationStatus.RUNNING.value,
        result={},
        attempts=0,
        operator_email=operator_email,
        seed_key=seed_key,
    )
    db.add(generation)
    db.flush()
    for city in CITY_ORDER:
        db.add(CityGenerationTask(generation_id=generation.id, city=city,
                                  status=CityTaskStatus.PENDING.value, allocation={}))
    db.flush()
    return generation


# ---------------------------------------------------------------------------
# 可续跑的城市试算
# ---------------------------------------------------------------------------

def _run_city(db: Session, generation: AudienceGeneration, campaign, task: CityGenerationTask,
              matrix: dict[str, list[dict]], quotas: dict[str, CityQuota]) -> None:
    quota = quotas[task.city]
    segments = matrix.get(task.city) or []
    if not segments:
        task.status = CityTaskStatus.SKIPPED.value
        task.allocation = {"city": task.city, "skipped": True, "reason": "该城暂无已评分会员"}
        db.flush()
        return
    result = city_lift_service.allocate_city(
        task.city,
        segments,
        budget=quota.budget,
        reach_cost=campaign.reach_cost,
        orders_per_converter=campaign.orders_per_converter,
        tail_factor=campaign.tail_factor,
    )
    task.status = CityTaskStatus.DONE.value
    task.allocation = result
    task.error = None
    db.flush()


def run_generation(
    db: Session,
    generation: AudienceGeneration,
) -> AudienceGeneration:
    """执行（或续跑）生成任务。

    每个城市试算完即 ``flush`` 固化子任务状态；控制器在异常时统一 ``commit``
    保留中断现场。中断后再次调用只跑剩余 pending 城市，已 done/skipped 的城市
    直接复用结果，不重新占额。
    """
    from src.models.campaign import Campaign

    campaign = db.get(Campaign, generation.campaign_id)
    if campaign is None:
        raise not_found("campaign", generation.campaign_id)

    generation.attempts = int(generation.attempts or 0) + 1
    if generation.status == GenerationStatus.PUBLISHED.value:
        return generation
    generation.status = GenerationStatus.RUNNING.value
    generation.failure_reason = None
    db.flush()

    quotas = {row.city: row for row in city_service.ensure_city_quotas(db, campaign)}
    matrix = city_service.city_segment_matrix(db)

    tasks = {
        row.city: row
        for row in db.scalars(
            select(CityGenerationTask).where(
                CityGenerationTask.generation_id == generation.id
            )
        ).all()
    }
    try:
        for city in CITY_ORDER:
            task = tasks[city]
            if task.status in {CityTaskStatus.DONE.value, CityTaskStatus.SKIPPED.value}:
                # 续跑：已完成城市原样复用，绝不重新占额
                continue
            _run_city(db, generation, campaign, task, matrix, quotas)
    except Exception as exc:  # noqa: BLE001 - 中断可续跑：落失败态后抛出，由调用方决定事务
        generation.status = GenerationStatus.FAILED.value
        generation.failure_reason = str(exc)[:1000]
        db.flush()
        raise

    _finalize_ready(db, generation, campaign, tasks)
    return generation


def _finalize_ready(db: Session, generation: AudienceGeneration, campaign,
                    tasks: dict[str, CityGenerationTask]) -> None:
    """八城试算全部结束 → 执行发布前调剂，固化结果，置 ready。"""
    city_results = []
    for city in CITY_ORDER:
        task = tasks[city]
        if task.status == CityTaskStatus.SKIPPED.value:
            # 无会员城市：给一份零结果，保证城市核对行完整
            city_results.append(_empty_city_result(city, generation, db))
            continue
        city_results.append(task.allocation)

    reallocation = city_lift_service.reallocate_idle_budget(
        city_results,
        tail_factor=campaign.tail_factor,
        orders_per_converter=campaign.orders_per_converter,
    )
    generation.result = {
        "campaign_id": campaign.id,
        "budget": generation.budget,
        "cities": reallocation["cities"],
        "city_summaries": reallocation["city_summaries"],
        "transfers": reallocation["transfers"],
        "totals": reallocation["totals"],
        "log": reallocation["log"],
    }
    generation.status = GenerationStatus.READY.value
    generation.failure_reason = None
    db.flush()


def _empty_city_result(city: str, generation: AudienceGeneration, db: Session) -> dict:
    quota = db.scalar(
        select(CityQuota).where(
            CityQuota.campaign_id == generation.campaign_id, CityQuota.city == city
        )
    )
    budget = float(quota.budget) if quota else 0.0
    return {
        "city": city,
        "quota_budget": round(budget, 2),
        "global_rate": 0.0,
        "segments": [],
        "totals": {
            "audience": 0, "spend": 0.0, "idle_budget": round(budget, 2),
            "inc_orders": 0, "inc_gmv": 0.0, "roi": 0.0, "headroom": 0,
            "segments_allocated": 0, "segments_deferred": 0, "segments_ineligible": 0,
        },
        "remaining": round(budget, 2),
    }


# ---------------------------------------------------------------------------
# 发布：一次性切换正式名单，失败保留旧版
# ---------------------------------------------------------------------------

def _next_audience_no(db: Session) -> str:
    from sqlalchemy import func

    prefix = f"A{local_now():%Y%m%d}"
    existing = db.scalar(
        select(func.count()).select_from(Audience).where(Audience.audience_no.like(f"{prefix}%"))
    ) or 0
    return f"{prefix}{existing + 1:02d}"


def publish(db: Session, generation: AudienceGeneration, *, actor: str,
            note: str | None = None) -> Audience:
    """把 ready 的试算结果一次性发布为正式名单（单事务，失败整体回滚）。"""
    if generation.status != GenerationStatus.READY.value:
        raise AppError(
            "VALIDATION", 409,
            {"reason": f"任务状态为 {GENERATION_STATUS_LABELS.get(generation.status, generation.status)}，"
                       "只有待发布任务可以发布；中断任务请先续跑"},
        )
    result = generation.result or {}
    totals = result.get("totals") or {}
    if not totals.get("audience"):
        raise AppError("VALIDATION", 409, {"reason": "各城配额下没有可投人群，无法发布空名单"})

    campaign_id = generation.campaign_id
    previous = db.scalar(
        select(Audience)
        .where(Audience.campaign_id == campaign_id, Audience.current_version.is_(True))
        .order_by(Audience.version.desc())
    )
    next_version = int(previous.version + 1) if previous else 1

    # 全局分群视角的汇总（保持与旧 allocation 字段同构，兼容历史名单/导出）
    flat_allocation = _flatten_segment_allocation(result.get("cities") or [])
    global_rate = _weighted_global_rate(result.get("cities") or [])

    audience = Audience(
        audience_no=_next_audience_no(db),
        campaign_id=campaign_id,
        status="pending",
        budget=round(float(generation.budget), 2),
        member_count=int(totals.get("audience") or 0),
        spend=round(float(totals.get("spend") or 0.0), 2),
        inc_orders=int(totals.get("inc_orders") or 0),
        inc_gmv=round(float(totals.get("inc_gmv") or 0.0), 2),
        roi=round(float(totals.get("roi") or 0.0), 2),
        global_rate=round(global_rate, 6),
        allocation=flat_allocation,
        city_allocation=result.get("city_summaries") or [],
        version=next_version,
        current_version=True,
        supersedes_id=previous.id if previous else None,
        generation_id=generation.id,
        note=note or result.get("log"),
        operator_email=actor,
    )
    db.add(audience)
    db.flush()

    # 旧版本一次性降为历史版（同一事务；本语句之后若报错会随事务一起回滚）
    if previous is not None:
        previous.current_version = False

    # 城市配额占用回写：发布成功才提交，失败保留旧 committed_*
    quotas = {
        row.city: row
        for row in db.scalars(select(CityQuota).where(CityQuota.campaign_id == campaign_id)).all()
    }
    for summary in result.get("city_summaries") or []:
        quota = quotas.get(summary["city"])
        if quota is None:
            raise AppError("INTERNAL", 500, {"reason": f"缺少 {summary['city']} 的城市配额行"})
        quota.committed_spend = round(float(summary.get("spend") or 0.0), 2)
        quota.committed_members = int(summary.get("audience") or 0)

    generation.status = GenerationStatus.PUBLISHED.value
    generation.audience_id = audience.id
    db.flush()
    return audience


def _flatten_segment_allocation(cities: list[dict]) -> list[dict]:
    """把城市 × 分群结果汇总成全局分群行（字段与算法 B 输出对齐）。"""
    merged: dict[str, dict] = {}
    for city_result in cities:
        for row in city_result.get("segments") or []:
            code = row["segment_code"]
            bucket = merged.setdefault(
                code,
                {
                    "segment_code": code,
                    "segment_name": row["segment_name"],
                    "status": "ineligible",
                    "priority": None,
                    "allocated": 0,
                    "budget": 0.0,
                    "inc_orders": 0,
                    "inc_gmv": 0.0,
                    "core_allocated": 0,
                    "tail_allocated": 0,
                    "coupon_face": row.get("coupon_face"),
                    "lift": row.get("lift"),
                    "unit_cost": row.get("unit_cost"),
                    "efficiency": row.get("efficiency"),
                    "purchase_rate": row.get("purchase_rate"),
                    "cities": [],
                },
            )
            if row.get("status") == "allocated":
                bucket["status"] = "allocated"
                bucket["allocated"] += int(row.get("allocated") or 0)
                bucket["budget"] = round(bucket["budget"] + float(row.get("budget") or 0.0), 2)
                bucket["inc_orders"] += int(row.get("inc_orders") or 0)
                bucket["inc_gmv"] = round(bucket["inc_gmv"] + float(row.get("inc_gmv") or 0.0), 2)
                bucket["core_allocated"] += int(row.get("core_allocated") or 0)
                bucket["tail_allocated"] += int(row.get("tail_allocated") or 0)
            bucket["cities"].append(
                {
                    "city": city_result["city"],
                    "status": row.get("status"),
                    "allocated": int(row.get("allocated") or 0),
                    "budget": round(float(row.get("budget") or 0.0), 2),
                    "lift": row.get("lift"),
                }
            )
    ordered = sorted(merged.values(), key=lambda item: item["segment_code"])
    for row in ordered:
        row["roi"] = round(row["inc_gmv"] / row["budget"], 2) if row["budget"] > 0 else 0.0
    return ordered


def _weighted_global_rate(cities: list[dict]) -> float:
    total_members = 0
    weighted = 0.0
    for city_result in cities:
        count = sum(int(row.get("member_count") or 0) for row in city_result.get("segments") or [])
        total_members += count
        weighted += float(city_result.get("global_rate") or 0.0) * count
    return weighted / total_members if total_members else 0.0


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def serialize_generation(db: Session, generation: AudienceGeneration) -> dict:
    tasks = db.scalars(
        select(CityGenerationTask)
        .where(CityGenerationTask.generation_id == generation.id)
        .order_by(CityGenerationTask.id)
    ).all()
    city_progress = []
    for task in tasks:
        alloc = task.allocation or {}
        city_progress.append(
            {
                "city": task.city,
                "status": task.status,
                "status_label": CITY_TASK_STATUS_LABELS.get(task.status, task.status),
                "audience": (alloc.get("totals") or {}).get("audience", 0),
                "spend": (alloc.get("totals") or {}).get("spend", 0.0),
                "idle_budget": (alloc.get("totals") or {}).get("idle_budget", 0.0),
                "error": task.error,
            }
        )
    return {
        "id": generation.id,
        "request_id": generation.request_id,
        "campaign_id": generation.campaign_id,
        "budget": round(generation.budget or 0.0, 2),
        "status": generation.status,
        "status_label": GENERATION_STATUS_LABELS.get(generation.status, generation.status),
        "attempts": generation.attempts,
        "audience_id": generation.audience_id,
        "failure_reason": generation.failure_reason,
        "operator_email": generation.operator_email,
        "city_progress": city_progress,
        "result": generation.result or None,
        "created_at": iso_utc(generation.created_at),
        "updated_at": iso_utc(generation.updated_at),
    }
