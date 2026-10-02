from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from litestar import Controller, MediaType, Request, Response, get, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Template
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from charclamp.domain.models import BurnShift, Clamp, User
from charclamp.domain.rules import (
    SHIFT_FIELD_LABELS,
    RuleError,
    ShiftEditPerm,
    assert_can_set_clamp_status,
    can_mark_clamp_drawn,
    forbidden_shift_changes,
    shift_edit_perm,
)
from charclamp.infra.db import SessionLocal
from charclamp.infra.security import verify_password

STATUS_LABELS = {
    Clamp.STATUS_STACKED: "已码窑",
    Clamp.STATUS_BURNING: "焖烧中",
    Clamp.STATUS_DRAWN: "已出炭",
}


def _set_flash(request: Request, message: str, category: str = "ok") -> None:
    data = dict(request.session or {})
    data["flash"] = message
    data["flash_cat"] = category
    request.set_session(data)


def _pop_flash(request: Request) -> tuple[str | None, str | None]:
    data = dict(request.session or {})
    message = data.pop("flash", None)
    category = data.pop("flash_cat", None)
    if message is not None or category is not None:
        request.set_session(data)
    return message, category


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _error_text(message: str, status_code: int) -> Response:
    """后台保存结论以明确的状态码返回（403 越权 / 409 并发冲突 / 400 参数错误）。"""
    return Response(content=message, status_code=status_code, media_type=MediaType.TEXT)


def _naive_utc(dt: datetime) -> datetime:
    """统一为 UTC 朴素时间再比较（datetime-local 输入与库中值都按 UTC 处理）。"""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _collect_shift_changes(shift: BurnShift, data: dict[str, Any]) -> dict[str, Any]:
    """把提交的表单解析成「实际发生变化」的字段集；解析失败抛 ValueError。"""
    changes: dict[str, Any] = {}
    if "notes" in data:
        notes = (data.get("notes") or "").strip()
        if notes != shift.notes:
            changes["notes"] = notes
    if "peak_temp_c" in data:
        raw = (data.get("peak_temp_c") or "").strip()
        try:
            peak = float(raw) if raw else None
        except ValueError:
            raise ValueError("峰值温度必须是数字") from None
        if peak != shift.peak_temp_c:
            changes["peak_temp_c"] = peak
    if "started_at" in data:
        raw = (data.get("started_at") or "").strip()
        if not raw:
            raise ValueError("开始时刻不能为空")
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError("开始时刻格式无效") from None
        if _naive_utc(started) != _naive_utc(shift.started_at):
            changes["started_at"] = started
    if "charcoal_grade" in data:
        grade = (data.get("charcoal_grade") or "B").strip()
        if grade != shift.charcoal_grade:
            changes["charcoal_grade"] = grade
    return changes


async def _load_timeline_context(clamp_id: int | None = None) -> dict[str, Any]:
    async with SessionLocal() as db:
        clamps = list(
            (
                await db.execute(
                    select(Clamp)
                    .options(selectinload(Clamp.site), selectinload(Clamp.shifts))
                    .order_by(Clamp.code)
                )
            )
            .scalars()
            .all()
        )
        query = (
            select(BurnShift)
            .options(
                selectinload(BurnShift.clamp).selectinload(Clamp.site),
                selectinload(BurnShift.created_by),
            )
            .order_by(BurnShift.started_at.desc())
        )
        if clamp_id is not None:
            query = query.where(BurnShift.clamp_id == clamp_id)
        shifts = list((await db.execute(query)).scalars().all())
        site_name = clamps[0].site.name if clamps else "乌石岗焖烧坞"
    return {
        "clamps": clamps,
        "shifts": shifts,
        "active_clamp_id": clamp_id,
        "status_labels": STATUS_LABELS,
        "site_name": site_name,
    }


def _shift_perms(shifts: list[BurnShift], user: User) -> dict[int, ShiftEditPerm]:
    """时间轴上每条班次的改写权限结论（与后台保存共用同一套规则）。"""
    return {s.id: shift_edit_perm(user, s, s.clamp) for s in shifts}


