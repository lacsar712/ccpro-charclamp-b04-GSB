from __future__ import annotations

from datetime import timedelta

from charclamp.domain.models import BurnShift, Clamp, Site, User, utcnow
from charclamp.infra.db import SyncSessionLocal
from charclamp.infra.security import hash_password


def _upsert_user(session, username: str, role: str, password: str) -> User:
    user = session.query(User).filter_by(username=username).first()
    if not user:
        user = User(username=username, role=role, password_hash=hash_password(password))
        session.add(user)
    else:
        user.password_hash = hash_password(password)
        user.role = role
    return user


def seed_demo() -> None:
    with SyncSessionLocal() as session:
        admin = _upsert_user(session, "admin", "admin", "123456")
        worker = _upsert_user(session, "worker", "worker", "123456")
        worker2 = _upsert_user(session, "worker2", "worker", "123456")

        if session.query(Site).first():
            session.commit()
            return

        site = Site(name="乌石岗焖烧坞", location="河谷台地北侧", notes="青冈为主，夜班闷窑")
        session.add(site)
        session.flush()

        c1 = Clamp(site=site, code="坞东-甲", status=Clamp.STATUS_BURNING, wood_species="青冈")
        c2 = Clamp(site=site, code="坞东-乙", status=Clamp.STATUS_STACKED, wood_species="松木")
        c3 = Clamp(site=site, code="河沿-丙", status=Clamp.STATUS_DRAWN, wood_species="栎木")
        session.add_all([c1, c2, c3])
        session.flush()

        now = utcnow()
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # 「当日班」必须落在当天：回推若干小时，但不越过当天 0 点（种子在任意时刻运行都稳定）。
        own_today_start = max(now - timedelta(hours=10), today0)
        other_today_start = max(now - timedelta(hours=3), today0)
        session.add_all(
            [
                # 自建当日班：worker 本人、当天开始 → 工人可改备注
                BurnShift(
                    clamp=c1,
                    started_at=own_today_start,
                    peak_temp_c=455.0,
                    charcoal_grade="A",
                    notes="峰值已过，可出炭",
                    created_by=worker,
                ),
                # 自建非当日班：worker 本人但始于昨天 → 工人只读（当天边界）
                BurnShift(
                    clamp=c1,
                    started_at=now - timedelta(days=1, hours=2),
                    peak_temp_c=430.0,
                    charcoal_grade="B",
                    notes="昨日已交班",
                    created_by=worker,
                ),
                # 他人班：worker2 的当日班 → worker 只读，worker2 可改备注
                BurnShift(
                    clamp=c2,
                    started_at=other_today_start,
                    peak_temp_c=None,
                    charcoal_grade="B",
                    notes="刚点火，未测峰值",
                    created_by=worker2,
                ),
                # 已出炭窑班：河沿-丙 已出炭 → 全员只读（含管理员）
                BurnShift(
                    clamp=c3,
                    started_at=now - timedelta(days=2),
                    peak_temp_c=520.0,
                    charcoal_grade="A+",
                    notes="已出炭班次",
                    created_by=admin,
                ),
            ]
        )
        session.commit()
