from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import TransactionNotMinedError
from blockchain.base import UsdcTransfer
from blockchain.base import get_base_sepolia_client
from config import settings
from database.models import WebhookEvent
from main import app


TRANSACTION_HASH = "0x" + "ab" * 32
SENDER_ADDRESS = "0x1111111111111111111111111111111111111111"


class FakeBlockchainClient:
    def __init__(self) -> None:
        self.confirmations = settings.payment_required_confirmations
        self.amount = Decimal("1.00")
        self.recipient = settings.merchant_wallet_address
        self.error: Exception | None = None

    async def get_usdc_transfers(self, transaction_hash: str):
        if self.error is not None:
            raise self.error

        return [
            UsdcTransfer(
                transaction_hash=transaction_hash,
                log_index=0,
                sender=SENDER_ADDRESS,
                recipient=self.recipient,
                raw_amount=int(self.amount * Decimal(1_000_000)),
                amount=self.amount,
                block_number=100,
                confirmations=self.confirmations,
            )
        ]


@pytest_asyncio.fixture
async def verification_client(client: AsyncClient):
    fake_blockchain = FakeBlockchainClient()
    app.dependency_overrides[get_base_sepolia_client] = lambda: fake_blockchain

    yield client, fake_blockchain


async def create_payment(client: AsyncClient, amount: str = "1.00") -> str:
    response = await client.post("/payments", json={"amount": amount})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_verified_transfer_confirms_payment(
    verification_client,
    test_session: AsyncSession,
):
    client, fake_blockchain = verification_client
    payment_id = await create_payment(client)

    response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["payment"]["status"] == "confirmed"
    assert result["payment"]["transaction_hash"] == TRANSACTION_HASH
    assert result["payment"]["detected_at"] is not None
    assert result["payment"]["confirmed_at"] is not None
    assert result["sender_address"] == SENDER_ADDRESS
    assert result["confirmations"] == settings.payment_required_confirmations

    event_result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.payment_id == payment_id)
    )
    event = event_result.scalar_one()
    assert event.event_type == "payment.confirmed"
    assert event.destination_url == settings.merchant_webhook_url
    assert event.payload["data"]["payment"]["transaction_hash"] == TRANSACTION_HASH


@pytest.mark.asyncio
async def test_payment_stays_confirming_until_threshold(
    verification_client,
    test_session: AsyncSession,
):
    client, fake_blockchain = verification_client
    fake_blockchain.confirmations = settings.payment_required_confirmations - 1
    payment_id = await create_payment(client)

    first_response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )
    assert first_response.status_code == 200
    assert first_response.json()["payment"]["status"] == "confirming"

    pending_event_result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.payment_id == payment_id)
    )
    assert pending_event_result.scalar_one_or_none() is None

    fake_blockchain.confirmations = settings.payment_required_confirmations
    second_response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert second_response.status_code == 200
    assert second_response.json()["payment"]["status"] == "confirmed"

    confirmed_event_result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.payment_id == payment_id)
    )
    assert confirmed_event_result.scalar_one().event_type == "payment.confirmed"


@pytest.mark.asyncio
async def test_wrong_transfer_amount_is_rejected(verification_client):
    client, fake_blockchain = verification_client
    fake_blockchain.amount = Decimal("0.99")
    payment_id = await create_payment(client)

    response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert response.status_code == 400
    assert "amount does not match" in response.json()["detail"]


@pytest.mark.asyncio
async def test_wrong_transfer_recipient_is_rejected(verification_client):
    client, fake_blockchain = verification_client
    fake_blockchain.recipient = "0x2222222222222222222222222222222222222222"
    payment_id = await create_payment(client)

    response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert response.status_code == 400
    assert "recipient does not match" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unmined_transaction_can_be_retried(verification_client):
    client, fake_blockchain = verification_client
    fake_blockchain.error = TransactionNotMinedError("Transaction has not been mined")
    payment_id = await create_payment(client)

    response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert response.status_code == 409
    assert "not been mined" in response.json()["detail"]


@pytest.mark.asyncio
async def test_transaction_cannot_pay_two_payments(verification_client):
    client, fake_blockchain = verification_client
    first_payment_id = await create_payment(client)
    second_payment_id = await create_payment(client)

    first_response = await client.post(
        f"/payments/{first_payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )
    assert first_response.status_code == 200

    second_response = await client.post(
        f"/payments/{second_payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert second_response.status_code == 409
    assert "another payment" in second_response.json()["detail"]


@pytest.mark.asyncio
async def test_confirmed_verification_is_safe_to_retry(
    verification_client,
    test_session: AsyncSession,
):
    client, fake_blockchain = verification_client
    payment_id = await create_payment(client)

    first_response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )
    second_response = await client.post(
        f"/payments/{payment_id}/verify",
        json={"transaction_hash": TRANSACTION_HASH},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["payment"]["status"] == "confirmed"

    event_result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.payment_id == payment_id)
    )
    assert len(event_result.scalars().all()) == 1
