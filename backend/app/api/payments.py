from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.database import get_session
from database.models import Payment
from domain.payments import PaymentStatus
from schemas.payments import PaymentCreate
from schemas.payments import PaymentResponse

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
)


@router.post("", response_model=PaymentResponse, status_code=201)
async def create_payment(
    payment_data: PaymentCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create and store a new pending USDC payment."""

    created_at = datetime.now(timezone.utc)

    payment = Payment(
        id=f"pay_{uuid4().hex}",
        amount=payment_data.amount,
        currency="USDC",
        chain="base-sepolia",
        recipient_address=settings.merchant_wallet_address,
        status=PaymentStatus.PENDING,
        created_at=created_at,
        expires_at=created_at
        + timedelta(minutes=settings.payment_expiration_minutes),
    )

    session.add(payment)

    await session.commit()
    await session.refresh(payment)

    return payment


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Return a payment by its payment ID."""

    result = await session.execute(
        select(Payment).where(Payment.id == payment_id)
    )

    payment = result.scalar_one_or_none()

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return payment
