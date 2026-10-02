from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from charclamp.domain.models import Base

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://charclamp:charclamp@127.0.0.1:6150/charclamp",
)
DATABASE_URL_SYNC = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg2://charclamp:charclamp@127.0.0.1:6150/charclamp",
)

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

sync_engine = create_engine(DATABASE_URL_SYNC, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)


def sync_create_all() -> None:
    Base.metadata.create_all(sync_engine)
    _ensure_shift_edit_columns()


def _ensure_shift_edit_columns() -> None:
    """老库补列：create_all 不会改已存在的表，这里为班次改写功能幂等补齐。"""
    if sync_engine.dialect.name != "postgresql":
        return
    with sync_engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE burn_shifts ADD COLUMN IF NOT EXISTS created_by_id INTEGER REFERENCES users(id)")
        )
        conn.execute(
            text("ALTER TABLE burn_shifts ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
        )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
