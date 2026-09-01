from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest

from backend.send_test_payment import PaymentScriptError
from backend.send_test_payment import load_test_account
from backend.send_test_payment import validate_payment


RECIPIENT_ADDRESS = "0x2222222222222222222222222222222222222222"


def make_payment(**changes):
    payment = {
        "status": "pending",
        "currency": "USDC",
        "chain": "base-sepolia",
        "amount": "0.01",
        "recipient_address": RECIPIENT_ADDRESS,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    payment.update(changes)
    return payment


def test_valid_payment_returns_exact_amount_and_recipient():
    amount, recipient = validate_payment(make_payment())

    assert amount == Decimal("0.01")
    assert recipient == RECIPIENT_ADDRESS


def test_non_pending_payment_is_rejected_to_prevent_duplicate_send():
    with pytest.raises(PaymentScriptError, match="sending the payment twice"):
        validate_payment(make_payment(status="confirmed"))


def test_expired_payment_is_rejected():
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    with pytest.raises(PaymentScriptError, match="already expired"):
        validate_payment(make_payment(expires_at=expired_at))


def test_amount_with_more_than_six_decimals_is_rejected():
    with pytest.raises(PaymentScriptError, match="more than six decimal places"):
        validate_payment(make_payment(amount="0.0000001"))


def test_account_can_be_loaded_from_named_environment_variable(monkeypatch):
    private_key = "11" * 32
    monkeypatch.setenv("STABLEPAY_TEST_KEY", private_key)

    account = load_test_account("STABLEPAY_TEST_KEY")

    assert account.address.startswith("0x")


def test_missing_private_key_environment_variable_is_rejected(monkeypatch):
    monkeypatch.delenv("STABLEPAY_TEST_KEY", raising=False)

    with pytest.raises(PaymentScriptError, match="is not configured"):
        load_test_account("STABLEPAY_TEST_KEY")
