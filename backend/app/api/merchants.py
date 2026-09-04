from datetime import datetime
from datetime import timezone

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Response
from sqlalchemy import or_
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.authentication import get_authenticated_merchant
from config import settings
from database.database import get_session
from database.models import Merchant
from database.models import MerchantApiKey
from database.models import LedgerAccount
from database.models import LedgerEntry
from database.models import LedgerTransaction
from database.models import Settlement
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from domain.settlements import SettlementStatus
from schemas.merchants import MerchantBalanceResponse
from schemas.merchants import MerchantMicropaymentResponse
from schemas.merchants import SettlementCreate
from schemas.merchants import SettlementCreatedResponse
from schemas.merchants import SettlementResponse
from schemas.merchants import MerchantApiKeyCreate
from schemas.merchants import MerchantApiKeyCreatedResponse
from schemas.merchants import MerchantApiKeyResponse
from schemas.merchants import MerchantResponse
from schemas.merchants import MerchantUpdate
from schemas.vaults import LedgerActivityResponse
from services.ledger import atomic_to_usdc
from services.ledger import usdc_to_atomic
from services.api_keys import ApiKeyError
from services.api_keys import generate_merchant_api_key
from services.merchants import DuplicateMerchantWalletError
from services.merchants import MerchantAccountError
from services.merchants import update_merchant_account
from services.settlements import SettlementError
from services.settlements import cancel_settlement
from services.settlements import create_settlement


router = APIRouter(
    prefix="/merchants",
    tags=["merchants"],
)


@router.get("/me", response_model=MerchantResponse)
async def get_current_merchant(
    merchant: Merchant = Depends(get_authenticated_merchant),
):
    """Return the merchant represented by the Bearer API key."""

    return merchant


