import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.merchants import router as merchants_router
from api.payments import router as payments_router
from api.vaults import router as vaults_router
from api.web import APP_DIRECTORY
from api.web import router as web_router
from workers.blockchain_monitor import run_blockchain_monitor_worker
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
    blockchain_monitor_task = asyncio.create_task(
        run_blockchain_monitor_worker(stop_event),
        name="blockchain-monitor-worker",
    )

    try:
        yield
    finally:
        stop_event.set()
        await asyncio.gather(
            expiration_task,
            webhook_task,
            blockchain_monitor_task,
        )


app = FastAPI(
    title="Stablecoin Payment Gateway",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=APP_DIRECTORY / "web" / "static"),
    name="static",
)
app.include_router(payments_router)
app.include_router(merchants_router)
app.include_router(vaults_router)
app.include_router(web_router)
