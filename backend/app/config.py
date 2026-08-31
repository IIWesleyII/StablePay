import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic import field_validator
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
