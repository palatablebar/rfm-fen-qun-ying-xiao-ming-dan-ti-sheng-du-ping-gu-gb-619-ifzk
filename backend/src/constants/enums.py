"""共享枚举（后端唯一定义处）。前端副本见 frontend/site/js/config.js。

README「共享枚举出现位置」一节列出全部出现点，改动需两侧同步。
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class MemberTier(StrEnum):
    """会员卡等级。"""

    NORMAL = "normal"
    SILVER = "silver"
    GOLD = "gold"
    BLACK = "black"


MEMBER_TIER_LABELS: dict[str, str] = {
    MemberTier.NORMAL.value: "普通会员",
    MemberTier.SILVER.value: "银卡",
    MemberTier.GOLD.value: "金卡",
    MemberTier.BLACK.value: "黑金卡",
}

MEMBER_TIER_ORDER: list[str] = [
    MemberTier.NORMAL.value,
    MemberTier.SILVER.value,
    MemberTier.GOLD.value,
    MemberTier.BLACK.value,
]


class SegmentCode(StrEnum):
    """RFM 八类规则分群（顺序即展示顺序与规则命中顺序）。"""

    CHAMPION = "champion"
    LOYAL = "loyal"
    POTENTIAL = "potential"
    AT_RISK = "at_risk"
    NEW = "new"
    CHURN_WARN = "churn_warn"
    ORDINARY = "ordinary"
    HIBERNATING = "hibernating"


SEGMENT_LABELS: dict[str, str] = {
    SegmentCode.CHAMPION.value: "重要价值",
    SegmentCode.LOYAL.value: "重要保持",
    SegmentCode.POTENTIAL.value: "重要发展",
    SegmentCode.AT_RISK.value: "重要挽留",
    SegmentCode.NEW.value: "新客养成",
    SegmentCode.CHURN_WARN.value: "流失预警",
    SegmentCode.ORDINARY.value: "一般价值",
    SegmentCode.HIBERNATING.value: "沉睡会员",
}

SEGMENT_RULES: dict[str, str] = {
    SegmentCode.CHAMPION.value: "R≥4 且 F≥4 且 M≥4",
    SegmentCode.LOYAL.value: "R≥3 且 F≥4 且 M≥3",
    SegmentCode.POTENTIAL.value: "R≥4 且 F≥3 且 M≥4",
    SegmentCode.AT_RISK.value: "R≤2 且 F≥4 且 M≥4",
    SegmentCode.NEW.value: "R≥4 且 F≤2（近期首购、单量少）",
    SegmentCode.CHURN_WARN.value: "R≤2 且 F≥2 且 M≥2",
    SegmentCode.ORDINARY.value: "R≥3 且 M≤3（常来但客单低）",
    SegmentCode.HIBERNATING.value: "其余（低 R / 低 F / 低 M）",
}

SEGMENT_ORDER: list[str] = [code.value for code in SegmentCode]


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


CAMPAIGN_STATUS_LABELS: dict[str, str] = {
    CampaignStatus.DRAFT.value: "草稿",
    CampaignStatus.ACTIVE.value: "进行中",
    CampaignStatus.CLOSED.value: "已结束",
}


class AudienceStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PUSHED = "pushed"


AUDIENCE_STATUS_LABELS: dict[str, str] = {
    AudienceStatus.PENDING.value: "待确认",
    AudienceStatus.CONFIRMED.value: "已确认",
    AudienceStatus.PUSHED.value: "已推送券平台",
}

AUDIENCE_STATUS_ORDER: list[str] = [
    AudienceStatus.PENDING.value,
    AudienceStatus.CONFIRMED.value,
    AudienceStatus.PUSHED.value,
]


class OrderChannel(StrEnum):
    MINIAPP = "miniapp"
    STORE = "store"
    DELIVERY = "delivery"

ORDER_CHANNEL_LABELS: dict[str, str] = {
    OrderChannel.MINIAPP.value: "小程序自提",
    OrderChannel.STORE.value: "门店收银",
    OrderChannel.DELIVERY.value: "外卖平台",
}


# ---- 城市配额（算法 C：每城独立试算，发布前才允许跨城调剂）----

# 八城固定顺序：所有排序/展示/导出都按这个顺序，保证确定性
CITY_ORDER: list[str] = [
    "上海", "杭州", "苏州", "南京", "宁波", "成都", "武汉", "重庆",
]

# 城市预算配额权重（总部季度预算按此切到城市，城市之间互不抢占）
CITY_BUDGET_WEIGHTS: dict[str, float] = {
    "上海": 0.24,
    "杭州": 0.15,
    "苏州": 0.12,
    "南京": 0.12,
    "宁波": 0.10,
    "成都": 0.11,
    "武汉": 0.09,
    "重庆": 0.07,
}

# 城市券响应系数：作用在会员历史响应率上，制造「上海高、成都武汉低」的地域差异
CITY_RESPONSE_FACTORS: dict[str, float] = {
    "上海": 1.22,
    "杭州": 1.10,
    "苏州": 1.05,
    "南京": 0.98,
    "宁波": 0.95,
    "成都": 0.78,
    "武汉": 0.74,
    "重庆": 0.85,
}

# 各城分群的券面额覆盖（不配置则沿用全局 segments.coupon_face）
CITY_COUPON_FACE: dict[str, dict[str, float]] = {
    "成都": {"new": 14.0, "at_risk": 12.0},
    "武汉": {"new": 13.0},
}


class GenerationStatus(StrEnum):
    """名单生成任务的生命周期（可中断续跑）。"""

    RUNNING = "running"      # 城市任务进行中（部分城市已完成并占额）
    READY = "ready"          # 全部城市试算完成，等待发布
    PUBLISHED = "published"  # 已一次性切换为正式名单
    FAILED = "failed"        # 中断且未跑完，可续跑


GENERATION_STATUS_LABELS: dict[str, str] = {
    GenerationStatus.RUNNING.value: "生成中",
    GenerationStatus.READY.value: "待发布",
    GenerationStatus.PUBLISHED.value: "已发布",
    GenerationStatus.FAILED.value: "中断待续跑",
}


class CityTaskStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"  # 该城没有可投分群/无会员


CITY_TASK_STATUS_LABELS: dict[str, str] = {
    CityTaskStatus.PENDING.value: "待试算",
    CityTaskStatus.DONE.value: "已完成",
    CityTaskStatus.SKIPPED.value: "无可投人群",
}


class ReallocationStatus(StrEnum):
    """发布前跨城调剂的明细状态。"""

    KEPT = "kept"          # 保留在本城配额内
    SURPLUS = "surplus"    # 本城人不够，预算闲置（调剂前）
    RECEIVED = "received"  # 接收其它城市的闲置余额
    IDLE = "idle"          # 调剂后仍未用完的余额


REALLOCATION_STATUS_LABELS: dict[str, str] = {
    ReallocationStatus.KEPT.value: "配额内",
    ReallocationStatus.SURPLUS.value: "本城闲置",
    ReallocationStatus.RECEIVED.value: "调入余额",
    ReallocationStatus.IDLE.value: "未用余额",
}


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    SEED = "seed"


TRIGGER_LABELS: dict[str, str] = {
    TriggerKind.MANUAL.value: "手动重算",
    TriggerKind.SCHEDULE.value: "每日定时",
    TriggerKind.SEED.value: "初始化",
}
