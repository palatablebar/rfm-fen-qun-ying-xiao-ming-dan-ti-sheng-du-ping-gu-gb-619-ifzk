"""城市预览与导出的端到端测试（内存库，不依赖外部服务）。"""
from __future__ import annotations

from sqlalchemy import select

from src.models.audience import Audience
from src.services import audience_service, city_service, export_service, generation_service


def _campaign(db):
    from src.models.campaign import Campaign

    return db.scalar(select(Campaign).where(Campaign.code == "test-campaign"))


def test_city_preview_reports_per_city_budget_people_increment_and_idle(db):
    campaign = _campaign(db)
    preview = audience_service.preview_city_allocation(db, campaign)
    cities = {row["city"]: row for row in preview["cities"]}
    assert set(cities) == set(city_service.list_cities())
    # 每城都能核对：配额、人数、增量、未用余额四个口径齐全
    for summary in preview["city_summaries"]:
        assert {"city", "quota_budget", "audience", "inc_orders", "unused_budget"} <= set(summary)
    # 各城配额之和等于活动预算；总花费不超过预算
    assert sum(row["quota_budget"] for row in preview["cities"]) == campaign.budget
    assert preview["totals"]["spend"] <= campaign.budget + 1e-6
    # 城市核对表与预览同源
    recon = {row["city"]: row for row in city_service.reconcile_quotas(db, campaign)}
    assert set(recon) == set(cities)
    assert sum(row["quota_budget"] for row in recon.values()) == campaign.budget


def test_published_audience_csv_expands_by_city_and_segment(db):
    campaign = _campaign(db)
    generation = generation_service.create_generation(
        db, campaign=campaign, budget=None, operator_email="tester@x.io"
    )
    generation_service.run_generation(db, generation)
    audience = generation_service.publish(db, generation, actor="tester@x.io")
    db.commit()

    content = export_service.audience_csv(db, audience.id).decode("utf-8-sig")
    lines = [line for line in content.splitlines() if line.strip()]
    # 表头 + 至少有会员明细行
    assert "城市" in lines[0] and "版本" in lines[0]
    assert len(lines) > 1
    # 明细行数与名单人数一致
    assert len(lines) - 1 == audience.member_count
    # 导出的城市只能来自有投放的城市
    exported_cities = {line.split(",")[4] for line in lines[1:]}
    allocated_cities = {
        item["city"] for item in audience.city_allocation if item.get("audience", 0) > 0
    }
    assert exported_cities == allocated_cities


def test_audience_csv_without_id_only_exports_current_versions(db):
    campaign = _campaign(db)
    gen1 = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                 operator_email="tester@x.io")
    generation_service.run_generation(db, gen1)
    generation_service.publish(db, gen1, actor="tester@x.io")
    gen2 = generation_service.create_generation(db, campaign=campaign, budget=None,
                                                 operator_email="tester@x.io")
    generation_service.run_generation(db, gen2)
    generation_service.publish(db, gen2, actor="tester@x.io")
    db.commit()
    content = export_service.audience_csv(db).decode("utf-8-sig")
    # 只应出现最新正式版的名单号
    latest = db.scalar(select(Audience).where(Audience.current_version.is_(True)))
    assert latest.audience_no in content
    old = db.scalar(select(Audience).where(Audience.current_version.is_(False)))
    assert old.audience_no not in content


def test_city_reconciliation_csv_covers_every_city(db):
    campaign = _campaign(db)
    content = export_service.city_reconciliation_csv(db, campaign.id).decode("utf-8-sig")
    lines = [line for line in content.splitlines() if line.strip()]
    assert len(lines) == 1 + len(city_service.list_cities())
    for city in city_service.list_cities():
        assert any(city in line for line in lines[1:])
