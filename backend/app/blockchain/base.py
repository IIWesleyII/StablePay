import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from types import TracebackType
from typing import Any

from web3 import AsyncHTTPProvider
from web3 import AsyncWeb3
from web3 import Web3
from web3.exceptions import TransactionNotFound

from config import settings


BASE_SEPOLIA_CHAIN_ID = 84532
USDC_DECIMALS = 6
USDC_SYMBOL = "USDC"
TRANSACTION_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
TRANSFER_EVENT_TOPIC = Web3.to_hex(
    Web3.keccak(text="Transfer(address,address,uint256)")
).lower()

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


class BlockchainTransactionError(RuntimeError):
    """Raised when a blockchain transaction cannot be safely processed."""


class InvalidTransactionHashError(BlockchainTransactionError):
    """Raised when a transaction hash has the wrong format."""


class TransactionNotMinedError(BlockchainTransactionError):
    """Raised when a transaction has no mined receipt yet."""


class TransactionFailedError(BlockchainTransactionError):
    """Raised when a mined transaction reverted."""


@dataclass(frozen=True)
class BaseSepoliaStatus:
    chain_id: int
    latest_block: int
    usdc_address: str
    usdc_code_size: int
    usdc_symbol: str
    usdc_decimals: int


@dataclass(frozen=True)
class UsdcTransfer:
    transaction_hash: str
    log_index: int
    sender: str
    recipient: str
    raw_amount: int
    amount: Decimal
    block_number: int
    confirmations: int
    block_hash: str | None = None
    block_timestamp: datetime | None = None


