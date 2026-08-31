from decimal import Decimal
from types import SimpleNamespace

import pytest
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound

from blockchain.base import BASE_SEPOLIA_CHAIN_ID
from blockchain.base import BaseSepoliaClient
from blockchain.base import BlockchainConnectionError
from blockchain.base import BlockchainTransactionError
from blockchain.base import InvalidTransactionHashError
from blockchain.base import ContractNotFoundError
from blockchain.base import TransactionFailedError
from blockchain.base import TransactionNotMinedError
from blockchain.base import UnexpectedTokenError
from blockchain.base import WrongNetworkError


USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
SENDER_ADDRESS = "0x1111111111111111111111111111111111111111"
RECIPIENT_ADDRESS = "0x2222222222222222222222222222222222222222"
TRANSACTION_HASH = "0x" + "ab" * 32


class FakeEth:
    def __init__(
        self,
        chain_id: int = BASE_SEPOLIA_CHAIN_ID,
        block_number: int = 123456,
        contract_code: bytes = b"\x60\x80",
        token_symbol: str = "USDC",
        token_decimals: int = 6,
        chain_id_error: Exception | None = None,
        receipt: dict | None = None,
        receipt_error: Exception | None = None,
    ) -> None:
        self._chain_id = chain_id
        self._block_number = block_number
        self._contract_code = contract_code
        self._token_symbol = token_symbol
        self._token_decimals = token_decimals
        self._chain_id_error = chain_id_error
        self._receipt = receipt
        self._receipt_error = receipt_error

    @property
    def chain_id(self):
        async def get_chain_id():
            if self._chain_id_error is not None:
                raise self._chain_id_error
            return self._chain_id

        return get_chain_id()

    @property
    def block_number(self):
        async def get_block_number():
            return self._block_number

        return get_block_number()

    async def get_code(self, address: str) -> bytes:
        return self._contract_code

    def contract(self, address: str, abi: list[dict]):
        return FakeContract(self._token_symbol, self._token_decimals)

    async def get_transaction_receipt(self, transaction_hash: str):
        if self._receipt_error is not None:
            raise self._receipt_error
        if self._receipt is None:
            raise TransactionNotFound(transaction_hash)
        return self._receipt


class FakeContractCall:
    def __init__(self, value):
        self._value = value

    async def call(self):
        return self._value


class FakeContractFunctions:
    def __init__(self, symbol: str, decimals: int) -> None:
        self._symbol = symbol
        self._decimals = decimals

    def symbol(self) -> FakeContractCall:
        return FakeContractCall(self._symbol)

    def decimals(self) -> FakeContractCall:
        return FakeContractCall(self._decimals)


class FakeContract:
    def __init__(self, symbol: str, decimals: int) -> None:
        self.functions = FakeContractFunctions(symbol, decimals)


class FakeProvider:
    def __init__(self) -> None:
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


def make_web3(fake_eth: FakeEth):
    return SimpleNamespace(eth=fake_eth, provider=FakeProvider())


def address_topic(address: str) -> HexBytes:
    return HexBytes("0x" + "00" * 12 + address[2:])


def make_transfer_log(
    token_address: str = USDC_ADDRESS,
    amount: int = 10_000,
    log_index: int = 0,
) -> dict:
    return {
        "address": token_address,
        "topics": [
            Web3.keccak(text="Transfer(address,address,uint256)"),
            address_topic(SENDER_ADDRESS),
            address_topic(RECIPIENT_ADDRESS),
        ],
        "data": HexBytes(amount.to_bytes(32, byteorder="big")),
        "logIndex": log_index,
    }


def make_receipt(
    logs: list[dict] | None = None,
    status: int = 1,
    block_number: int = 123450,
    transaction_hash: str = TRANSACTION_HASH,
) -> dict:
    return {
        "transactionHash": HexBytes(transaction_hash),
        "status": status,
        "blockNumber": block_number,
        "logs": logs or [],
    }


@pytest.mark.asyncio
async def test_connection_returns_validated_network_status():
    web3_client = make_web3(FakeEth())
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    status = await client.check_connection()

    assert status.chain_id == BASE_SEPOLIA_CHAIN_ID
    assert status.latest_block == 123456
    assert status.usdc_address == USDC_ADDRESS
    assert status.usdc_code_size == 2
    assert status.usdc_symbol == "USDC"
    assert status.usdc_decimals == 6


