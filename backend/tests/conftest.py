import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool


APP_DIRECTORY = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from database.database import Base  # noqa: E402
from database.database import get_session  # noqa: E402
from main import app  # noqa: E402


@pytest_asyncio.fixture
async def test_session() -> AsyncIterator[AsyncSession]:
    """Provide a clean, temporary database session for each test."""

    test_engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
    )

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(
        test_engine,
        expire_on_commit=False,
    )

    async with TestSession() as session:
        yield session

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(test_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Provide an API client whose requests use the temporary database."""

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield test_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
