"""城市预算配额：每个活动在每个城市保留一份独立预算，城市之间互不抢占。

配额在活动创建/名单试算前按 CITY_BUDGET_WEIGHTS 切好；名单生成只能花本城配额，
发布前的跨城调剂写在 audience_city_quotas.reallocation 字段里，不改本配额行。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class CityQuota(Base):
    __tablename__ = "city_quotas"
    __table_args__ = (
        UniqueConstraint("campaign_id", "city", name="uq_city_quota_campaign_city"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    city: Mapped[str] = mapped_column(String(24), index=True)
    budget_weight: Mapped[float] = mapped_column(Float, default=0.0)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    # 最近一次发布占用/释放的快照（发布成功才更新；失败保留旧值）
    committed_spend: Mapped[float] = mapped_column(Float, default=0.0)
    committed_members: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
