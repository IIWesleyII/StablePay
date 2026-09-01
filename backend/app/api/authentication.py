"""FastAPI dependency for authenticating merchant API keys."""

from datetime import datetime
from datetime import timezone

from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_session
from database.models import Merchant
from database.models import MerchantApiKey
from services.api_keys import ApiKeyError
from services.api_keys import parse_api_key_id
from services.api_keys import verify_api_key


bearer_scheme = HTTPBearer(auto_error=False)


async def get_authenticated_merchant(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Merchant:
    """Return the active merchant represented by a valid Bearer API key."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_api_key()

    plaintext_key = credentials.credentials
    try:
        key_id = parse_api_key_id(plaintext_key)
    except ApiKeyError as error:
        raise _invalid_api_key() from error

    result = await session.execute(
        select(MerchantApiKey, Merchant)
        .join(Merchant, Merchant.id == MerchantApiKey.merchant_id)
        .where(MerchantApiKey.id == key_id)
    )
    row = result.one_or_none()

    if row is None:
        raise _invalid_api_key()

    api_key, merchant = row
    current_time = datetime.now(timezone.utc)

    if not verify_api_key(plaintext_key, api_key.secret_hash):
        raise _invalid_api_key()
    if api_key.revoked_at is not None:
        raise _invalid_api_key()
    if (
        api_key.expires_at is not None
        and current_time >= _database_timestamp_as_utc(api_key.expires_at)
    ):
        raise _invalid_api_key()
    if not merchant.is_active:
        raise HTTPException(
            status_code=403,
            detail="Merchant account is inactive",
        )

    api_key.last_used_at = current_time
    await session.commit()

    return merchant


def _invalid_api_key() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _database_timestamp_as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
