from datetime import datetime
from datetime import timezone

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Response
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.authentication import get_authenticated_merchant
from database.database import get_session
from database.models import Merchant
from database.models import MerchantApiKey
from schemas.merchants import MerchantApiKeyCreate
from schemas.merchants import MerchantApiKeyCreatedResponse
from schemas.merchants import MerchantApiKeyResponse
from schemas.merchants import MerchantResponse
from schemas.merchants import MerchantUpdate
from services.api_keys import ApiKeyError
from services.api_keys import generate_merchant_api_key
from services.merchants import DuplicateMerchantWalletError
from services.merchants import MerchantAccountError
from services.merchants import update_merchant_account


router = APIRouter(
    prefix="/merchants",
    tags=["merchants"],
)


@router.get("/me", response_model=MerchantResponse)
async def get_current_merchant(
    merchant: Merchant = Depends(get_authenticated_merchant),
):
    """Return the merchant represented by the Bearer API key."""

    return merchant


@router.patch("/me", response_model=MerchantResponse)
async def update_current_merchant(
    request: MerchantUpdate,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Update settings used for future payments and webhook events."""

    changes = request.model_dump(exclude_unset=True)
    try:
        await update_merchant_account(session, merchant, **changes)
    except DuplicateMerchantWalletError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MerchantAccountError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    await session.commit()
    await session.refresh(merchant)
    return merchant


@router.post(
    "/me/api-keys",
    response_model=MerchantApiKeyCreatedResponse,
    status_code=201,
)
async def create_current_merchant_api_key(
    request: MerchantApiKeyCreate,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """Create a key and reveal its plaintext only in this response."""

    try:
        generated = generate_merchant_api_key(
            merchant_id=merchant.id,
            name=request.name,
            expires_at=request.expires_at,
        )
    except ApiKeyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    session.add(generated.record)
    await session.commit()

    return MerchantApiKeyCreatedResponse(
        id=generated.record.id,
        name=generated.record.name,
        key_prefix=generated.record.key_prefix,
        created_at=generated.record.created_at,
        last_used_at=generated.record.last_used_at,
        expires_at=generated.record.expires_at,
        revoked_at=generated.record.revoked_at,
        api_key=generated.plaintext,
    )


@router.get(
    "/me/api-keys",
    response_model=list[MerchantApiKeyResponse],
)
async def list_current_merchant_api_keys(
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
):
    """List only the authenticated merchant's safe key metadata."""

    result = await session.execute(
        select(MerchantApiKey)
        .where(MerchantApiKey.merchant_id == merchant.id)
        .order_by(MerchantApiKey.created_at.desc(), MerchantApiKey.id.desc())
    )
    return list(result.scalars())


@router.delete(
    "/me/api-keys/{api_key_id}",
    status_code=204,
)
async def revoke_current_merchant_api_key(
    api_key_id: str,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke one of the authenticated merchant's API keys."""

    result = await session.execute(
        select(MerchantApiKey).where(
            MerchantApiKey.id == api_key_id,
            MerchantApiKey.merchant_id == merchant.id,
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key.revoked_at is None:
        current_time = datetime.now(timezone.utc)
        key_is_usable = (
            api_key.expires_at is None
            or _database_timestamp_as_utc(api_key.expires_at) > current_time
        )
        if key_is_usable:
            replacement_result = await session.execute(
                select(MerchantApiKey.id)
                .where(
                    MerchantApiKey.merchant_id == merchant.id,
                    MerchantApiKey.id != api_key.id,
                    MerchantApiKey.revoked_at.is_(None),
                    or_(
                        MerchantApiKey.expires_at.is_(None),
                        MerchantApiKey.expires_at > current_time,
                    ),
                )
                .limit(1)
            )
            if replacement_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot revoke the merchant's last active API key",
                )

        api_key.revoked_at = current_time
        await session.commit()

    return Response(status_code=204)


def _database_timestamp_as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
