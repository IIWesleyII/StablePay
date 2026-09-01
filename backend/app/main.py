import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.payments import router as payments_router
from workers.payment_expiration import run_payment_expiration_worker
from workers.webhook_delivery import run_webhook_delivery_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop application background workers."""

    stop_event = asyncio.Event()
    expiration_task = asyncio.create_task(
        run_payment_expiration_worker(stop_event),
        name="payment-expiration-worker",
    )
    webhook_task = asyncio.create_task(
        run_webhook_delivery_worker(stop_event),
        name="webhook-delivery-worker",
    )

    try:
        yield
    finally:
        stop_event.set()
        await asyncio.gather(expiration_task, webhook_task)


app = FastAPI(
    title="Stablecoin Payment Gateway",
    lifespan=lifespan,
)

app.include_router(payments_router)
