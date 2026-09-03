"""Create and expire expected deposits into StablePay's shared vault wallet."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import Vault
from database.models import VaultDeposit
from domain.ledger import VaultDepositStatus


class VaultDepositError(ValueError):
    """Raised when a vault deposit intent is invalid."""


async def create_vault_deposit(
    session: AsyncSession,
    vault: Vault,
    amount_atomic: int,
    current_time: datetime | None = None,
) -> VaultDeposit:
    if amount_atomic <= 0:
        raise VaultDepositError("Vault deposit amount must be positive")

    now = _as_utc(current_time or datetime.now(timezone.utc))
    duplicate_result = await session.execute(
        select(VaultDeposit.id).where(
            VaultDeposit.vault_id == vault.id,
            VaultDeposit.amount_atomic == amount_atomic,
            VaultDeposit.status == VaultDepositStatus.PENDING,
        )
    )
    if duplicate_result.scalar_one_or_none() is not None:
        raise VaultDepositError(
            "This vault already has a pending deposit for the same amount"
        )

    deposit = VaultDeposit(
        id=f"dep_{uuid4().hex}",
        vault_id=vault.id,
        amount_atomic=amount_atomic,
        status=VaultDepositStatus.PENDING,
        created_at=now,
        expires_at=now
        + timedelta(minutes=settings.vault_deposit_expiration_minutes),
    )
    session.add(deposit)
    await session.flush()
    return deposit


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise VaultDepositError("Vault deposit timestamp must include a timezone")
    return value.astimezone(timezone.utc)
