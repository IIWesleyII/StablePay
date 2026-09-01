import re
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    merchant_wallet_address: str
    base_sepolia_rpc_url: str
    base_sepolia_usdc_address: str = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    payment_expiration_minutes: int = Field(default=15, gt=0)
    payment_expiration_poll_seconds: int = Field(default=30, gt=0)
    payment_required_confirmations: int = Field(default=3, gt=0)
    merchant_webhook_url: str = "http://127.0.0.1:9000/webhooks/stablepay"
    merchant_webhook_secret: str | None = None
    webhook_delivery_poll_seconds: int = Field(default=2, gt=0)
    webhook_delivery_timeout_seconds: int = Field(default=10, gt=0)
    webhook_delivery_batch_size: int = Field(default=20, gt=0)
    webhook_delivery_lease_seconds: int = Field(default=30, gt=0)
    webhook_delivery_max_attempts: int = Field(default=5, gt=0)
    webhook_delivery_retry_seconds: int = Field(default=5, gt=0)

    @model_validator(mode="after")
    def validate_webhook_delivery_lease(self) -> Self:
        if (
            self.webhook_delivery_lease_seconds
            <= self.webhook_delivery_timeout_seconds
        ):
            raise ValueError(
                "WEBHOOK_DELIVERY_LEASE_SECONDS must be greater than "
                "WEBHOOK_DELIVERY_TIMEOUT_SECONDS"
            )

        return self

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the postgresql+asyncpg:// scheme"
            )

        return value

    @field_validator("merchant_wallet_address")
    @classmethod
    def validate_merchant_wallet_address(cls, value: str) -> str:
        if re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
            raise ValueError(
                "MERCHANT_WALLET_ADDRESS must be a 42-character Ethereum address"
            )

        if value.lower() == "0x0000000000000000000000000000000000000000":
            raise ValueError("MERCHANT_WALLET_ADDRESS must not be the zero address")

        return value

    @field_validator("base_sepolia_rpc_url")
    @classmethod
    def validate_base_sepolia_rpc_url(cls, value: str) -> str:
        parsed_url = urlparse(value)

        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("BASE_SEPOLIA_RPC_URL must be a valid HTTP(S) URL")

        return value

    @field_validator("merchant_webhook_url")
    @classmethod
    def validate_merchant_webhook_url(cls, value: str) -> str:
        parsed_url = urlparse(value)

        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("MERCHANT_WEBHOOK_URL must be a valid HTTP(S) URL")

        return value

    @field_validator("merchant_webhook_secret")
    @classmethod
    def validate_merchant_webhook_secret(cls, value: str | None) -> str | None:
        if value is not None and len(value) < 32:
            raise ValueError(
                "MERCHANT_WEBHOOK_SECRET must contain at least 32 characters"
            )

        return value

    @field_validator("base_sepolia_usdc_address")
    @classmethod
    def validate_base_sepolia_usdc_address(cls, value: str) -> str:
        if re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
            raise ValueError(
                "BASE_SEPOLIA_USDC_ADDRESS must be a 42-character Ethereum address"
            )

        if value.lower() == "0x0000000000000000000000000000000000000000":
            raise ValueError("BASE_SEPOLIA_USDC_ADDRESS must not be the zero address")

        return value


settings = Settings()
