from dataclasses import dataclass
from types import TracebackType
from typing import Any

from web3 import AsyncHTTPProvider
from web3 import AsyncWeb3
from web3 import Web3

from config import settings


BASE_SEPOLIA_CHAIN_ID = 84532
USDC_DECIMALS = 6
USDC_SYMBOL = "USDC"

ERC20_METADATA_ABI = [
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class BlockchainConnectionError(RuntimeError):
    """Raised when StablePay cannot read required blockchain data."""


class WrongNetworkError(BlockchainConnectionError):
    """Raised when an RPC endpoint is connected to the wrong blockchain."""


class ContractNotFoundError(BlockchainConnectionError):
    """Raised when the configured token address has no deployed bytecode."""


class UnexpectedTokenError(BlockchainConnectionError):
    """Raised when the configured contract does not identify itself as USDC."""


@dataclass(frozen=True)
class BaseSepoliaStatus:
    chain_id: int
    latest_block: int
    usdc_address: str
    usdc_code_size: int
    usdc_symbol: str
    usdc_decimals: int


class BaseSepoliaClient:
    """Read-only access to the Base Sepolia network."""

    def __init__(self, rpc_url: str, usdc_address: str, web3_client):
        self._web3 = web3_client or AsyncWeb3(
            AsyncHTTPProvider(
                rpc_url,
                request_kwargs={"timeout": 10},
            )
        )
        self._usdc_address = Web3.to_checksum_address(usdc_address)

    @classmethod
    def from_settings(cls) -> "BaseSepoliaClient":
        return cls(
            rpc_url=settings.base_sepolia_rpc_url,
            usdc_address=settings.base_sepolia_usdc_address,
        )

    async def check_connection(self) -> BaseSepoliaStatus:
        """Validate the network and configured USDC contract."""

        try:
            chain_id = await self._web3.eth.chain_id
        except Exception as error:
            raise BlockchainConnectionError(
                "Unable to read the Base Sepolia chain ID"
            ) from error

        if chain_id != BASE_SEPOLIA_CHAIN_ID:
            raise WrongNetworkError(
                f"Expected Base Sepolia chain ID {BASE_SEPOLIA_CHAIN_ID}, "
                f"but RPC returned {chain_id}"
            )

        try:
            latest_block = await self._web3.eth.block_number
            contract_code = await self._web3.eth.get_code(self._usdc_address)
        except Exception as error:
            raise BlockchainConnectionError(
                "Unable to read Base Sepolia block or contract data"
            ) from error

        if len(contract_code) == 0:
            raise ContractNotFoundError(
                "Configured Base Sepolia USDC address has no deployed contract"
            )

        try:
            contract = self._web3.eth.contract(
                address=self._usdc_address,
                abi=ERC20_METADATA_ABI,
            )
            token_symbol = await contract.functions.symbol().call()
            token_decimals = await contract.functions.decimals().call()
        except Exception as error:
            raise BlockchainConnectionError(
                "Unable to read configured Base Sepolia token metadata"
            ) from error

        if token_symbol != USDC_SYMBOL or token_decimals != USDC_DECIMALS:
            raise UnexpectedTokenError(
                f"Expected {USDC_SYMBOL} with {USDC_DECIMALS} decimals, "
                f"but contract returned {token_symbol} with {token_decimals} decimals"
            )

        return BaseSepoliaStatus(
            chain_id=chain_id,
            latest_block=latest_block,
            usdc_address=self._usdc_address,
            usdc_code_size=len(contract_code),
            usdc_symbol=token_symbol,
            usdc_decimals=token_decimals,
        )

    async def close(self) -> None:
        await self._web3.provider.disconnect()

    async def __aenter__(self) -> "BaseSepoliaClient":
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
