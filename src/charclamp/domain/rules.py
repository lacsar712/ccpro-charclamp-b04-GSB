"""炭窑焖烧志业务规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from charclamp.domain.models import BurnShift, Clamp, User, is_admin

MIN_PEAK_TEMP_FOR_DRAWN = 400.0

# 业务时区：当天班次按窑场当地日历日判定。
APP_TZ = ZoneInfo("Asia/Shanghai")

FIELD_NOTES = "notes"
FIELD_STARTED_AT = "started_at"
FIELD_PEAK_TEMP_C = "peak_temp_c"
FIELD_CHARCOAL_GRADE = "charcoal_grade"

ALL_SHIFT_FIELDS = frozenset(
    {FIELD_NOTES, FIELD_STARTED_AT, FIELD_PEAK_TEMP_C, FIELD_CHARCOAL_GRADE}
)
# 操作工只能改备注；开始时刻、峰值、等级一律仅管理员。
ADMIN_ONLY_FIELDS = frozenset(
    {FIELD_STARTED_AT, FIELD_PEAK_TEMP_C, FIELD_CHARCOAL_GRADE}
)


class RuleError(ValueError):
    """业务规则校验失败。"""


class ShiftEditDenied(RuleError):
    """班次改写越权（抽屉与保存接口共用）。"""


def latest_shift_for_clamp(clamp: Clamp) -> BurnShift | None:
    if not clamp.shifts:
        return None
    return max(clamp.shifts, key=lambda s: s.started_at)


def can_mark_clamp_drawn(clamp: Clamp) -> tuple[bool, str]:
    """
    炭窑转为「已出炭」(drawn) 的前提：
    最近一条焖烧班次的峰值温度已记录，且 >= 400℃。
    """
    latest = latest_shift_for_clamp(clamp)
    if latest is None:
        return False, "该窑尚无焖烧班次，不能标记为已出炭"
    if latest.peak_temp_c is None:
        return False, "最近班次尚未记录峰值温度，不能标记为已出炭"
    if latest.peak_temp_c < MIN_PEAK_TEMP_FOR_DRAWN:
        return (
            False,
            f"最近班次峰值温度 {latest.peak_temp_c}℃ 低于 {MIN_PEAK_TEMP_FOR_DRAWN:.0f}℃，不能标记为已出炭",
        )
    return True, ""


def assert_can_set_clamp_status(clamp: Clamp, new_status: str) -> None:
    allowed = {Clamp.STATUS_STACKED, Clamp.STATUS_BURNING, Clamp.STATUS_DRAWN}
    if new_status not in allowed:
        raise RuleError(f"无效状态：{new_status}")
    if new_status == Clamp.STATUS_DRAWN:
        ok, msg = can_mark_clamp_drawn(clamp)
        if not ok:
            raise RuleError(msg)


@dataclass(frozen=True)
class ShiftEditPolicy:
    """某用户对某班次的改写结论——抽屉渲染与 POST 保存唯一共同依据。"""

    can_edit: bool
    reason: str
    editable_fields: frozenset[str]
    user_is_admin: bool

    def can_touch(self, field_name: str) -> bool:
        return field_name in self.editable_fields


def _as_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # 历史数据以 UTC 写入。
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(APP_TZ)


def is_shift_today(shift: BurnShift, now: datetime | None = None) -> bool:
    now = now or datetime.now(APP_TZ)
    return _as_local(shift.started_at).date() == _as_local(now).date()


def shift_edit_policy(
    shift: BurnShift,
    clamp: Clamp,
    user: User,
    now: datetime | None = None,
) -> ShiftEditPolicy:
    """
    班次改写权限边界（唯一判定入口）：
    1. 已出炭窑上的班次——全员只读，管理员也不例外；
    2. 管理员可改开始时刻、峰值、等级、备注；
    3. 操作工仅可改“本人 + 当天”班次的备注。
    """
    if clamp.status == Clamp.STATUS_DRAWN:
        return ShiftEditPolicy(False, "该窑已出炭，班次全员只读", frozenset(), is_admin(user))

    if is_admin(user):
        return ShiftEditPolicy(True, "", ALL_SHIFT_FIELDS, True)

    if shift.created_by_id != user.id:
        return ShiftEditPolicy(False, "操作工只能改写本人登记的班次", frozenset(), False)

    if not is_shift_today(shift, now):
        return ShiftEditPolicy(False, "操作工只能改写当天班次的备注", frozenset(), False)

    return ShiftEditPolicy(True, "", frozenset({FIELD_NOTES}), False)


def assert_submitted_fields_allowed(
    policy: ShiftEditPolicy, submitted_fields: set[str]
) -> None:
    """拒绝越权字段：哪怕输入框被藏起，直接 POST 也不能成功。"""
    if not policy.can_edit:
        raise ShiftEditDenied(policy.reason or "该班次不可改写")
    touched = submitted_fields & ALL_SHIFT_FIELDS
    forbidden = touched - policy.editable_fields
    if forbidden:
        labels = {
            FIELD_STARTED_AT: "开始时刻",
            FIELD_PEAK_TEMP_C: "峰值温度",
            FIELD_CHARCOAL_GRADE: "炭品等级",
            FIELD_NOTES: "备注",
        }
        names = "、".join(labels.get(f, f) for f in sorted(forbidden))
        raise ShiftEditDenied(f"无权修改：{names}（仅管理员）")
