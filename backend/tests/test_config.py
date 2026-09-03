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


def test_stablepay_vault_address_must_be_valid_when_configured():
    with pytest.raises(ValidationError, match="STABLEPAY_VAULT_ADDRESS"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            stablepay_vault_address="not-an-address",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "setting_name",
    ["vault_challenge_expiration_minutes", "vault_deposit_expiration_minutes"],
)
def test_vault_expiration_settings_must_be_positive(setting_name: str):
    with pytest.raises(ValidationError, match=setting_name):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            _env_file=None,
            **{setting_name: 0},
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


@pytest.mark.parametrize(
    "setting_name",
    [
        "blockchain_monitor_poll_seconds",
        "blockchain_monitor_block_batch_size",
        "blockchain_monitor_initial_lookback_blocks",
        "blockchain_monitor_confirmation_batch_size",
    ],
)
def test_blockchain_monitor_settings_must_be_positive(setting_name: str):
    with pytest.raises(ValidationError, match=setting_name):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            _env_file=None,
            **{setting_name: 0},
        )


def test_merchant_webhook_url_must_be_http():
    with pytest.raises(ValidationError, match="MERCHANT_WEBHOOK_URL"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            merchant_webhook_url="not-a-url",
            _env_file=None,
        )


def test_merchant_webhook_secret_must_be_long_enough():
    with pytest.raises(ValidationError, match="MERCHANT_WEBHOOK_SECRET"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            merchant_webhook_secret="too-short",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "setting_name",
    [
        "webhook_delivery_poll_seconds",
        "webhook_delivery_timeout_seconds",
        "webhook_delivery_batch_size",
        "webhook_delivery_lease_seconds",
        "webhook_delivery_max_attempts",
        "webhook_delivery_retry_seconds",
    ],
)
def test_webhook_worker_settings_must_be_positive(setting_name: str):
    with pytest.raises(ValidationError, match=setting_name):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            _env_file=None,
            **{setting_name: 0},
        )


def test_webhook_lease_must_outlast_request_timeout():
    with pytest.raises(
        ValidationError,
        match="WEBHOOK_DELIVERY_LEASE_SECONDS",
    ):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            base_sepolia_rpc_url="https://sepolia.base.org",
            webhook_delivery_timeout_seconds=30,
            webhook_delivery_lease_seconds=30,
            _env_file=None,
        )
