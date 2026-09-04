from types import SimpleNamespace

import pytest
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound

from backend.run_settlements import broadcast_or_find_existing
from backend.send_test_payment import PaymentScriptError


RAW_TRANSACTION = HexBytes("0x" + "12" * 40)
TRANSACTION_HASH = Web3.to_hex(Web3.keccak(RAW_TRANSACTION)).lower()


class FakeEth:
    def __init__(self, *, send_error=None, transaction_exists=False):
        self.send_error = send_error
        self.transaction_exists = transaction_exists
        self.sent = 0

    def send_raw_transaction(self, raw_transaction):
        self.sent += 1
        if self.send_error is not None:
            raise self.send_error
        return HexBytes(TRANSACTION_HASH)

    def get_transaction(self, transaction_hash):
        if not self.transaction_exists:
            raise TransactionNotFound(transaction_hash)
        return {"hash": transaction_hash}


def test_broadcast_accepts_expected_transaction_hash():
    eth = FakeEth()

    broadcast_or_find_existing(SimpleNamespace(eth=eth), RAW_TRANSACTION, TRANSACTION_HASH)

    assert eth.sent == 1


def test_rebroadcast_accepts_transaction_already_known_to_rpc():
    eth = FakeEth(send_error=ValueError("already known"), transaction_exists=True)

    broadcast_or_find_existing(SimpleNamespace(eth=eth), RAW_TRANSACTION, TRANSACTION_HASH)

    assert eth.sent == 1


def test_broadcast_rejects_unknown_failed_transaction():
    eth = FakeEth(send_error=ValueError("connection lost"))

    with pytest.raises(PaymentScriptError, match="reservation remains safe"):
        broadcast_or_find_existing(
            SimpleNamespace(eth=eth), RAW_TRANSACTION, TRANSACTION_HASH
        )


def test_broadcast_never_sends_payload_with_mismatched_hash():
    eth = FakeEth()

    with pytest.raises(PaymentScriptError, match="does not match"):
        broadcast_or_find_existing(
            SimpleNamespace(eth=eth), RAW_TRANSACTION, "0x" + "ab" * 32
        )

    assert eth.sent == 0
