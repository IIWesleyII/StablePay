import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.payments import router as payments_router
from workers.payment_expiration import run_payment_expiration_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop application background workers."""

    stop_event = asyncio.Event()
    expiration_task = asyncio.create_task(
        run_payment_expiration_worker(stop_event),
        name="payment-expiration-worker",
    )

    try:
        yield
    finally:
        stop_event.set()
        await expiration_task


app = FastAPI(
    title="Stablecoin Payment Gateway",
    lifespan=lifespan,
)

app.include_router(payments_router)
