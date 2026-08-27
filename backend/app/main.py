from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.payments import router as payments_router
from database.database import Base
from database.database import engine
from database import models


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield


app = FastAPI(
    title="Stablecoin Payment Gateway",
    lifespan=lifespan,
)

app.include_router(payments_router)