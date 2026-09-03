"""Controlled values used by the vault and internal USDC ledger."""

from enum import StrEnum


class VaultDepositStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


class LedgerOwnerType(StrEnum):
    SYSTEM = "system"
    VAULT = "vault"
    MERCHANT = "merchant"


class LedgerTransactionType(StrEnum):
    DEPOSIT = "deposit"
    MICROPAYMENT = "micropayment"
    SETTLEMENT = "settlement"
