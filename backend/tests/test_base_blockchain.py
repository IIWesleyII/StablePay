from types import SimpleNamespace

import pytest

from blockchain.base import BASE_SEPOLIA_CHAIN_ID
from blockchain.base import BaseSepoliaClient
from blockchain.base import BlockchainConnectionError
from blockchain.base import ContractNotFoundError
from blockchain.base import UnexpectedTokenError
from blockchain.base import WrongNetworkError


USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


class FakeEth:
    def __init__(
        self,
        chain_id: int = BASE_SEPOLIA_CHAIN_ID,
        block_number: int = 123456,
        contract_code: bytes = b"\x60\x80",
        token_symbol: str = "USDC",
        token_decimals: int = 6,
        chain_id_error: Exception | None = None,
    ) -> None:
        self._chain_id = chain_id
        self._block_number = block_number
        self._contract_code = contract_code
        self._token_symbol = token_symbol
        self._token_decimals = token_decimals
        self._chain_id_error = chain_id_error

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
