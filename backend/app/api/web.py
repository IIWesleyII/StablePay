"""Browser pages and the limited public checkout-status API."""

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_session
from database.models import Merchant
from database.models import Payment
from schemas.payments import CheckoutPaymentResponse


APP_DIRECTORY = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=APP_DIRECTORY / "web" / "templates")

router = APIRouter(tags=["checkout"])


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def merchant_dashboard(request: Request):
    """Serve the API-key-authenticated merchant dashboard shell."""

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
    )


@router.get(
    "/checkout/{payment_id}/status",
    response_model=CheckoutPaymentResponse,
)
async def get_checkout_payment_status(
    payment_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Return only the public details needed to complete one payment."""

    result = await session.execute(
        select(Payment, Merchant.name)
        .outerjoin(Merchant, Merchant.id == Payment.merchant_id)
        .where(Payment.id == payment_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    payment, merchant_name = row
    return {
        "id": payment.id,
        "merchant_name": merchant_name or "StablePay Merchant",
        "amount": payment.amount,
        "currency": payment.currency,
        "chain": payment.chain,
        "recipient_address": payment.recipient_address,
        "status": payment.status,
        "transaction_hash": payment.transaction_hash,
        "expires_at": payment.expires_at,
        "confirmed_at": payment.confirmed_at,
    }


@router.get(
    "/checkout/{payment_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def customer_checkout(
    request: Request,
    payment_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Serve checkout only when the requested payment exists."""

    payment_exists = await session.scalar(
        select(Payment.id).where(Payment.id == payment_id)
    )
    if payment_exists is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    return templates.TemplateResponse(
        request=request,
        name="checkout.html",
        context={"payment_id": payment_id},
    )
