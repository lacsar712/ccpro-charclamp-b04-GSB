"""炭窑焖烧志业务规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from charclamp.domain.models import BurnShift, Clamp

if TYPE_CHECKING:
    from charclamp.domain.models import User

MIN_PEAK_TEMP_FOR_DRAWN = 400.0


class RuleError(ValueError):
    """业务规则校验失败。"""


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


# ---- 班次改写权限矩阵 ----

ROLE_ADMIN = "admin"

#: 班次可被改写的字段全集
SHIFT_EDITABLE_FIELDS: tuple[str, ...] = ("notes", "peak_temp_c", "started_at", "charcoal_grade")
#: 操作工可改写的字段（仅备注）
SHIFT_WORKER_FIELDS: tuple[str, ...] = ("notes",)

SHIFT_FIELD_LABELS = {
    "notes": "备注",
    "peak_temp_c": "峰值温度",
    "started_at": "开始时刻",
    "charcoal_grade": "炭品等级",
}


@dataclass(frozen=True)
class ShiftEditPerm:
    """单个用户对某条班次的改写权限结论（抽屉渲染与后台保存共用）。"""

    allowed: bool
    reason: str = ""
    fields: frozenset[str] = frozenset()

    def can_edit(self, field: str) -> bool:
        return field in self.fields


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_shift_today(shift: BurnShift, now: datetime | None = None) -> bool:
    """班次是否属于「当天」（按 UTC 日历日判定，与模型 utcnow 一致）。"""
    now = now or datetime.now(timezone.utc)
    return _as_utc(shift.started_at).date() == _as_utc(now).date()


def shift_edit_perm(
    user: User,
    shift: BurnShift,
    clamp: Clamp,
    now: datetime | None = None,
) -> ShiftEditPerm:
    """
    班次改写权限矩阵：
    - 已出炭窑上的班次：全员只读（含管理员）；
    - 管理员：可改备注、峰值温度、开始时刻、炭品等级；
    - 操作工：仅可改「本人当天」班次的备注。
    """
    if clamp.status == Clamp.STATUS_DRAWN:
        return ShiftEditPerm(False, "该窑已出炭，窑上班次全员只读", frozenset())
    if user.role == ROLE_ADMIN:
        return ShiftEditPerm(True, "", frozenset(SHIFT_EDITABLE_FIELDS))
    if shift.created_by_id != user.id:
        return ShiftEditPerm(False, "只能改写本人登记的班次", frozenset())
    if not is_shift_today(shift, now):
        return ShiftEditPerm(False, "只能改写当天班次", frozenset())
    return ShiftEditPerm(True, "", frozenset(SHIFT_WORKER_FIELDS))


def forbidden_shift_changes(perm: ShiftEditPerm, changed_fields: list[str]) -> list[str]:
    """从实际发生变化的字段中，挑出当前权限不允许改写的字段。"""
    return [field for field in changed_fields if not perm.can_edit(field)]
