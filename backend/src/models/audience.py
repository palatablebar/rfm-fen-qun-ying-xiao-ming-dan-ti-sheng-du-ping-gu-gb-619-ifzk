"""营销名单：算法 B 的预算分配结果，可确认、可导出。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class Audience(Base):
    __tablename__ = "audiences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    audience_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    inc_orders: Mapped[int] = mapped_column(Integer, default=0)
    inc_gmv: Mapped[float] = mapped_column(Float, default=0.0)
    roi: Mapped[float] = mapped_column(Float, default=0.0)
    global_rate: Mapped[float] = mapped_column(Float, default=0.0)
    allocation: Mapped[list] = mapped_column(JSON, default=list)
    # 城市维度试算/调剂结果（算法 C）：每城预算、人数、增量、未用余额
    city_allocation: Mapped[list] = mapped_column(JSON, default=list)
    # 同一活动下的正式版本号：只有 current_version=True 的名单才是对外正式版
    version: Mapped[int] = mapped_column(Integer, default=1)
    current_version: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 发布时被本版本替换掉的上一版名单 id（失败保留旧版时该字段不写）
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_id: Mapped[int | None] = mapped_column(
        ForeignKey("audience_generations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_email: Mapped[str] = mapped_column(String(160), default="system")
    confirmed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    seed_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
