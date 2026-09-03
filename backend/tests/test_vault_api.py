from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import Vault
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer


VAULT_ADDRESS = "0x3333333333333333333333333333333333333333"


async def open_test_vault(client: AsyncClient) -> tuple[str, str]:
    wallet = Account.create()
    challenge_response = await client.post(
        "/vaults/challenges",
        json={"wallet_address": wallet.address},
    )
    assert challenge_response.status_code == 201
    challenge = challenge_response.json()
    signature = Account.sign_message(
        encode_defunct(text=challenge["message"]),
        wallet.key,
    ).signature.hex()
    vault_response = await client.post(
        "/vaults",
        json={"challenge_id": challenge["id"], "signature": signature},
    )
    assert vault_response.status_code == 201, vault_response.text
    body = vault_response.json()
    return body["id"], body["access_token"]


@pytest.mark.asyncio
async def test_vault_token_authenticates_balance_endpoint(client: AsyncClient):
    _, token = await open_test_vault(client)

    response = await client.get(
        "/vaults/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["balance"] == "0"
    assert "access_token" not in response.json()


@pytest.mark.asyncio
async def test_deposit_intent_returns_exact_on_chain_instructions(
    client: AsyncClient,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    _, token = await open_test_vault(client)

    response = await client.post(
        "/vaults/deposits",
        json={"amount": "5.00"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == "5"
    assert body["recipient_address"] == VAULT_ADDRESS
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_micropayment_is_idempotent_and_updates_both_balances(
    authenticated_client: AsyncClient,
    authenticated_merchant,
    test_session: AsyncSession,
):
    vault_id, token = await open_test_vault(authenticated_client)
    vault = await test_session.get(Vault, vault_id)
    assert vault is not None
    system = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.SYSTEM, "test-custody"
    )
    vault_account = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.VAULT, vault.id
    )
    await post_ledger_transfer(
        test_session,
        source_account=system,
        destination_account=vault_account,
        amount_atomic=10_000,
        transaction_type=LedgerTransactionType.DEPOSIT,
        idempotency_key="test-funding",
        reference_id="dep_test",
    )
    await test_session.commit()

    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "api-call-42",
    }
    payload = {
        "merchant_id": authenticated_merchant.id,
        "amount": "0.001",
        "reference": "weather-api-call-42",
    }
    first = await authenticated_client.post(
        "/vaults/micropayments", json=payload, headers=headers
    )
    retry = await authenticated_client.post(
        "/vaults/micropayments", json=payload, headers=headers
    )
    vault_balance = await authenticated_client.get(
        "/vaults/me", headers={"Authorization": f"Bearer {token}"}
    )
    merchant_balance = await authenticated_client.get("/merchants/me/balance")
    merchant_receipt = await authenticated_client.get(
        f"/merchants/me/micropayments/{first.json()['id']}"
    )

    assert first.status_code == 201
    assert retry.status_code == 201
    assert first.json()["replayed"] is False
    assert retry.json()["replayed"] is True
    assert retry.json()["id"] == first.json()["id"]
    assert vault_balance.json()["balance"] == "0.009"
    assert merchant_balance.json()["available_balance"] == "0.001"
    assert merchant_receipt.status_code == 200
    assert merchant_receipt.json()["reference"] == "weather-api-call-42"


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_different_purchase(
    authenticated_client: AsyncClient,
    authenticated_merchant,
    test_session: AsyncSession,
):
    vault_id, token = await open_test_vault(authenticated_client)
    system = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.SYSTEM, "test-custody"
    )
    vault_account = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.VAULT, vault_id
    )
    await post_ledger_transfer(
        test_session,
        source_account=system,
        destination_account=vault_account,
        amount_atomic=10_000,
        transaction_type=LedgerTransactionType.DEPOSIT,
        idempotency_key="test-funding",
        reference_id="dep_test",
    )
    await test_session.commit()
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "one-logical-purchase",
    }
    first = await authenticated_client.post(
        "/vaults/micropayments",
        json={
            "merchant_id": authenticated_merchant.id,
            "amount": "0.001",
            "reference": "call-one",
        },
        headers=headers,
    )
    conflicting_retry = await authenticated_client.post(
        "/vaults/micropayments",
        json={
            "merchant_id": authenticated_merchant.id,
            "amount": "0.002",
            "reference": "call-two",
        },
        headers=headers,
    )

    assert first.status_code == 201
    assert conflicting_retry.status_code == 409
    assert "different ledger transfer" in conflicting_retry.json()["detail"]


@pytest.mark.asyncio
async def test_vault_cannot_spend_more_than_its_balance(
    authenticated_client: AsyncClient,
    authenticated_merchant,
):
    _, token = await open_test_vault(authenticated_client)
    response = await authenticated_client.post(
        "/vaults/micropayments",
        json={
            "merchant_id": authenticated_merchant.id,
            "amount": "0.001",
            "reference": "unfunded-call",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "unfunded-call",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Vault has insufficient USDC balance"
