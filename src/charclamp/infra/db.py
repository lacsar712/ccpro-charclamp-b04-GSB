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


# 班次归属/乐观锁列：旧库幂等补齐（create_all 不会 ALTER 已存在的表）。
_SHIFT_COLUMN_MIGRATIONS = (
    "ALTER TABLE burn_shifts ADD COLUMN IF NOT EXISTS created_by_id INTEGER REFERENCES users(id)",
    "ALTER TABLE burn_shifts ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE burn_shifts ADD COLUMN IF NOT EXISTS version_id INTEGER",
    "UPDATE burn_shifts SET created_at = started_at WHERE created_at IS NULL",
    "UPDATE burn_shifts SET version_id = 1 WHERE version_id IS NULL",
)


def sync_ensure_shift_columns() -> None:
    with sync_engine.begin() as conn:
        for stmt in _SHIFT_COLUMN_MIGRATIONS:
            conn.execute(text(stmt))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
