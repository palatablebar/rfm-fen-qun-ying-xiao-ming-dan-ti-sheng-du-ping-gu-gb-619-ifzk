"""名单生成任务与城市子任务：可中断、可续跑、重试不重复占额。

设计要点：
- ``AudienceGeneration`` 是一次「生成名单」请求的幂等载体（request_id 唯一）。
  同一 request_id 的重试直接返回同一行，不会再占一份城市配额。
- 每个城市一行 ``CityGenerationTask``，带状态与试算结果 JSON；
  续跑时只处理 pending 城市，done 城市原样复用，天然不重复占额。
- 生成完成后状态置 ready；发布是独立动作，一次性把正式名单切换过去，
  失败回滚，旧版名单保持 current 不变。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class AudienceGeneration(Base):
    __tablename__ = "audience_generations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 幂等键：同一次生成请求（含前端重试、worker 续跑）共用一个 request_id
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    # 八城独立试算 + 发布前调剂后的完整结果（城市维度 + 分群维度）
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    # ready 之后发布产生的正式名单 id；未发布为 None
    audience_id: Mapped[int | None] = mapped_column(
        ForeignKey("audiences.id", ondelete="SET NULL"), nullable=True, index=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    operator_email: Mapped[str] = mapped_column(String(160), default="system")
    seed_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CityGenerationTask(Base):
    __tablename__ = "city_generation_tasks"
    __table_args__ = (
        UniqueConstraint("generation_id", "city", name="uq_city_task_generation_city"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("audience_generations.id", ondelete="CASCADE"), index=True
    )
    city: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 该城独立试算结果（lift_service 城市版输出），done 后固化，续跑不再重算
    allocation: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
