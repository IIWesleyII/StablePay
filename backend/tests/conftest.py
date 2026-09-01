import sys
from collections.abc import AsyncIterator
from datetime import datetime
from datetime import timezone
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
from database.models import Merchant  # noqa: E402
from api.authentication import get_authenticated_merchant  # noqa: E402
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


@pytest_asyncio.fixture
async def authenticated_merchant(test_session: AsyncSession) -> Merchant:
    current_time = datetime.now(timezone.utc)
    merchant = Merchant(
        id="mch_authenticated_test",
        name="Authenticated Test Merchant",
        wallet_address="0x2222222222222222222222222222222222222222",
        webhook_url="https://merchant.test/webhooks/stablepay",
        is_active=True,
        created_at=current_time,
        updated_at=current_time,
    )
    test_session.add(merchant)
    await test_session.commit()
    return merchant


@pytest_asyncio.fixture
async def authenticated_client(
    client: AsyncClient,
    authenticated_merchant: Merchant,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_authenticated_merchant] = (
        lambda: authenticated_merchant
    )

    yield client

    app.dependency_overrides.pop(get_authenticated_merchant, None)
