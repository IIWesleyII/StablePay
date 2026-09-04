import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from services.ledger import SYSTEM_CUSTODY_OWNER_ID
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer


async def fund_merchant(
    session: AsyncSession,
    merchant_id: str,
    amount_atomic: int = 10_000,
) -> None:
    custody = await get_or_create_ledger_account(
        session, LedgerOwnerType.SYSTEM, SYSTEM_CUSTODY_OWNER_ID
    )
    merchant = await get_or_create_ledger_account(
        session, LedgerOwnerType.MERCHANT, merchant_id
    )
    await post_ledger_transfer(
        session,
        source_account=custody,
        destination_account=merchant,
        amount_atomic=amount_atomic,
        transaction_type=LedgerTransactionType.MICROPAYMENT,
        idempotency_key="api-settlement-funding",
        reference_id="test-funding",
    )
    await session.commit()


@pytest.mark.asyncio
async def test_merchant_can_reserve_and_cancel_settlement(
    authenticated_client: AsyncClient,
    authenticated_merchant,
    test_session: AsyncSession,
):
    await fund_merchant(test_session, authenticated_merchant.id)
    headers = {"Idempotency-Key": "weekly-payout-1"}

    first = await authenticated_client.post(
        "/merchants/me/settlements",
        json={"amount": "0.004"},
        headers=headers,
    )
    retry = await authenticated_client.post(
        "/merchants/me/settlements",
        json={"amount": "0.004"},
        headers=headers,
    )
    reserved_balance = await authenticated_client.get("/merchants/me/balance")
    listed = await authenticated_client.get("/merchants/me/settlements")

    assert first.status_code == 201
    assert retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert first.json()["replayed"] is False
    assert retry.json()["replayed"] is True
    assert first.json()["status"] == "pending"
    assert reserved_balance.json()["available_balance"] == "0.006"
    assert reserved_balance.json()["reserved_balance"] == "0.004"
    assert listed.json()[0]["id"] == first.json()["id"]

    cancelled = await authenticated_client.post(
        f"/merchants/me/settlements/{first.json()['id']}/cancel"
    )
    restored_balance = await authenticated_client.get("/merchants/me/balance")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert restored_balance.json()["available_balance"] == "0.01"
    assert restored_balance.json()["reserved_balance"] == "0"


@pytest.mark.asyncio
async def test_settlement_defaults_to_all_available_balance(
    authenticated_client: AsyncClient,
    authenticated_merchant,
    test_session: AsyncSession,
):
    await fund_merchant(test_session, authenticated_merchant.id)

    response = await authenticated_client.post(
        "/merchants/me/settlements",
        json={},
        headers={"Idempotency-Key": "settle-everything"},
    )

    assert response.status_code == 201
    assert response.json()["amount"] == "0.01"


@pytest.mark.asyncio
async def test_settlement_rejects_overdraw(
    authenticated_client: AsyncClient,
    authenticated_merchant,
    test_session: AsyncSession,
):
    await fund_merchant(test_session, authenticated_merchant.id)

    response = await authenticated_client.post(
        "/merchants/me/settlements",
        json={"amount": "0.02"},
        headers={"Idempotency-Key": "too-large"},
    )

    assert response.status_code == 409
    assert "insufficient available balance" in response.json()["detail"]


@pytest.mark.asyncio
async def test_settlement_requires_idempotency_key(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.post(
        "/merchants/me/settlements",
        json={},
    )

    assert response.status_code == 422
