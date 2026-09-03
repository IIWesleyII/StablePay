from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.authentication import get_authenticated_merchant
from blockchain.base import BaseSepoliaClient
from blockchain.base import BlockchainConnectionError
from blockchain.base import BlockchainTransactionError
from blockchain.base import TransactionNotMinedError
from blockchain.base import get_base_sepolia_client
from config import settings
from database.database import get_session
from database.models import Merchant
from database.models import Payment
from domain.payments import PaymentStatus
from schemas.payments import PaymentCreate
from schemas.payments import PaymentListResponse
from schemas.payments import PaymentResponse
from schemas.payments import PaymentVerificationRequest
from schemas.payments import PaymentVerificationResponse
from services.payment_lifecycle import PaymentLifecycleError
from services.payment_lifecycle import mark_payment_confirmed
from services.payment_lifecycle import mark_payment_detected
from services.payment_lifecycle import mark_payment_expired
from services.payment_verification import PaymentVerificationError
from services.payment_verification import find_matching_transfer
from services.webhook_events import WebhookEventError
from services.webhook_events import create_payment_confirmed_event
from services.webhook_events import get_payment_webhook_url

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
)


@router.post("", response_model=PaymentResponse, status_code=201)
async def create_payment(
    payment_data: PaymentCreate,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Create and store a new pending USDC payment."""

    created_at = datetime.now(timezone.utc)

    payment = Payment(
        id=f"pay_{uuid4().hex}",
        merchant_id=merchant.id,
        amount=payment_data.amount,
        currency="USDC",
        chain="base-sepolia",
        recipient_address=merchant.wallet_address,
        status=PaymentStatus.PENDING,
        created_at=created_at,
        expires_at=created_at
        + timedelta(minutes=settings.payment_expiration_minutes),
    )

    session.add(payment)

    await session.commit()
    await session.refresh(payment)

    return payment


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    status: PaymentStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Return one merchant's recent payments and lifecycle totals."""

    payment_filters = [Payment.merchant_id == merchant.id]
    if status is not None:
        payment_filters.append(Payment.status == status)

    payments_result = await session.execute(
        select(Payment)
        .where(*payment_filters)
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total_result = await session.execute(
        select(func.count())
        .select_from(Payment)
        .where(*payment_filters)
    )
    counts_result = await session.execute(
        select(Payment.status, func.count())
        .where(Payment.merchant_id == merchant.id)
        .group_by(Payment.status)
    )

    status_counts = {payment_status.value: 0 for payment_status in PaymentStatus}
    for payment_status, count in counts_result:
        status_counts[payment_status.value] = count

    return {
        "items": list(payments_result.scalars()),
        "total": total_result.scalar_one(),
        "limit": limit,
        "offset": offset,
        "status_counts": status_counts,
    }


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: str,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Return a payment by its payment ID."""

    result = await session.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.merchant_id == merchant.id,
        )
    )

    payment = result.scalar_one_or_none()

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return payment


@router.post(
    "/{payment_id}/verify",
    response_model=PaymentVerificationResponse,
)
async def verify_payment(
    payment_id: str,
    verification_data: PaymentVerificationRequest,
    session: AsyncSession = Depends(get_session),
    blockchain_client: BaseSepoliaClient = Depends(get_base_sepolia_client),
):
    """Verify and attach a Base Sepolia USDC transaction to a payment."""

    payment = await _get_payment_or_404(session, payment_id)
    transaction_hash = verification_data.transaction_hash
    current_time = datetime.now(timezone.utc)

    if payment.status == PaymentStatus.EXPIRED:
        raise HTTPException(status_code=409, detail="Payment has expired")

    expiration_time = _database_timestamp_as_utc(payment.expires_at)

    if payment.status == PaymentStatus.PENDING and current_time >= expiration_time:
        payment.expires_at = expiration_time
        mark_payment_expired(payment, current_time)
        await session.commit()
        raise HTTPException(status_code=409, detail="Payment has expired")

    if (
        payment.transaction_hash is not None
        and payment.transaction_hash != transaction_hash
    ):
        raise HTTPException(
            status_code=409,
            detail="Payment is already linked to another transaction",
        )

    duplicate_result = await session.execute(
        select(Payment.id).where(
            Payment.transaction_hash == transaction_hash,
            Payment.id != payment.id,
        )
    )
    if duplicate_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Transaction is already linked to another payment",
        )

    try:
        transfers = await blockchain_client.get_usdc_transfers(transaction_hash)
        matching_transfer = find_matching_transfer(payment, transfers)
    except TransactionNotMinedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except BlockchainConnectionError as error:
        raise HTTPException(
            status_code=502,
            detail="Unable to verify transaction with Base Sepolia",
        ) from error
    except (BlockchainTransactionError, PaymentVerificationError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        became_confirmed = False

        payment.payer_address = matching_transfer.sender
        payment.transaction_block_number = matching_transfer.block_number
        payment.transaction_log_index = matching_transfer.log_index

        if payment.status == PaymentStatus.PENDING:
            mark_payment_detected(payment, transaction_hash, current_time)

        if (
            payment.status == PaymentStatus.CONFIRMING
            and matching_transfer.confirmations
            >= settings.payment_required_confirmations
        ):
            if payment.detected_at is not None:
                payment.detected_at = _database_timestamp_as_utc(
                    payment.detected_at
                )
            mark_payment_confirmed(payment, current_time)
            became_confirmed = True

        if became_confirmed:
            webhook_url = await get_payment_webhook_url(payment, session)
            session.add(
                create_payment_confirmed_event(
                    payment,
                    webhook_url,
                    current_time,
                )
            )

        await session.commit()
        await session.refresh(payment)
    except (PaymentLifecycleError, WebhookEventError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Transaction is already linked to another payment",
        ) from error

    return {
        "payment": payment,
        "sender_address": matching_transfer.sender,
        "confirmations": matching_transfer.confirmations,
        "required_confirmations": settings.payment_required_confirmations,
    }


async def _get_payment_or_404(
    session: AsyncSession,
    payment_id: str,
) -> Payment:
    result = await session.execute(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    payment = result.scalar_one_or_none()

    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    return payment


def _database_timestamp_as_utc(value: datetime) -> datetime:
    """Normalize timestamps loaded from databases that omit timezone metadata."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