@pytest.mark.asyncio
async def test_connection_rejects_wrong_network():
    web3_client = make_web3(FakeEth(chain_id=1))
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(WrongNetworkError, match="RPC returned 1"):
        await client.check_connection()


@pytest.mark.asyncio
async def test_connection_rejects_address_without_contract():
    web3_client = make_web3(FakeEth(contract_code=b""))
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(ContractNotFoundError, match="no deployed contract"):
        await client.check_connection()


@pytest.mark.asyncio
async def test_rpc_error_is_wrapped_without_exposing_url():
    web3_client = make_web3(FakeEth(chain_id_error=OSError("provider failed")))
    secret_rpc_url = "https://rpc.example/private-api-key"
    client = BaseSepoliaClient(secret_rpc_url, USDC_ADDRESS, web3_client)

    with pytest.raises(BlockchainConnectionError) as error:
        await client.check_connection()

    assert "private-api-key" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symbol", "decimals"),
    [("FAKE", 6), ("USDC", 18)],
)
async def test_connection_rejects_unexpected_token_metadata(
    symbol: str,
    decimals: int,
):
    web3_client = make_web3(
        FakeEth(token_symbol=symbol, token_decimals=decimals)
    )
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(UnexpectedTokenError, match="Expected USDC with 6 decimals"):
        await client.check_connection()


@pytest.mark.asyncio
async def test_client_disconnects_provider():
    web3_client = make_web3(FakeEth())
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    await client.close()

    assert web3_client.provider.disconnected is True


def test_client_can_be_created_from_settings():
    client = BaseSepoliaClient.from_settings()

    assert client is not None


@pytest.mark.asyncio
async def test_receipt_parser_returns_usdc_transfers():
    other_contract_log = make_transfer_log(
        token_address="0x3333333333333333333333333333333333333333",
    )
    usdc_log = make_transfer_log()
    web3_client = make_web3(
        FakeEth(receipt=make_receipt([other_contract_log, usdc_log]))
    )
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    transfers = await client.get_usdc_transfers(TRANSACTION_HASH)

    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.transaction_hash == TRANSACTION_HASH
    assert transfer.sender == SENDER_ADDRESS
    assert transfer.recipient == RECIPIENT_ADDRESS
    assert transfer.raw_amount == 10_000
    assert transfer.amount == Decimal("0.01")
    assert transfer.block_number == 123450
    assert transfer.confirmations == 7


@pytest.mark.asyncio
async def test_receipt_parser_returns_empty_list_without_usdc_transfer():
    unrelated_log = make_transfer_log(
        token_address="0x3333333333333333333333333333333333333333",
    )
    web3_client = make_web3(FakeEth(receipt=make_receipt([unrelated_log])))
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    transfers = await client.get_usdc_transfers(TRANSACTION_HASH)

    assert transfers == []


@pytest.mark.asyncio
async def test_receipt_parser_rejects_unmined_transaction():
    web3_client = make_web3(
        FakeEth(receipt_error=TransactionNotFound(TRANSACTION_HASH))
    )
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(TransactionNotMinedError, match="not been mined"):
        await client.get_usdc_transfers(TRANSACTION_HASH)


@pytest.mark.asyncio
async def test_receipt_parser_rejects_failed_transaction():
    web3_client = make_web3(FakeEth(receipt=make_receipt(status=0)))
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(TransactionFailedError, match="reverted"):
        await client.get_usdc_transfers(TRANSACTION_HASH)


@pytest.mark.asyncio
async def test_receipt_parser_rejects_invalid_hash_before_rpc_call():
    web3_client = make_web3(FakeEth())
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(InvalidTransactionHashError, match="64 hexadecimal"):
        await client.get_usdc_transfers("not-a-hash")


@pytest.mark.asyncio
async def test_receipt_parser_rejects_mismatched_receipt_hash():
    different_hash = "0x" + "cd" * 32
    web3_client = make_web3(
        FakeEth(receipt=make_receipt(transaction_hash=different_hash))
    )
    client = BaseSepoliaClient("https://rpc.example", USDC_ADDRESS, web3_client)

    with pytest.raises(BlockchainTransactionError, match="does not match"):
        await client.get_usdc_transfers(TRANSACTION_HASH)
