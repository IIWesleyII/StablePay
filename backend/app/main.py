from fastapi import FastAPI

from api.payments import router as payments_router


app = FastAPI(
    title="Stablecoin Payment Gateway",
)

app.include_router(payments_router)
