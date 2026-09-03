"""Customer vault, deposit, and off-chain micropayment endpoints."""

from datetime import datetime
from datetime import timezone
from decimal import Decimal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.vault_authentication import get_authenticated_vault
from config import settings
from database.database import get_session
from database.models import LedgerAccount
from database.models import LedgerEntry
from database.models import LedgerTransaction
from database.models import Merchant
from database.models import Vault
from database.models import VaultDeposit
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from schemas.vaults import LedgerActivityResponse
from schemas.vaults import MicropaymentCreate
from schemas.vaults import MicropaymentResponse
from schemas.vaults import VaultChallengeCreate
from schemas.vaults import VaultChallengeResponse
from schemas.vaults import VaultCreate
from schemas.vaults import VaultCreatedResponse
from schemas.vaults import VaultDepositCreate
from schemas.vaults import VaultDepositResponse
from schemas.vaults import VaultResponse
from services.ledger import IdempotencyConflictError
from services.ledger import InsufficientBalanceError
from services.ledger import LedgerError
from services.ledger import atomic_to_usdc
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer
from services.ledger import usdc_to_atomic
from services.vault_deposits import VaultDepositError
from services.vault_deposits import create_vault_deposit
from services.vaults import VaultError
from services.vaults import create_vault_challenge
from services.vaults import create_vault_from_signature


router = APIRouter(prefix="/vaults", tags=["vaults"])