class BaseSepoliaClient:
    """Read-only access to the Base Sepolia network."""

    def __init__(
        self,
        rpc_url: str,
        usdc_address: str,
        web3_client: Any | None = None,
    ) -> None:
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

        chain_id = await self._require_base_sepolia()

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

    async def get_usdc_transfers(self, transaction_hash: str) -> list[UsdcTransfer]:
        """Return every USDC Transfer event emitted by a mined transaction."""

        if TRANSACTION_HASH_PATTERN.fullmatch(transaction_hash) is None:
            raise InvalidTransactionHashError(
                "Transaction hash must start with 0x followed by 64 hexadecimal characters"
            )

        await self._require_base_sepolia()
        normalized_hash = transaction_hash.lower()

        try:
            receipt = await self._web3.eth.get_transaction_receipt(normalized_hash)
        except TransactionNotFound as error:
            raise TransactionNotMinedError(
                "Transaction has not been mined or does not exist"
            ) from error
        except Exception as error:
            raise BlockchainConnectionError(
                "Unable to read the Base Sepolia transaction receipt"
            ) from error

        if receipt["status"] != 1:
            raise TransactionFailedError("Transaction reverted and did not succeed")

        block_number = receipt["blockNumber"]
        if block_number is None:
            raise TransactionNotMinedError("Transaction has not been mined yet")

        try:
            latest_block = await self._web3.eth.block_number
        except Exception as error:
            raise BlockchainConnectionError(
                "Unable to calculate transaction confirmations"
            ) from error

        confirmations = max(latest_block - block_number + 1, 0)
        receipt_hash = Web3.to_hex(receipt["transactionHash"]).lower()

        if receipt_hash != normalized_hash:
            raise BlockchainTransactionError(
                "Transaction receipt hash does not match the requested transaction"
            )

        transfers = []

        for log in receipt["logs"]:
            if log["address"].lower() != self._usdc_address.lower():
                continue

            topics = log["topics"]
            if len(topics) < 3 or Web3.to_hex(topics[0]).lower() != TRANSFER_EVENT_TOPIC:
                continue

            try:
                sender = _address_from_topic(topics[1])
                recipient = _address_from_topic(topics[2])
                raw_amount = int(Web3.to_hex(log["data"]), 16)
            except (TypeError, ValueError) as error:
                raise BlockchainTransactionError(
                    "USDC Transfer event contains invalid encoded data"
                ) from error

            transfers.append(
                UsdcTransfer(
                    transaction_hash=receipt_hash,
                    log_index=log["logIndex"],
                    sender=sender,
                    recipient=recipient,
                    raw_amount=raw_amount,
                    amount=Decimal(raw_amount) / Decimal(10**USDC_DECIMALS),
                    block_number=block_number,
                    confirmations=confirmations,
                )
            )

        return transfers

    async def get_latest_block_number(self) -> int:
        """Return the current Base Sepolia head after validating the network."""

        await self._require_base_sepolia()
        try:
            return await self._web3.eth.block_number
        except Exception as error:
            raise BlockchainConnectionError(
                "Unable to read the latest Base Sepolia block"
            ) from error

    async def get_block_hash(self, block_number: int) -> str:
        """Return one canonical block hash for durable cursor tracking."""

        if block_number < 0:
            raise BlockchainTransactionError("Block number cannot be negative")
        await self._require_base_sepolia()
        try:
            block = await self._web3.eth.get_block(block_number)
            return Web3.to_hex(block["hash"]).lower()
        except Exception as error:
            raise BlockchainConnectionError(
                f"Unable to read Base Sepolia block {block_number}"
            ) from error

    async def get_usdc_transfer_logs(
        self,
        from_block: int,
        to_block: int,
        recipient_addresses: list[str],
        latest_block: int | None = None,
    ) -> list[UsdcTransfer]:
        """Read USDC transfers to selected recipients across a block range."""

        if from_block < 0 or to_block < from_block:
            raise BlockchainTransactionError("Blockchain scan range is invalid")
        if not recipient_addresses:
            return []

        try:
            normalized_recipients = {
                Web3.to_checksum_address(address) for address in recipient_addresses
            }
        except (TypeError, ValueError) as error:
            raise BlockchainTransactionError(
                "Blockchain scan contains an invalid recipient address"
            ) from error

        await self._require_base_sepolia()
        recipient_topics = [
            "0x" + "0" * 24 + address[2:].lower()
            for address in sorted(normalized_recipients)
        ]

        try:
            logs = await self._web3.eth.get_logs(
                {
                    "address": self._usdc_address,
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "topics": [TRANSFER_EVENT_TOPIC, None, recipient_topics],
                }
            )
            chain_head = (
                latest_block
                if latest_block is not None
                else await self._web3.eth.block_number
            )
        except Exception as error:
            raise BlockchainConnectionError(
                f"Unable to read Base Sepolia USDC logs from block "
                f"{from_block} to {to_block}"
            ) from error

        block_cache: dict[int, tuple[str, datetime]] = {}
        transfers: list[UsdcTransfer] = []
        for log in logs:
            if log.get("removed", False):
                continue
            if log["address"].lower() != self._usdc_address.lower():
                continue

            topics = log["topics"]
            if len(topics) < 3 or Web3.to_hex(topics[0]).lower() != TRANSFER_EVENT_TOPIC:
                continue

            try:
                sender = _address_from_topic(topics[1])
                recipient = _address_from_topic(topics[2])
                raw_amount = int(Web3.to_hex(log["data"]), 16)
                block_number = int(log["blockNumber"])
                transaction_hash = Web3.to_hex(log["transactionHash"]).lower()
                log_index = int(log["logIndex"])
                log_block_hash = Web3.to_hex(log["blockHash"]).lower()
            except (KeyError, TypeError, ValueError) as error:
                raise BlockchainTransactionError(
                    "USDC Transfer log contains invalid encoded data"
                ) from error

            if recipient not in normalized_recipients:
                continue

            if block_number not in block_cache:
                try:
                    block = await self._web3.eth.get_block(block_number)
                    canonical_hash = Web3.to_hex(block["hash"]).lower()
                    block_timestamp = datetime.fromtimestamp(
                        int(block["timestamp"]),
                        tz=timezone.utc,
                    )
                except Exception as error:
                    raise BlockchainConnectionError(
                        f"Unable to read Base Sepolia block {block_number}"
                    ) from error
                block_cache[block_number] = (canonical_hash, block_timestamp)

            canonical_hash, block_timestamp = block_cache[block_number]
            if log_block_hash != canonical_hash:
                raise BlockchainTransactionError(
                    "USDC Transfer log is no longer in the canonical block"
                )

            transfers.append(
                UsdcTransfer(
                    transaction_hash=transaction_hash,
                    log_index=log_index,
                    sender=sender,
                    recipient=recipient,
                    raw_amount=raw_amount,
                    amount=Decimal(raw_amount) / Decimal(10**USDC_DECIMALS),
                    block_number=block_number,
                    confirmations=max(chain_head - block_number + 1, 0),
                    block_hash=canonical_hash,
                    block_timestamp=block_timestamp,
                )
            )

        return sorted(
            transfers,
            key=lambda transfer: (transfer.block_number, transfer.log_index),
        )

    async def _require_base_sepolia(self) -> int:
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

        return chain_id

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


def _address_from_topic(topic: Any) -> str:
    topic_hex = Web3.to_hex(topic)
    return Web3.to_checksum_address("0x" + topic_hex[-40:])


async def get_base_sepolia_client() -> AsyncIterator[BaseSepoliaClient]:
    """Provide a request-scoped, read-only Base Sepolia client."""

    async with BaseSepoliaClient.from_settings() as client:
        yield client
