from datetime import datetime
from datetime import timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean
from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from database.database import Base
from domain.payments import PaymentStatus
from domain.settlements import SettlementStatus
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from domain.ledger import VaultDepositStatus
from domain.webhooks import WebhookDeliveryStatus


payment_status_type = SqlEnum(
    PaymentStatus,
    name="payment_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)

webhook_delivery_status_type = SqlEnum(
    WebhookDeliveryStatus,
    name="webhook_delivery_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)

vault_deposit_status_type = SqlEnum(
    VaultDepositStatus,
    name="vault_deposit_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)

ledger_owner_type = SqlEnum(
    LedgerOwnerType,
    name="ledger_owner_type",
    values_callable=lambda owner_enum: [owner.value for owner in owner_enum],
    validate_strings=True,
    create_constraint=True,
)

ledger_transaction_type = SqlEnum(
    LedgerTransactionType,
    name="ledger_transaction_type",
    values_callable=lambda transaction_enum: [
        transaction_type.value for transaction_type in transaction_enum
    ],
    validate_strings=True,
    create_constraint=True,
)

settlement_status_type = SqlEnum(
    SettlementStatus,
    name="settlement_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)


class Merchant(Base):
    """A business that accepts payments through StablePay."""

    __tablename__ = "merchants"
    __table_args__ = (
        UniqueConstraint(
            "wallet_address",
            name="uq_merchants_wallet_address",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    wallet_address: Mapped[str] = mapped_column(
        String(42),
        nullable=False,
    )
    webhook_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MerchantApiKey(Base):
    """A hashed credential that authenticates one merchant."""

    __tablename__ = "merchant_api_keys"
    __table_args__ = (
        UniqueConstraint(
            "secret_hash",
            name="uq_merchant_api_keys_secret_hash",
        ),
        Index(
            "ix_merchant_api_keys_merchant_revoked",
            "merchant_id",
            "revoked_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
    )
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    key_prefix: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    secret_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "transaction_hash",
            name="uq_payments_transaction_hash",
        ),
        Index(
            "ix_payments_merchant_created_at",
            "merchant_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
    )
    merchant_id: Mapped[str | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(30, 6),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USDC",
    )
    chain: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="base-sepolia",
    )
    recipient_address: Mapped[str] = mapped_column(
        String(42),
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        payment_status_type,
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    transaction_hash: Mapped[str | None] = mapped_column(
        String(66),
        nullable=True,
    )
    payer_address: Mapped[str | None] = mapped_column(
        String(42),
        nullable=True,
    )
    transaction_block_number: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    transaction_log_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class BlockchainCursor(Base):
    """Durable progress for one blockchain/token log scanner."""

    __tablename__ = "blockchain_cursors"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    chain: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    token_address: Mapped[str] = mapped_column(
        String(42),
        nullable=False,
    )
    last_scanned_block: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    last_scanned_block_hash: Mapped[str | None] = mapped_column(
        String(66),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class VaultChallenge(Base):
    """A short-lived message used to prove control of a wallet once."""

    __tablename__ = "vault_challenges"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Vault(Base):
    """A customer's authenticated internal USDC balance."""

    __tablename__ = "vaults"
    __table_args__ = (
        UniqueConstraint("wallet_address", name="uq_vaults_wallet_address"),
        UniqueConstraint("access_token_hash", name="uq_vaults_access_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    access_token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class VaultDeposit(Base):
    """An expected on-chain USDC transfer into StablePay's vault wallet."""

    __tablename__ = "vault_deposits"
    __table_args__ = (
        CheckConstraint("amount_atomic > 0", name="ck_vault_deposits_positive"),
        UniqueConstraint(
            "transaction_hash",
            "transaction_log_index",
            name="uq_vault_deposits_transaction_log",
        ),
        Index(
            "ix_vault_deposits_status_created_at",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    vault_id: Mapped[str] = mapped_column(
        ForeignKey("vaults.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_atomic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[VaultDepositStatus] = mapped_column(
        vault_deposit_status_type,
        nullable=False,
        default=VaultDepositStatus.PENDING,
        server_default=VaultDepositStatus.PENDING.value,
    )
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    transaction_block_number: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    transaction_log_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_atomic) / Decimal(1_000_000)


class LedgerAccount(Base):
    """One cached balance whose history is represented by ledger entries."""

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "currency",
            name="uq_ledger_accounts_owner_currency",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_type: Mapped[LedgerOwnerType] = mapped_column(
        ledger_owner_type,
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(40), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USDC",
        server_default="USDC",
    )
    balance_atomic: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def balance(self) -> Decimal:
        return Decimal(self.balance_atomic) / Decimal(1_000_000)


class LedgerTransaction(Base):
    """A logical transfer represented by two or more balanced entries."""

    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ledger_transactions_idempotency"),
        CheckConstraint("amount_atomic > 0", name="ck_ledger_transactions_positive"),
        Index("ix_ledger_transactions_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_type: Mapped[LedgerTransactionType] = mapped_column(
        ledger_transaction_type,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_account_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_account_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_atomic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_atomic) / Decimal(1_000_000)


class LedgerEntry(Base):
    """One immutable signed side of a balanced ledger transaction."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_atomic != 0", name="ck_ledger_entries_nonzero"),
        Index(
            "ix_ledger_entries_account_created_at",
            "account_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_atomic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_atomic) / Decimal(1_000_000)


class Settlement(Base):
    """One aggregate on-chain payout of a merchant's internal balance."""

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("amount_atomic > 0", name="ck_settlements_positive"),
        UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_settlements_merchant_idempotency",
        ),
        UniqueConstraint(
            "transaction_hash",
            name="uq_settlements_transaction_hash",
        ),
        Index(
            "ix_settlements_status_created_at",
            "status",
            "created_at",
        ),
        Index(
            "ix_settlements_merchant_created_at",
            "merchant_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_atomic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USDC",
        server_default="USDC",
    )
    chain: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="base-sepolia",
        server_default="base-sepolia",
    )
    destination_address: Mapped[str] = mapped_column(String(42), nullable=False)
    status: Mapped[SettlementStatus] = mapped_column(
        settlement_status_type,
        nullable=False,
        default=SettlementStatus.PENDING,
        server_default=SettlementStatus.PENDING.value,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reservation_ledger_transaction_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    completion_ledger_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    transaction_nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    signed_transaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    broadcast_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_atomic) / Decimal(1_000_000)


class WebhookEvent(Base):
    """A durable merchant notification waiting to be delivered."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "payment_id",
            name="uq_webhook_events_event_type_payment_id",
        ),
        Index(
            "ix_webhook_events_delivery_queue",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        webhook_delivery_status_type,
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        server_default=WebhookDeliveryStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
