import pytest
from pydantic import ValidationError

from config import Settings


def test_payment_expiration_must_be_positive():
    with pytest.raises(ValidationError, match="payment_expiration_minutes"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            payment_expiration_minutes=0,
            _env_file=None,
        )


def test_payment_expiration_poll_interval_must_be_positive():
    with pytest.raises(ValidationError, match="payment_expiration_poll_seconds"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            payment_expiration_poll_seconds=0,
            _env_file=None,
        )


def test_base_sepolia_rpc_url_must_be_http():
    with pytest.raises(ValidationError, match="BASE_SEPOLIA_RPC_URL"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="not-a-url",
            _env_file=None,
        )


def test_base_sepolia_usdc_address_must_be_valid():
    with pytest.raises(ValidationError, match="BASE_SEPOLIA_USDC_ADDRESS"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            base_sepolia_usdc_address="not-an-address",
            _env_file=None,
        )


def test_payment_required_confirmations_must_be_positive():
    with pytest.raises(ValidationError, match="payment_required_confirmations"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            payment_required_confirmations=0,
            _env_file=None,
        )