@router.post(
    "/challenges",
    response_model=VaultChallengeResponse,
    status_code=201,
)
async def request_vault_challenge(
    request: VaultChallengeCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create the exact free message a customer wallet must sign once."""

    try:
        challenge = await create_vault_challenge(
            session,
            request.wallet_address,
        )
    except VaultError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return challenge


@router.post("", response_model=VaultCreatedResponse, status_code=201)
async def open_vault(
    request: VaultCreate,
    session: AsyncSession = Depends(get_session),
):
    """Verify wallet ownership and return a reusable secret exactly once."""

    try:
        created = await create_vault_from_signature(
            session,
            request.challenge_id,
            request.signature,
        )
        await get_or_create_ledger_account(
            session,
            LedgerOwnerType.VAULT,
            created.vault.id,
            created.vault.created_at,
        )
    except VaultError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return VaultCreatedResponse(
        id=created.vault.id,
        wallet_address=created.vault.wallet_address,
        access_token=created.access_token,
        balance=Decimal(0),
        currency="USDC",
        created_at=created.vault.created_at,
    )


@router.get("/me", response_model=VaultResponse)
async def get_current_vault(
    vault: Vault = Depends(get_authenticated_vault),
    session: AsyncSession = Depends(get_session),
):
    account = await _get_vault_account(session, vault.id)
    return VaultResponse(
        id=vault.id,
        wallet_address=vault.wallet_address,
        balance=account.balance,
        currency=account.currency,
        created_at=vault.created_at,
    )


@router.post(
    "/deposits",
    response_model=VaultDepositResponse,
    status_code=201,
)
async def create_deposit(
    request: VaultDepositCreate,
    vault: Vault = Depends(get_authenticated_vault),
    session: AsyncSession = Depends(get_session),
):
    if settings.stablepay_vault_address is None:
        raise HTTPException(
            status_code=503,
            detail="STABLEPAY_VAULT_ADDRESS is not configured",
        )
    conflicting_merchant = await session.execute(
        select(Merchant.id).where(
            Merchant.wallet_address.ilike(settings.stablepay_vault_address)
        )
    )
    if conflicting_merchant.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "STABLEPAY_VAULT_ADDRESS must be different from every merchant "
                "payment address"
            ),
        )
    try:
        deposit = await create_vault_deposit(
            session,
            vault,
            usdc_to_atomic(request.amount),
        )
    except (LedgerError, VaultDepositError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await session.commit()
    return _deposit_response(deposit, vault)


@router.get("/deposits", response_model=list[VaultDepositResponse])
async def list_deposits(
    limit: int = Query(default=50, ge=1, le=100),
    vault: Vault = Depends(get_authenticated_vault),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(VaultDeposit)
        .where(VaultDeposit.vault_id == vault.id)
        .order_by(VaultDeposit.created_at.desc(), VaultDeposit.id.desc())
        .limit(limit)
    )
    return [_deposit_response(deposit, vault) for deposit in result.scalars()]


@router.get("/deposits/{deposit_id}", response_model=VaultDepositResponse)
async def get_deposit(
    deposit_id: str,
    vault: Vault = Depends(get_authenticated_vault),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(VaultDeposit).where(
            VaultDeposit.id == deposit_id,
            VaultDeposit.vault_id == vault.id,
        )
    )
    deposit = result.scalar_one_or_none()
    if deposit is None:
        raise HTTPException(status_code=404, detail="Vault deposit not found")
    return _deposit_response(deposit, vault)


@router.post(
    "/micropayments",
    response_model=MicropaymentResponse,
    status_code=201,
)
async def create_micropayment(
    request: MicropaymentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    vault: Vault = Depends(get_authenticated_vault),
    session: AsyncSession = Depends(get_session),
):
    """Move existing vault value to a merchant without a blockchain transfer."""

    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 128:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must contain 1 to 128 characters",
        )

    merchant = await session.get(Merchant, request.merchant_id)
    if merchant is None or not merchant.is_active:
        raise HTTPException(status_code=404, detail="Active merchant not found")

    amount_atomic = usdc_to_atomic(request.amount)
    internal_key = f"micropayment:{vault.id}:{normalized_key}"
    try:
        posted = await _post_micropayment(
            session,
            vault,
            merchant,
            amount_atomic,
            internal_key,
            request.reference,
        )
        await session.commit()
    except IntegrityError:
        # A simultaneous retry may win the unique-key race. Reload and replay it.
        await session.rollback()
        try:
            posted = await _post_micropayment(
                session,
                vault,
                merchant,
                amount_atomic,
                internal_key,
                request.reference,
            )
            await session.commit()
        except (LedgerError, IntegrityError) as error:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
    except (InsufficientBalanceError, IdempotencyConflictError, LedgerError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    return MicropaymentResponse(
        id=posted.transaction.id,
        transaction_type=posted.transaction.transaction_type,
        merchant_id=merchant.id,
        amount=atomic_to_usdc(posted.transaction.amount_atomic),
        currency="USDC",
        reference=posted.transaction.reference_id,
        vault_balance=atomic_to_usdc(posted.source_balance_atomic),
        replayed=posted.replayed,
        created_at=posted.transaction.created_at,
    )


@router.get("/activity", response_model=list[LedgerActivityResponse])
async def list_vault_activity(
    limit: int = Query(default=50, ge=1, le=100),
    vault: Vault = Depends(get_authenticated_vault),
    session: AsyncSession = Depends(get_session),
):
    account = await _get_vault_account(session, vault.id)
    result = await session.execute(
        select(LedgerTransaction, LedgerEntry.amount_atomic)
        .join(
            LedgerEntry,
            LedgerEntry.transaction_id == LedgerTransaction.id,
        )
        .where(LedgerEntry.account_id == account.id)
        .order_by(LedgerTransaction.created_at.desc(), LedgerTransaction.id.desc())
        .limit(limit)
    )
    return [
        LedgerActivityResponse(
            id=transaction.id,
            transaction_type=transaction.transaction_type,
            amount=atomic_to_usdc(signed_amount),
            reference=transaction.reference_id,
            created_at=transaction.created_at,
        )
        for transaction, signed_amount in result
    ]


async def _post_micropayment(
    session: AsyncSession,
    vault: Vault,
    merchant: Merchant,
    amount_atomic: int,
    idempotency_key: str,
    reference: str,
):
    source = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.VAULT,
        vault.id,
    )
    destination = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.MERCHANT,
        merchant.id,
    )
    return await post_ledger_transfer(
        session,
        source_account=source,
        destination_account=destination,
        amount_atomic=amount_atomic,
        transaction_type=LedgerTransactionType.MICROPAYMENT,
        idempotency_key=idempotency_key,
        reference_id=reference,
        current_time=datetime.now(timezone.utc),
    )


async def _get_vault_account(
    session: AsyncSession,
    vault_id: str,
) -> LedgerAccount:
    result = await session.execute(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == LedgerOwnerType.VAULT,
            LedgerAccount.owner_id == vault_id,
            LedgerAccount.currency == "USDC",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=500, detail="Vault ledger account is missing")
    return account


def _deposit_response(
    deposit: VaultDeposit,
    vault: Vault,
) -> VaultDepositResponse:
    if settings.stablepay_vault_address is None:
        raise HTTPException(status_code=503, detail="Vault address is not configured")
    return VaultDepositResponse(
        id=deposit.id,
        vault_id=deposit.vault_id,
        amount=deposit.amount,
        currency="USDC",
        chain="base-sepolia",
        recipient_address=settings.stablepay_vault_address,
        sender_address=vault.wallet_address,
        status=deposit.status,
        transaction_hash=deposit.transaction_hash,
        transaction_block_number=deposit.transaction_block_number,
        transaction_log_index=deposit.transaction_log_index,
        created_at=deposit.created_at,
        expires_at=deposit.expires_at,
        confirmed_at=deposit.confirmed_at,
    )
