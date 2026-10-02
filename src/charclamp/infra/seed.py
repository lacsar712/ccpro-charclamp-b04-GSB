from __future__ import annotations

from datetime import timedelta, timezone

from charclamp.domain.models import BurnShift, Clamp, Site, User, utcnow
from charclamp.domain.rules import APP_TZ
from charclamp.infra.db import SyncSessionLocal
from charclamp.infra.security import hash_password


def _ensure_user(session, username: str, role: str) -> User:
    user = session.query(User).filter_by(username=username).first()
    if not user:
        user = User(username=username, role=role, password_hash=hash_password("123456"))
        session.add(user)
    else:
        user.password_hash = hash_password("123456")
        user.role = role
    return user


def seed_demo() -> None:
    with SyncSessionLocal() as session:
        admin = _ensure_user(session, "admin", "admin")
        worker = _ensure_user(session, "worker", "worker")
        # 第二名操作工：用于验证“他人班次不可改”与并发改写。
        worker2 = _ensure_user(session, "worker2", "worker")
        session.flush()

        existing = session.query(Site).first()
        if existing:
            _reconcile_demo_shifts(session, worker, worker2)
            session.commit()
            return

        site = Site(name="乌石岗焖烧坞", location="河谷台地北侧", notes="青冈为主，夜班闷窑")
        session.add(site)
        session.flush()

        c1 = Clamp(site=site, code="坞东-甲", status=Clamp.STATUS_BURNING, wood_species="青冈")
        c2 = Clamp(site=site, code="坞东-乙", status=Clamp.STATUS_BURNING, wood_species="松木")
        c3 = Clamp(site=site, code="河沿-丙", status=Clamp.STATUS_DRAWN, wood_species="栎木")
        session.add_all([c1, c2, c3])
        session.flush()

        now = utcnow()
        # 当天本地 09:00（转 UTC 存储），保证“当天班次”判定不随种子运行时刻漂移。
        today_local = now.astimezone(APP_TZ).replace(hour=9, minute=0, second=0, microsecond=0)
        today_9am = today_local.astimezone(timezone.utc)

        session.add_all(
            [
                # 他人班：登记人 worker2，worker 打开只读；admin 可改峰值/时刻。
                BurnShift(
                    clamp=c1,
                    started_at=now - timedelta(hours=10),
                    peak_temp_c=455.0,
                    charcoal_grade="A",
                    notes="峰值已过，可出炭（他人班次）",
                    created_by=worker2,
                ),
                # 自建当日班：worker 本人、当天——worker 仅可改备注。
                BurnShift(
                    clamp=c2,
                    started_at=today_9am,
                    peak_temp_c=None,
                    charcoal_grade="B",
                    notes="刚点火，未测峰值（本人当天班次）",
                    created_by=worker,
                ),
                # 已出炭窑班：全员只读，admin 也不可改。
                BurnShift(
                    clamp=c3,
                    started_at=now - timedelta(days=2),
                    peak_temp_c=520.0,
                    charcoal_grade="A+",
                    notes="已出炭班次（全员只读）",
                    created_by=worker,
                ),
            ]
        )
        session.commit()


def _reconcile_demo_shifts(session, worker: User, worker2: User) -> None:
    """旧卷对账：按窑号把种子班次修正为三场景所需的归属/时刻/窑态。"""
    now = utcnow()
    today_9am = (
        now.astimezone(APP_TZ)
        .replace(hour=9, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    expectations = {
        "坞东-甲": (Clamp.STATUS_BURNING, worker2, None),
        "坞东-乙": (Clamp.STATUS_BURNING, worker, today_9am),
        "河沿-丙": (Clamp.STATUS_DRAWN, worker, None),
    }
    for code, (status, owner, today_start) in expectations.items():
        clamp = session.query(Clamp).filter_by(code=code).first()
        if clamp is None:
            continue
        clamp.status = status
        shift = max(clamp.shifts, key=lambda s: s.started_at, default=None)
        if shift is None:
            continue
        shift.created_by_id = owner.id
        if shift.created_at is None:
            shift.created_at = shift.started_at
        if today_start is not None:
            # 自建班必须落在“当天”，旧卷里的历史时刻每天随种子校正。
            shift.started_at = today_start
