from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from services.api_keys import ApiKeyError
from services.api_keys import generate_merchant_api_key
from services.api_keys import parse_api_key_id
from services.api_keys import verify_api_key


MERCHANT_ID = "mch_test_merchant"


def test_generated_api_key_stores_only_a_hash():
    generated = generate_merchant_api_key(MERCHANT_ID, "Local development")

    assert generated.plaintext.startswith("sp_test.key_")
    assert generated.record.merchant_id == MERCHANT_ID
    assert generated.record.name == "Local development"
    assert generated.record.secret_hash != generated.plaintext
    assert generated.plaintext not in repr(generated)
    assert verify_api_key(
        generated.plaintext,
        generated.record.secret_hash,
    )


def test_two_generated_keys_are_different():
    first = generate_merchant_api_key(MERCHANT_ID, "First")
    second = generate_merchant_api_key(MERCHANT_ID, "Second")

    assert first.plaintext != second.plaintext
    assert first.record.secret_hash != second.record.secret_hash


def test_wrong_api_key_does_not_verify():
    generated = generate_merchant_api_key(MERCHANT_ID, "Local development")

    assert not verify_api_key(
        "sp_test.key_00000000000000000000000000000000.wrong",
        generated.record.secret_hash,
    )


def test_api_key_id_can_be_parsed_without_database_access():
    generated = generate_merchant_api_key(MERCHANT_ID, "Local development")

    assert parse_api_key_id(generated.plaintext) == generated.record.id


@pytest.mark.parametrize(
    "plaintext",
    [
        "",
        "wrong.key_value.secret",
        "sp_test.not-a-key.secret",
        "sp_test.key_00000000000000000000000000000000",
    ],
)
def test_invalid_api_key_format_is_rejected(plaintext: str):
    with pytest.raises(ApiKeyError, match="invalid format"):
        parse_api_key_id(plaintext)


def test_api_key_name_must_not_be_empty():
    with pytest.raises(ApiKeyError, match="name must not be empty"):
        generate_merchant_api_key(MERCHANT_ID, "   ")


def test_api_key_name_must_fit_database_column():
    with pytest.raises(ApiKeyError, match="must not exceed 100"):
        generate_merchant_api_key(MERCHANT_ID, "x" * 101)


def test_api_key_creation_time_must_include_timezone():
    with pytest.raises(ApiKeyError, match="must include a timezone"):
        generate_merchant_api_key(
            MERCHANT_ID,
            "Local development",
            created_at=datetime(2026, 9, 1, 12, 0),
        )


def test_api_key_can_have_a_future_expiration():
    creation_time = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    expiration_time = creation_time + timedelta(days=30)

    generated = generate_merchant_api_key(
        MERCHANT_ID,
        "Temporary",
        created_at=creation_time,
        expires_at=expiration_time,
    )

    assert generated.record.expires_at == expiration_time


def test_api_key_expiration_must_be_after_creation():
    creation_time = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ApiKeyError, match="expiration must be in the future"):
        generate_merchant_api_key(
            MERCHANT_ID,
            "Already expired",
            created_at=creation_time,
            expires_at=creation_time - timedelta(seconds=1),
        )


def test_api_key_expiration_must_include_timezone():
    with pytest.raises(ApiKeyError, match="must include a timezone"):
        generate_merchant_api_key(
            MERCHANT_ID,
            "Naive expiration",
            expires_at=datetime(2030, 1, 1, 12, 0),
        )
