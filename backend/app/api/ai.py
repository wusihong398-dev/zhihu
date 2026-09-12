from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.ai_provider import AIProviderConfig
from app.models.user import User, UserRole
from app.models.user_ai_provider import UserAIProviderConfig
from app.schemas.ai_provider import (
    AIProviderRead,
    AIProviderTestRequest,
    AIProviderTestResult,
    AIProviderUpdate,
)
from app.services.ai_providers import PROVIDERS, ProviderDefinition, test_provider
from app.services.secret_box import decrypt_secret, encrypt_secret, mask_secret

router = APIRouter(
    prefix="/ai/providers",
    tags=["ai providers"],
    dependencies=[Depends(require_active_user)],
)


async def _get_configs(db: AsyncSession, user: User) -> dict[str, UserAIProviderConfig]:
    result = await db.execute(
        select(UserAIProviderConfig).where(UserAIProviderConfig.user_id == user.id)
    )
    configs = {item.provider: item for item in result.scalars()}
    legacy: dict[str, AIProviderConfig] = {}
    if not configs and user.role == UserRole.admin:
        legacy_result = await db.execute(select(AIProviderConfig))
        legacy = {item.provider: item for item in legacy_result.scalars()}
    changed = False
    for provider, definition in PROVIDERS.items():
        if provider not in configs:
            old = legacy.get(provider)
            config = UserAIProviderConfig(
                user_id=user.id,
                provider=provider,
                model=old.model if old else definition.models[0],
                api_key_encrypted=old.api_key_encrypted if old else None,
                enabled=old.enabled if old else False,
                last_test_ok=old.last_test_ok if old else None,
                last_test_message=old.last_test_message if old else None,
                last_tested_at=old.last_tested_at if old else None,
            )
            db.add(config)
            configs[provider] = config
            changed = True
    if changed:
        await db.commit()
        for config in configs.values():
            await db.refresh(config)
    return configs


def _to_read(
    config: UserAIProviderConfig, definition: ProviderDefinition
) -> AIProviderRead:
    masked_key = None
    if config.api_key_encrypted:
        try:
            masked_key = mask_secret(decrypt_secret(config.api_key_encrypted))
        except ValueError:
            masked_key = "已保存（需要重新填写）"
    return AIProviderRead(
        provider=definition.provider,
        display_name=definition.display_name,
        base_url=definition.base_url,
        models=list(definition.models),
        model=config.model,
        enabled=config.enabled,
        has_api_key=bool(config.api_key_encrypted),
        masked_key=masked_key,
        last_test_ok=config.last_test_ok,
        last_test_message=config.last_test_message,
        last_tested_at=config.last_tested_at,
    )


@router.get("", response_model=list[AIProviderRead])
async def list_providers(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> list[AIProviderRead]:
    configs = await _get_configs(db, user)
    return [_to_read(configs[key], definition) for key, definition in PROVIDERS.items()]


@router.put("/{provider}", response_model=AIProviderRead)
async def update_provider(
    provider: str,
    payload: AIProviderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AIProviderRead:
    definition = PROVIDERS.get(provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="AI 平台不存在")
    configs = await _get_configs(db, user)
    config = configs[provider]
    config.model = payload.model.strip()
    config.enabled = payload.enabled
    if payload.api_key and payload.api_key.strip():
        config.api_key_encrypted = encrypt_secret(payload.api_key.strip())
        config.last_test_ok = None
        config.last_test_message = None
        config.last_tested_at = None
    await db.commit()
    await db.refresh(config)
    return _to_read(config, definition)


@router.post("/{provider}/test", response_model=AIProviderTestResult)
async def check_provider(
    provider: str,
    payload: AIProviderTestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AIProviderTestResult:
    definition = PROVIDERS.get(provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="AI 平台不存在")
    configs = await _get_configs(db, user)
    config = configs[provider]
    api_key = payload.api_key.strip() if payload.api_key else ""
    if not api_key and config.api_key_encrypted:
        try:
            api_key = decrypt_secret(config.api_key_encrypted)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    model = (payload.model or config.model).strip()
    ok, message, latency_ms = await test_provider(definition, api_key, model)
    config.last_test_ok = ok
    config.last_test_message = message[:255]
    config.last_tested_at = datetime.now(UTC)
    await db.commit()
    return AIProviderTestResult(ok=ok, message=message, latency_ms=latency_ms)
