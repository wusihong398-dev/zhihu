import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.article_prompt import ArticlePromptFolder, ArticlePromptTemplate
from app.models.user import User
from app.schemas.article_prompt import (
    PromptFolderCreate,
    PromptFolderRead,
    PromptFolderUpdate,
    PromptTemplateCreate,
    PromptTemplateListResponse,
    PromptTemplateRead,
    PromptTemplateUpdate,
)

router = APIRouter(
    tags=["article-prompts"], dependencies=[Depends(require_active_user)]
)


def _clean_name(value: str) -> tuple[str, str]:
    name = " ".join(value.split())
    if not name:
        raise HTTPException(status_code=422, detail="名称不能为空")
    return name, name.casefold()


async def _folder_for_user(
    folder_id: uuid.UUID, user: User, db: AsyncSession
) -> ArticlePromptFolder:
    folder = await db.get(ArticlePromptFolder, folder_id)
    if folder is None or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="提示词文件夹不存在")
    return folder


async def _template_for_user(
    template_id: uuid.UUID, user: User, db: AsyncSession
) -> ArticlePromptTemplate:
    template = await db.get(ArticlePromptTemplate, template_id)
    if template is None or template.user_id != user.id:
        raise HTTPException(status_code=404, detail="提示词模板不存在")
    return template


async def _resolve_folder(
    folder_id: uuid.UUID | None,
    folder_name: str | None,
    user: User,
    db: AsyncSession,
) -> uuid.UUID | None:
    if folder_id is not None:
        return (await _folder_for_user(folder_id, user, db)).id
    if folder_name and folder_name.strip():
        name, normalized = _clean_name(folder_name)
        result = await db.execute(
            select(ArticlePromptFolder).where(
                ArticlePromptFolder.user_id == user.id,
                ArticlePromptFolder.normalized_name == normalized,
            )
        )
        folder = result.scalar_one_or_none()
        if folder is None:
            folder = ArticlePromptFolder(
                user_id=user.id, name=name, normalized_name=normalized
            )
            db.add(folder)
            await db.flush()
        return folder.id
    return None


@router.get("/article-prompt-folders", response_model=list[PromptFolderRead])
async def list_prompt_folders(
    db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)
) -> list[PromptFolderRead]:
    article_counts = (
        select(
            ArticlePromptTemplate.folder_id,
            func.count(ArticlePromptTemplate.id).label("template_count"),
        )
        .where(ArticlePromptTemplate.user_id == user.id)
        .group_by(ArticlePromptTemplate.folder_id)
        .subquery()
    )
    result = await db.execute(
        select(
            ArticlePromptFolder,
            func.coalesce(article_counts.c.template_count, 0),
        )
        .outerjoin(
            article_counts, article_counts.c.folder_id == ArticlePromptFolder.id
        )
        .where(ArticlePromptFolder.user_id == user.id)
        .order_by(ArticlePromptFolder.name.asc())
    )
    return [
        PromptFolderRead.model_validate(folder).model_copy(
            update={"template_count": template_count}
        )
        for folder, template_count in result
    ]


@router.post(
    "/article-prompt-folders",
    response_model=PromptFolderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_folder(
    payload: PromptFolderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptFolderRead:
    name, normalized = _clean_name(payload.name)
    folder = ArticlePromptFolder(user_id=user.id, name=name, normalized_name=normalized)
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名文件夹已存在") from exc
    await db.refresh(folder)
    return PromptFolderRead.model_validate(folder)


@router.patch("/article-prompt-folders/{folder_id}", response_model=PromptFolderRead)
async def update_prompt_folder(
    folder_id: uuid.UUID,
    payload: PromptFolderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptFolderRead:
    folder = await _folder_for_user(folder_id, user, db)
    folder.name, folder.normalized_name = _clean_name(payload.name)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名文件夹已存在") from exc
    await db.refresh(folder)
    article_count = await db.scalar(
        select(func.count(ArticlePromptTemplate.id)).where(
            ArticlePromptTemplate.folder_id == folder.id,
            ArticlePromptTemplate.user_id == user.id,
        )
    )
    return PromptFolderRead.model_validate(folder).model_copy(
        update={"template_count": article_count or 0}
    )


@router.delete(
    "/article-prompt-folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_prompt_folder(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    folder = await _folder_for_user(folder_id, user, db)
    await db.delete(folder)
    await db.commit()


def _template_read(
    template: ArticlePromptTemplate, folder_name: str | None = None
) -> PromptTemplateRead:
    return PromptTemplateRead.model_validate(template).model_copy(
        update={"folder_name": folder_name}
    )


@router.get("/article-prompt-templates", response_model=PromptTemplateListResponse)
async def list_prompt_templates(
    folder_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptTemplateListResponse:
    filters = [ArticlePromptTemplate.user_id == user.id]
    if folder_id:
        await _folder_for_user(folder_id, user, db)
        filters.append(ArticlePromptTemplate.folder_id == folder_id)
    result = await db.execute(
        select(ArticlePromptTemplate, ArticlePromptFolder.name)
        .outerjoin(
            ArticlePromptFolder,
            ArticlePromptFolder.id == ArticlePromptTemplate.folder_id,
        )
        .where(*filters)
        .order_by(
            ArticlePromptTemplate.updated_at.desc(), ArticlePromptTemplate.name.asc()
        )
    )
    items = [_template_read(template, folder_name) for template, folder_name in result]
    return PromptTemplateListResponse(items=items, total=len(items))


@router.post(
    "/article-prompt-templates",
    response_model=PromptTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_template(
    payload: PromptTemplateCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptTemplateRead:
    name, normalized = _clean_name(payload.name)
    folder_id = await _resolve_folder(payload.folder_id, payload.folder_name, user, db)
    template = ArticlePromptTemplate(
        user_id=user.id,
        folder_id=folder_id,
        name=name,
        normalized_name=normalized,
        title_prompt=payload.title_prompt.strip(),
        content_prompt=payload.content_prompt.strip(),
    )
    db.add(template)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名提示词模板已存在") from exc
    await db.refresh(template)
    folder_name = None
    if template.folder_id:
        folder_name = (await db.get(ArticlePromptFolder, template.folder_id)).name
    return _template_read(template, folder_name)


@router.patch(
    "/article-prompt-templates/{template_id}", response_model=PromptTemplateRead
)
async def update_prompt_template(
    template_id: uuid.UUID,
    payload: PromptTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptTemplateRead:
    template = await _template_for_user(template_id, user, db)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        template.name, template.normalized_name = _clean_name(changes["name"])
    if "folder_id" in changes or "folder_name" in changes:
        template.folder_id = await _resolve_folder(
            changes.get("folder_id"), changes.get("folder_name"), user, db
        )
    if "title_prompt" in changes:
        template.title_prompt = changes["title_prompt"].strip()
    if "content_prompt" in changes:
        template.content_prompt = changes["content_prompt"].strip()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名提示词模板已存在") from exc
    await db.refresh(template)
    folder_name = None
    if template.folder_id:
        folder_name = (await db.get(ArticlePromptFolder, template.folder_id)).name
    return _template_read(template, folder_name)


@router.delete(
    "/article-prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_prompt_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    template = await _template_for_user(template_id, user, db)
    await db.delete(template)
    await db.commit()
