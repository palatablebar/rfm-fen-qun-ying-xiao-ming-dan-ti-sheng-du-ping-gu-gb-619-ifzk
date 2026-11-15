"""城市名单生成编排测试：配额隔离、幂等、中断续跑、发布原子切换、失败保留旧版。"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.constants.enums import GenerationStatus
from src.models.audience import Audience
from src.models.city_quota import CityQuota
from src.models.generation import AudienceGeneration, CityGenerationTask
from src.services import city_service, generation_service
from src.utils.errors import AppError


def _campaign(db):
    from src.models.campaign import Campaign

    return db.scalar(select(Campaign).where(Campaign.code == "test-campaign"))


def test_city_quotas_split_budget_and_sum_to_total(db):
    campaign = _campaign(db)
    quotas = city_service.ensure_city_quotas(db, campaign)
    assert len(quotas) == 8
    assert sum(q.budget for q in quotas) == pytest.approx(campaign.budget)
    # 幂等：再次调用不新增、不覆盖
    again = city_service.ensure_city_quotas(db, campaign)
    assert len(again) == 8
    assert db.scalar(select(func.count()).select_from(CityQuota)) == 8


def test_full_generation_then_publish_switches_current_version(db):
    campaign = _campaign(db)
    generation = generation_service.create_generation(
        db, campaign=campaign, budget=None, operator_email="tester@x.io"
    )
    generation_service.run_generation(db, generation)
    assert generation.status == GenerationStatus.READY.value
    # 八个城市子任务全部结束（done/skipped），结果已固化
    tasks = db.scalars(select(CityGenerationTask).where(
        CityGenerationTask.generation_id == generation.id)).all()
    assert {t.status for t in tasks} <= {"done", "skipped"}
    assert all((t.allocation or {}) for t in tasks if t.status == "done")

    audience = generation_service.publish(db, generation, actor="tester@x.io")
    db.commit()
    assert audience.current_version is True
    assert audience.version == 1
    assert generation.status == GenerationStatus.PUBLISHED.value
    assert generation.audience_id == audience.id
    # 城市配额占用在发布后才回写，且总和与名单花费一致
    quotas = db.scalars(select(CityQuota).where(CityQuota.campaign_id == campaign.id)).all()
    assert sum(q.committed_spend for q in quotas) == pytest.approx(audience.spend)
    assert sum(q.committed_members for q in quotas) == audience.member_count
    # 名单带城市核对行，覆盖全部八城
    assert {row["city"] for row in audience.city_allocation} == {q.city for q in quotas}


def test_request_id_is_idempotent_and_retry_does_not_double_allocate(db, monkeypatch):
    campaign = _campaign(db)
    request_id = "REQ-RETRY-001"
    first = generation_service.create_generation(
        db, campaign=campaign, budget=None, operator_email="tester@x.io", request_id=request_id
    )
    # 同 request_id 再次创建 → 直接返回同一任务，不新增
    second = generation_service.create_generation(
        db, campaign=campaign, budget=None, operator_email="tester@x.io", request_id=request_id
    )
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(AudienceGeneration)) == 1

    generation_service.run_generation(db, first)
    snapshot_spend = first.result["totals"]["spend"]
    # 已 ready 的任务再跑一次：城市全部复用，结果不变，不重复占额
    generation_service.run_generation(db, first)
    assert first.attempts == 2
    assert first.result["totals"]["spend"] == pytest.approx(snapshot_spend)


def test_interrupted_generation_can_resume_without_redoing_done_cities(db, monkeypatch):
    campaign = _campaign(db)
    generation = generation_service.create_generation(
        db, campaign=campaign, budget=None, operator_email="tester@x.io"
    )

    real_allocate = generation_service.city_lift_service.allocate_city
    calls: list[str] = []

    def flaky(city, segments, **kwargs):
        calls.append(city)
        if city == "武汉":
            raise RuntimeError("simulated worker crash")
        return real_allocate(city, segments, **kwargs)

    monkeypatch.setattr(generation_service.city_lift_service, "allocate_city", flaky)
    with pytest.raises(RuntimeError):
        generation_service.run_generation(db, generation)
    assert generation.status == GenerationStatus.FAILED.value
    done_before = db.scalars(select(CityGenerationTask).where(
        CityGenerationTask.generation_id == generation.id,
        CityGenerationTask.status == "done")).all()
    assert done_before, "中断前应已有城市完成并固化"
    wuhan = db.scalar(select(CityGenerationTask).where(
        CityGenerationTask.generation_id == generation.id,
        CityGenerationTask.city == "武汉"))
    assert wuhan.status == "pending"

    # 续跑：恢复真实算法，只跑剩余 pending 城市
    monkeypatch.undo()
    generation_service.run_generation(db, generation)
    assert generation.status == GenerationStatus.READY.value
    statuses = {t.city: t.status for t in db.scalars(select(CityGenerationTask).where(
        CityGenerationTask.generation_id == generation.id)).all()}
    assert set(statuses.values()) <= {"done", "skipped"}
    audience = generation_service.publish(db, generation, actor="tester@x.io")
    db.commit()
    assert audience.current_version is True


def test_publish_failure_keeps_previous_version_and_old_quota(db, monkeypatch):
    campaign = _campaign(db)
    # 第一版正常发布并提交
    gen1 = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                 operator_email="tester@x.io")
    generation_service.run_generation(db, gen1)
    v1 = generation_service.publish(db, gen1, actor="tester@x.io")
    db.commit()
    v1_id = v1.id
    v1_spend = v1.spend

    # 第二版试算完成并提交（真实流程中生成与发布是两个独立请求/事务）
    gen2 = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                 operator_email="tester@x.io")
    generation_service.run_generation(db, gen2)
    assert gen2.status == GenerationStatus.READY.value
    db.commit()
    gen2_id = gen2.id

    def boom(*args, **kwargs):
        raise RuntimeError("simulated publish failure")

    monkeypatch.setattr(generation_service, "_next_audience_no", boom)
    with pytest.raises(RuntimeError):
        generation_service.publish(db, db.get(AudienceGeneration, gen2_id), actor="tester@x.io")
    db.rollback()

    # 旧版仍是唯一正式名单，第二版发布没有留下任何 audience
    current = db.scalars(select(Audience).where(Audience.current_version.is_(True))).all()
    assert len(current) == 1
    assert current[0].id == v1_id
    quotas = db.scalars(select(CityQuota).where(CityQuota.campaign_id == campaign.id)).all()
    assert sum(q.committed_spend for q in quotas) == pytest.approx(v1_spend)
    # 生成任务仍停留在 ready，可再次尝试发布
    gen2_row = db.get(AudienceGeneration, gen2_id)
    assert gen2_row.status == GenerationStatus.READY.value
    assert gen2_row.audience_id is None

    # 恢复后重新发布成功，正式名单一次性切换为第二版
    monkeypatch.undo()
    v2 = generation_service.publish(db, gen2_row, actor="tester@x.io")
    db.commit()
    assert v2.version == 2
    assert v2.current_version is True
    assert v2.supersedes_id == v1_id
    assert db.get(Audience, v1_id).current_version is False


def test_publish_second_version_supersedes_first(db):
    campaign = _campaign(db)
    gen1 = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                 operator_email="tester@x.io")
    generation_service.run_generation(db, gen1)
    v1 = generation_service.publish(db, gen1, actor="tester@x.io")
    db.commit()

    gen2 = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                 operator_email="tester@x.io")
    generation_service.run_generation(db, gen2)
    v2 = generation_service.publish(db, gen2, actor="tester@x.io")
    db.commit()
    assert v2.version == 2
    assert v2.current_version is True
    assert v2.supersedes_id == v1.id
    db.refresh(v1)
    assert v1.current_version is False
    current = db.scalars(select(Audience).where(Audience.current_version.is_(True))).all()
    assert [a.id for a in current] == [v2.id]


def test_cannot_publish_unready_generation(db):
    campaign = _campaign(db)
    generation = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                       operator_email="tester@x.io")
    with pytest.raises(AppError):
        generation_service.publish(db, generation, actor="tester@x.io")
