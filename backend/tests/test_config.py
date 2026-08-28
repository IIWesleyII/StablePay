import pytest
from pydantic import ValidationError

from config import Settings


def test_payment_expiration_must_be_positive():
    with pytest.raises(ValidationError, match="payment_expiration_minutes"):
        Settings(
            database_url="postgresql+asyncpg://user:password@localhost/stablepay",
            merchant_wallet_address="0x1111111111111111111111111111111111111111",
            payment_expiration_minutes=0,
            _env_file=None,
        )
