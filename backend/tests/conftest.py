"""pytest 公共夹具：内存 SQLite + 最小会员/分群/活动数据集。

测试不依赖外部 PostgreSQL；引擎建表后插入两个城市、两个分群的会员，
用于验证城市配额隔离、中断续跑与发布原子切换。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.constants.enums import SEGMENT_ORDER
from src.models import Base
from src.models.campaign import Campaign
from src.models.member import Member
from src.models.segment import Segment


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionTest = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    session = SessionTest()
    _seed(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(session: Session) -> None:
    anchor = datetime(2026, 11, 14)
    # 两城 × 两群：成都/武汉各有高、低响应会员，保证本城 lift 有高有低
    plan = [
        # city, segment, count, rate
        ("成都", "champion", 40, 0.060),
        ("成都", "ordinary", 60, 0.010),
        ("武汉", "champion", 200, 0.055),
        ("武汉", "ordinary", 300, 0.012),
        ("上海", "champion", 100, 0.070),
    ]
    member_id = 0
    for city, segment, count, rate in plan:
        for _ in range(count):
            member_id += 1
            session.add(
                Member(
                    member_no=f"T{member_id:05d}",
                    name=f"会员{member_id}",
                    phone=f"138{member_id:08d}"[:11],
                    city=city,
                    tier="normal",
                    joined_on=date(2024, 1, 1),
                    last_order_at=anchor - timedelta(days=10),
                    orders_window=5,
                    amount_window=150.0,
                    purchase_rate=rate,
                    avg_ticket=30.0,
                    segment_code=segment,
                    composite_score=3.5,
                    scored_at=anchor,
                )
            )
    for index, code in enumerate(SEGMENT_ORDER):
        session.add(
            Segment(
                code=code,
                name=code,
                rule_text="test",
                sort_order=index,
                member_count=1,
                purchase_rate=0.03,
                avg_ticket=30.0,
                lift=1.0,
                coupon_face=6.0,
                core_share=0.6,
                enabled=code in {"champion", "ordinary"},
                as_of=anchor,
                snapshot={"global_purchase_rate": 0.03},
            )
        )
    session.add(
        Campaign(
            code="test-campaign",
            name="测试活动",
            channel="小程序订阅消息",
            budget=2000.0,
            status="active",
            reach_cost=0.15,
            orders_per_converter=1.4,
            tail_factor=0.6,
        )
    )
    session.commit()
