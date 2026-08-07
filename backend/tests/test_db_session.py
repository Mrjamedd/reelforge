"""Verify DB session uses aiosqlite and enables WAL."""
import pytest
from sqlalchemy import text

from app.db.session import engine, get_db


@pytest.mark.asyncio
async def test_engine_dialect_is_sqlite():
    assert engine.dialect.name == "sqlite"


@pytest.mark.asyncio
async def test_wal_mode_enabled():
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        assert mode.lower() == "wal", f"expected WAL, got {mode}"


@pytest.mark.asyncio
async def test_get_db_yields_session():
    async for session in get_db():
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
        break
