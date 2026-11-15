from __future__ import annotations

from pydantic import BaseModel, Field


class GenerationStartIn(BaseModel):
    """发起城市名单生成：活动 + 预算 + 客户端幂等键。"""

    campaign_id: int = Field(gt=0)
    budget: float | None = Field(default=None, gt=0, le=5_000_000)
    request_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


class GenerationPublishIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