@router.patch("/me", response_model=MerchantResponse)
async def update_current_merchant(
    request: MerchantUpdate,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Update settings used for future payments and webhook events."""

    changes = request.model_dump(exclude_unset=True)
    try:
        await update_merchant_account(session, merchant, **changes)
    except DuplicateMerchantWalletError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MerchantAccountError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    await session.commit()
    await session.refresh(merchant)
    return merchant


@router.post(
    "/me/api-keys",
    response_model=MerchantApiKeyCreatedResponse,
    status_code=201,
)
async def create_current_merchant_api_key(
    request: MerchantApiKeyCreate,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Create a key and reveal its plaintext only in this response."""

    try:
        generated = generate_merchant_api_key(
            merchant_id=merchant.id,
            name=request.name,
            expires_at=request.expires_at,
        )
    except ApiKeyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    session.add(generated.record)
    await session.commit()

    return MerchantApiKeyCreatedResponse(
        id=generated.record.id,
        name=generated.record.name,
        key_prefix=generated.record.key_prefix,
        created_at=generated.record.created_at,
        last_used_at=generated.record.last_used_at,
        expires_at=generated.record.expires_at,
        revoked_at=generated.record.revoked_at,
        api_key=generated.plaintext,
    )


@router.get(
    "/me/api-keys",
    response_model=list[MerchantApiKeyResponse],
)
async def list_current_merchant_api_keys(
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """List only the authenticated merchant's safe key metadata."""

    result = await session.execute(
        select(MerchantApiKey)
        .where(MerchantApiKey.merchant_id == merchant.id)
        .order_by(MerchantApiKey.created_at.desc(), MerchantApiKey.id.desc())
    )
    return list(result.scalars())


@router.delete(
    "/me/api-keys/{api_key_id}",
    status_code=204,
)
async def revoke_current_merchant_api_key(
    api_key_id: str,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke one of the authenticated merchant's API keys."""

    result = await session.execute(
        select(MerchantApiKey).where(
            MerchantApiKey.id == api_key_id,
            MerchantApiKey.merchant_id == merchant.id,
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key.revoked_at is None:
        current_time = datetime.now(timezone.utc)
        key_is_usable = (
            api_key.expires_at is None
            or _database_timestamp_as_utc(api_key.expires_at) > current_time
        )
        if key_is_usable:
            replacement_result = await session.execute(
                select(MerchantApiKey.id)
                .where(
                    MerchantApiKey.merchant_id == merchant.id,
                    MerchantApiKey.id != api_key.id,
                    MerchantApiKey.revoked_at.is_(None),
                    or_(
                        MerchantApiKey.expires_at.is_(None),
                        MerchantApiKey.expires_at > current_time,
                    ),
                )
                .limit(1)
            )
            if replacement_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot revoke the merchant's last active API key",
                )

        api_key.revoked_at = current_time
        await session.commit()

    return Response(status_code=204)


def _database_timestamp_as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/me/balance", response_model=MerchantBalanceResponse)
async def get_current_merchant_balance(
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Return USDC accumulated through internal micropayments."""

    account = await _find_merchant_ledger_account(session, merchant.id)
    reserved_result = await session.execute(
        select(func.coalesce(func.sum(Settlement.amount_atomic), 0)).where(
            Settlement.merchant_id == merchant.id,
            Settlement.status.in_(
                [
                    SettlementStatus.PENDING,
                    SettlementStatus.BROADCASTING,
                    SettlementStatus.SUBMITTED,
                    SettlementStatus.REVIEW_REQUIRED,
                ]
            ),
        )
    )
    settled_result = await session.execute(
        select(func.coalesce(func.sum(Settlement.amount_atomic), 0)).where(
            Settlement.merchant_id == merchant.id,
            Settlement.status == SettlementStatus.CONFIRMED,
        )
    )
    return MerchantBalanceResponse(
        merchant_id=merchant.id,
        available_balance=(account.balance if account is not None else 0),
        reserved_balance=atomic_to_usdc(int(reserved_result.scalar_one())),
        settled_balance=atomic_to_usdc(int(settled_result.scalar_one())),
        currency="USDC",
    )


@router.get("/me/activity", response_model=list[LedgerActivityResponse])
async def list_current_merchant_activity(
    limit: int = 50,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Return the merchant side of recent internal ledger transfers."""

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    account = await _find_merchant_ledger_account(session, merchant.id)
    if account is None:
        return []
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


async def _find_merchant_ledger_account(
    session: AsyncSession,
    merchant_id: str,
) -> LedgerAccount | None:
    result = await session.execute(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == LedgerOwnerType.MERCHANT,
            LedgerAccount.owner_id == merchant_id,
            LedgerAccount.currency == "USDC",
        )
    )
    return result.scalar_one_or_none()


@router.get(
    "/me/micropayments/{transaction_id}",
    response_model=MerchantMicropaymentResponse,
)
async def get_current_merchant_micropayment(
    transaction_id: str,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Verify that one internal micropayment belongs to this merchant."""

    account = await _find_merchant_ledger_account(session, merchant.id)
    if account is None:
        raise HTTPException(status_code=404, detail="Micropayment not found")
    result = await session.execute(
        select(LedgerTransaction)
        .join(
            LedgerEntry,
            LedgerEntry.transaction_id == LedgerTransaction.id,
        )
        .where(
            LedgerTransaction.id == transaction_id,
            LedgerTransaction.transaction_type
            == LedgerTransactionType.MICROPAYMENT,
            LedgerEntry.account_id == account.id,
            LedgerEntry.amount_atomic > 0,
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(status_code=404, detail="Micropayment not found")
    return MerchantMicropaymentResponse(
        id=transaction.id,
        transaction_type=transaction.transaction_type,
        amount=transaction.amount,
        currency="USDC",
        reference=transaction.reference_id,
        created_at=transaction.created_at,
    )


@router.post(
    "/me/settlements",
    response_model=SettlementCreatedResponse,
    status_code=201,
)
async def request_current_merchant_settlement(
    request: SettlementCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Reserve some or all available balance for one aggregate payout."""

    if settings.stablepay_vault_address is None:
        raise HTTPException(
            status_code=503,
            detail="STABLEPAY_VAULT_ADDRESS is not configured",
        )
    if merchant.wallet_address.lower() == settings.stablepay_vault_address.lower():
        raise HTTPException(
            status_code=409,
            detail="Merchant payout address must differ from the vault address",
        )
    merchant_id = merchant.id
    try:
        created = await create_settlement(
            session,
            merchant,
            amount_atomic=(
                None if request.amount is None else usdc_to_atomic(request.amount)
            ),
            minimum_amount_atomic=usdc_to_atomic(
                settings.settlement_minimum_amount
            ),
            idempotency_key=idempotency_key,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        merchant = await session.get(Merchant, merchant_id)
        if merchant is None:
            raise HTTPException(status_code=404, detail="Merchant not found")
        try:
            created = await create_settlement(
                session,
                merchant,
                amount_atomic=(
                    None
                    if request.amount is None
                    else usdc_to_atomic(request.amount)
                ),
                minimum_amount_atomic=usdc_to_atomic(
                    settings.settlement_minimum_amount
                ),
                idempotency_key=idempotency_key,
            )
            await session.commit()
        except (SettlementError, IntegrityError) as error:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
    except SettlementError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _settlement_response(created.settlement, created.replayed)


@router.get("/me/settlements", response_model=list[SettlementResponse])
async def list_current_merchant_settlements(
    status: SettlementStatus | None = None,
    limit: int = 50,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    filters = [Settlement.merchant_id == merchant.id]
    if status is not None:
        filters.append(Settlement.status == status)
    result = await session.execute(
        select(Settlement)
        .where(*filters)
        .order_by(Settlement.created_at.desc(), Settlement.id.desc())
        .limit(limit)
    )
    return [_settlement_response(settlement) for settlement in result.scalars()]


@router.get(
    "/me/settlements/{settlement_id}",
    response_model=SettlementResponse,
)
async def get_current_merchant_settlement(
    settlement_id: str,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Settlement).where(
            Settlement.id == settlement_id,
            Settlement.merchant_id == merchant.id,
        )
    )
    settlement = result.scalar_one_or_none()
    if settlement is None:
        raise HTTPException(status_code=404, detail="Settlement not found")
    return _settlement_response(settlement)


@router.post(
    "/me/settlements/{settlement_id}/cancel",
    response_model=SettlementResponse,
)
async def cancel_current_merchant_settlement(
    settlement_id: str,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    try:
        settlement = await cancel_settlement(
            session,
            settlement_id,
            merchant.id,
        )
        await session.commit()
    except SettlementError as error:
        await session.rollback()
        status_code = 404 if str(error) == "Settlement not found" else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    return _settlement_response(settlement)


def _settlement_response(
    settlement: Settlement,
    replayed: bool | None = None,
) -> SettlementResponse | SettlementCreatedResponse:
    values = {
        "id": settlement.id,
        "merchant_id": settlement.merchant_id,
        "amount": settlement.amount,
        "currency": settlement.currency,
        "chain": settlement.chain,
        "destination_address": settlement.destination_address,
        "status": settlement.status,
        "transaction_hash": settlement.transaction_hash,
        "failure_reason": settlement.failure_reason,
        "created_at": settlement.created_at,
        "broadcast_at": settlement.broadcast_at,
        "submitted_at": settlement.submitted_at,
        "confirmed_at": settlement.confirmed_at,
        "failed_at": settlement.failed_at,
        "cancelled_at": settlement.cancelled_at,
    }
    if replayed is not None:
        return SettlementCreatedResponse(**values, replayed=replayed)
    return SettlementResponse(**values)
