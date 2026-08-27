import os
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_session
from database.models import Payment
from schemas.payments import PaymentCreate
from schemas.payments import PaymentResponse

load_dotenv()

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
)

MERCHANT_WALLET_ADDRESS = os.getenv("MERCHANT_WALLET_ADDRESS")


@router.post("", response_model=PaymentResponse, status_code=201)
async def create_payment(payment_data: PaymentCreate, session=Depends(get_session)):
    """Create and store a new pending USDC payment."""

    payment = Payment(
        id=f"pay_{uuid4().hex}",
        amount=payment_data.amount,
        currency="USDC",
        chain="base-sepolia",
        recipient_address=MERCHANT_WALLET_ADDRESS,
        status="pending",
    )

    session.add(payment)

    await session.commit()
    await session.refresh(payment)

    return payment


from fastapi import HTTPException
from sqlalchemy import select


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(payment_id: str, session=Depends(get_session)):
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