class AuthController(Controller):
    path = ""
    tags = ["auth"]

    @get("/login", media_type=MediaType.HTML)
    async def login_page(self, request: Request) -> Template:
        flash, flash_cat = _pop_flash(request)
        return Template(
            template_name="login.html",
            context={"flash": flash, "flash_cat": flash_cat},
        )

    @post("/login")
    async def login(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        async with SessionLocal() as db:
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if not user or not verify_password(password, user.password_hash):
                request.set_session({"flash": "用户名或密码错误", "flash_cat": "error"})
                return Redirect("/login")
            request.set_session({"user_id": user.id})
        return Redirect("/")

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        request.clear_session()
        return Redirect("/login")


class TimelineController(Controller):
    path = ""
    tags = ["timeline"]

    @get("/", media_type=MediaType.HTML)
    async def timeline(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        ctx = await _load_timeline_context(clamp_id)
        return Template(
            template_name="timeline.html",
            context={
                **ctx,
                "shift_perms": _shift_perms(ctx["shifts"], request.user),
                "user": request.user,
                "flash": flash,
                "flash_cat": flash_cat,
            },
        )

    @get("/timeline/partial", media_type=MediaType.HTML)
    async def timeline_partial(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        ctx = await _load_timeline_context(clamp_id)
        return Template(
            template_name="partials/board.html",
            context={
                **ctx,
                "shift_perms": _shift_perms(ctx["shifts"], request.user),
                "user": request.user,
            },
        )

    @get("/drawer/shift-new", media_type=MediaType.HTML)
    async def drawer_shift_new(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        async with SessionLocal() as db:
            clamps = list((await db.execute(select(Clamp).order_by(Clamp.code))).scalars().all())
        return Template(
            template_name="partials/drawer_shift.html",
            context={
                "clamps": clamps,
                "preselect_clamp_id": clamp_id,
                "user": request.user,
            },
        )

    @get("/drawer/shift/{shift_id:int}/edit", media_type=MediaType.HTML)
    async def drawer_shift_edit(self, request: Request, shift_id: int) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            shift = (
                await db.execute(
                    select(BurnShift)
                    .where(BurnShift.id == shift_id)
                    .options(
                        selectinload(BurnShift.clamp).selectinload(Clamp.site),
                        selectinload(BurnShift.created_by),
                    )
                )
            ).scalar_one_or_none()
            if not shift:
                return Redirect("/")
            perm = shift_edit_perm(request.user, shift, shift.clamp)
        return Template(
            template_name="partials/drawer_shift_edit.html",
            context={
                "shift": shift,
                "clamp": shift.clamp,
                "perm": perm,
                "status_labels": STATUS_LABELS,
                "user": request.user,
            },
        )

    @get("/drawer/clamp/{clamp_id:int}", media_type=MediaType.HTML)
    async def drawer_clamp(self, request: Request, clamp_id: int) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(selectinload(Clamp.shifts), selectinload(Clamp.site))
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
        can_drawn, drawn_msg = can_mark_clamp_drawn(clamp)
        return Template(
            template_name="partials/drawer_clamp.html",
            context={
                "clamp": clamp,
                "status_labels": STATUS_LABELS,
                "can_drawn": can_drawn,
                "drawn_msg": drawn_msg,
                "user": request.user,
            },
        )


class ShiftController(Controller):
    path = "/shifts"
    tags = ["shifts"]

    @post("/new")
    async def create_shift(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        started_raw = data.get("started_at") or ""
        started_at = datetime.fromisoformat(started_raw) if started_raw else datetime.utcnow()
        peak_raw = (data.get("peak_temp_c") or "").strip()
        peak = float(peak_raw) if peak_raw else None
        clamp_id = int(data["clamp_id"])
        async with SessionLocal() as db:
            shift = BurnShift(
                clamp_id=clamp_id,
                started_at=started_at,
                peak_temp_c=peak,
                charcoal_grade=(data.get("charcoal_grade") or "B").strip(),
                notes=(data.get("notes") or "").strip(),
                created_by_id=request.user.id,
            )
            db.add(shift)
            clamp = (
                await db.execute(select(Clamp).where(Clamp.id == clamp_id))
            ).scalar_one_or_none()
            if clamp and clamp.status == Clamp.STATUS_STACKED:
                clamp.status = Clamp.STATUS_BURNING
            await db.commit()
        _set_flash(request, "焖烧班次已登记", "ok")
        return Redirect(f"/?clamp_id={clamp_id}")

    @post("/{shift_id:int}/edit")
    async def edit_shift(
        self,
        request: Request,
        shift_id: int,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Response | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            shift = (
                await db.execute(select(BurnShift).where(BurnShift.id == shift_id))
            ).scalar_one_or_none()
            if not shift:
                return _error_text("班次不存在", 404)
            # 锁定所属炭窑行，避免保存期间窑态被并发改为「已出炭」。
            clamp = (
                await db.execute(
                    select(Clamp).where(Clamp.id == shift.clamp_id).with_for_update()
                )
            ).scalar_one()

            # 后台保存与抽屉渲染共用同一权限矩阵：越权一律拒绝，不做部分生效。
            perm = shift_edit_perm(request.user, shift, clamp)
            if not perm.allowed:
                return _error_text(perm.reason or "无权改写该班次", 403)
            try:
                changes = _collect_shift_changes(shift, data)
            except ValueError as exc:
                return _error_text(str(exc), 400)
            forbidden = forbidden_shift_changes(perm, list(changes))
            if forbidden:
                labels = "、".join(SHIFT_FIELD_LABELS.get(f, f) for f in forbidden)
                return _error_text(f"权限不足：{labels}仅管理员可改", 403)

            # 乐观锁：优先用表单带回的版本号；未带时用请求开始时读到的版本号。
            # 并发保存时只有一笔 UPDATE 能命中旧版本，其余落空报 409。
            expected_version = _parse_optional_int(data.get("version"))
            if expected_version is None:
                expected_version = shift.version
            clamp_id = clamp.id
            if changes:
                result = await db.execute(
                    update(BurnShift)
                    .where(
                        BurnShift.id == shift_id,
                        BurnShift.version == expected_version,
                    )
                    .values(**changes, version=BurnShift.version + 1)
                )
                if result.rowcount != 1:
                    await db.rollback()
                    return _error_text("该班次刚被他人修改，请刷新后重试", 409)
                await db.commit()
        if changes:
            _set_flash(request, "班次已更新", "ok")
        else:
            _set_flash(request, "班次内容未变化", "ok")
        return Redirect(f"/?clamp_id={clamp_id}")


class ClampController(Controller):
    path = "/clamps"
    tags = ["clamps"]

    @post("/{clamp_id:int}/status")
    async def set_status(
        self,
        request: Request,
        clamp_id: int,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        new_status = (data.get("status") or "").strip()
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(selectinload(Clamp.shifts))
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            try:
                assert_can_set_clamp_status(clamp, new_status)
                clamp.status = new_status
                await db.commit()
                _set_flash(request, f"窑 {clamp.code} 状态已更新", "ok")
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
        return Redirect(f"/?clamp_id={clamp_id}")
