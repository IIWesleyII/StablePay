"""Bearer-token authentication for a customer's reusable vault session."""

from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_session
from database.models import Vault
from services.vaults import VaultError
from services.vaults import parse_vault_id
from services.vaults import verify_vault_access_token


vault_bearer_scheme = HTTPBearer(auto_error=False)


async def get_authenticated_vault(
    credentials: HTTPAuthorizationCredentials | None = Depends(vault_bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Vault:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_token()
    try:
        vault_id = parse_vault_id(credentials.credentials)
    except VaultError as error:
        raise _invalid_token() from error

    vault = await session.get(Vault, vault_id)
    if (
        vault is None
        or not vault.is_active
        or not verify_vault_access_token(
            credentials.credentials,
            vault.access_token_hash,
        )
    ):
        raise _invalid_token()
    return vault


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid vault access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
