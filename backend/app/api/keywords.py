import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.keyword import AccountKeyword
from app.models.keyword_folder import (
    KeywordFolder,
    KeywordFolderItem,
    KeywordJobDestination,
)
from app.models.keyword_job import KeywordCollectionJob, KeywordJobStatus
from app.schemas.keyword import (
    KeywordJobCreate,
    KeywordJobRead,
    KeywordBulkResult,
    KeywordDeleteRequest,
    KeywordFolderCreate,
    KeywordFolderRead,
    KeywordFolderUpdate,
    KeywordListResponse,
    KeywordMoveRequest,
    KeywordRead,
)
from app.services.keyword_collector import run_keyword_job

router = APIRouter(
    prefix="/accounts/{account_id}",
    tags=["keywords"],
    dependencies=[Depends(require_admin)],
)


async def _require_account(account_id: uuid.UUID, db: AsyncSession) -> ZhihuAccount:
    account = await db.get(ZhihuAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="知乎账号不存在")
    return account


async def _require_folder(
    account_id: uuid.UUID, folder_id: uuid.UUID, db: AsyncSession
) -> KeywordFolder:
    result = await db.execute(
        select(KeywordFolder).where(
            KeywordFolder.id == folder_id,
            KeywordFolder.account_id == account_id,
        )
    )
    folder = result.scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="关键词文件夹不存在")
    return folder


def _folder_name(value: str) -> tuple[str, str]:
    name = " ".join(value.split()).strip()
    if not name:
        raise HTTPException(status_code=422, detail="文件夹名称不能为空")
    return name, name.casefold()


@router.get("/keyword-folders", response_model=list[KeywordFolderRead])
async def list_keyword_folders(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[KeywordFolderRead]:
    await _require_account(account_id, db)
    result = await db.execute(
        select(KeywordFolder, func.count(KeywordFolderItem.keyword_id))
        .outerjoin(KeywordFolderItem, KeywordFolderItem.folder_id == KeywordFolder.id)
        .where(KeywordFolder.account_id == account_id)
        .group_by(KeywordFolder.id)
        .order_by(KeywordFolder.created_at.asc())
    )
    return [
        KeywordFolderRead(
            id=folder.id,
            account_id=folder.account_id,
            name=folder.name,
            keyword_count=count,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )
        for folder, count in result.all()
    ]


@router.post(
    "/keyword-folders",
    response_model=KeywordFolderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_keyword_folder(
    account_id: uuid.UUID,
    payload: KeywordFolderCreate,
    db: AsyncSession = Depends(get_db),
) -> KeywordFolderRead:
    await _require_account(account_id, db)
    name, normalized_name = _folder_name(payload.name)
    duplicate = await db.scalar(
        select(func.count(KeywordFolder.id)).where(
            KeywordFolder.account_id == account_id,
            KeywordFolder.normalized_name == normalized_name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同名关键词文件夹已存在")
    folder = KeywordFolder(
        account_id=account_id, name=name, normalized_name=normalized_name
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return KeywordFolderRead(
        id=folder.id,
        account_id=folder.account_id,
        name=folder.name,
        keyword_count=0,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.put("/keyword-folders/{folder_id}", response_model=KeywordFolderRead)
async def update_keyword_folder(
    account_id: uuid.UUID,
    folder_id: uuid.UUID,
    payload: KeywordFolderUpdate,
    db: AsyncSession = Depends(get_db),
) -> KeywordFolderRead:
    folder = await _require_folder(account_id, folder_id, db)
    name, normalized_name = _folder_name(payload.name)
    duplicate = await db.scalar(
        select(func.count(KeywordFolder.id)).where(
            KeywordFolder.account_id == account_id,
            KeywordFolder.normalized_name == normalized_name,
            KeywordFolder.id != folder_id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同名关键词文件夹已存在")
    folder.name = name
    folder.normalized_name = normalized_name
    count = await db.scalar(
        select(func.count(KeywordFolderItem.keyword_id)).where(
            KeywordFolderItem.folder_id == folder_id
        )
    )
    await db.commit()
    await db.refresh(folder)
    return KeywordFolderRead(
        id=folder.id,
        account_id=folder.account_id,
        name=folder.name,
        keyword_count=count or 0,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.delete("/keyword-folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyword_folder(
    account_id: uuid.UUID,
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    folder = await _require_folder(account_id, folder_id, db)
    await db.delete(folder)
    await db.commit()


@router.post(
    "/keyword-jobs",
    response_model=KeywordJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_keyword_job(
    account_id: uuid.UUID,
    payload: KeywordJobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> KeywordCollectionJob:
    await _require_account(account_id, db)
    active_result = await db.execute(
        select(KeywordCollectionJob).where(
            KeywordCollectionJob.account_id == account_id,
            KeywordCollectionJob.status.in_(
                [KeywordJobStatus.pending, KeywordJobStatus.running]
            ),
        )
    )
    if active_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该账号已有关键词采集任务正在运行")
    if payload.folder_id:
        await _require_folder(account_id, payload.folder_id, db)
    job = KeywordCollectionJob(
        account_id=account_id,
        seed_keyword=payload.seed_keyword.strip(),
        source=payload.source,
        target_count=payload.target_count,
    )
    db.add(job)
    await db.flush()
    if payload.folder_id:
        db.add(KeywordJobDestination(job_id=job.id, folder_id=payload.folder_id))
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(run_keyword_job, job.id)
    return job


@router.get("/keyword-jobs/latest", response_model=KeywordJobRead | None)
async def latest_keyword_job(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> KeywordCollectionJob | None:
    await _require_account(account_id, db)
    result = await db.execute(
        select(KeywordCollectionJob)
        .where(KeywordCollectionJob.account_id == account_id)
        .order_by(KeywordCollectionJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/keywords", response_model=KeywordListResponse)
async def list_keywords(
    account_id: uuid.UUID,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    folder_id: uuid.UUID | None = Query(default=None),
    unfiled: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> KeywordListResponse:
    await _require_account(account_id, db)
    filters = [AccountKeyword.account_id == account_id]
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                AccountKeyword.keyword.ilike(pattern),
                AccountKeyword.seed_keyword.ilike(pattern),
            )
        )
    if folder_id:
        await _require_folder(account_id, folder_id, db)
        filters.append(KeywordFolderItem.folder_id == folder_id)
    elif unfiled:
        filters.append(KeywordFolderItem.keyword_id.is_(None))
    count = await db.scalar(
        select(func.count(AccountKeyword.id))
        .outerjoin(KeywordFolderItem, KeywordFolderItem.keyword_id == AccountKeyword.id)
        .where(*filters)
    )
    result = await db.execute(
        select(AccountKeyword, KeywordFolderItem.folder_id)
        .outerjoin(KeywordFolderItem, KeywordFolderItem.keyword_id == AccountKeyword.id)
        .where(*filters)
        .order_by(AccountKeyword.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [
        KeywordRead.model_validate(keyword).model_copy(
            update={"folder_id": item_folder_id}
        )
        for keyword, item_folder_id in result.all()
    ]
    return KeywordListResponse(items=items, total=count or 0)


@router.patch("/keywords/folder", response_model=KeywordBulkResult)
async def move_keywords(
    account_id: uuid.UUID,
    payload: KeywordMoveRequest,
    db: AsyncSession = Depends(get_db),
) -> KeywordBulkResult:
    await _require_account(account_id, db)
    keyword_ids = list(dict.fromkeys(payload.keyword_ids))
    owned_result = await db.execute(
        select(AccountKeyword.id).where(
            AccountKeyword.account_id == account_id,
            AccountKeyword.id.in_(keyword_ids),
        )
    )
    owned_ids = set(owned_result.scalars())
    if len(owned_ids) != len(keyword_ids):
        raise HTTPException(status_code=404, detail="部分关键词不存在或不属于当前账号")
    if payload.folder_id:
        await _require_folder(account_id, payload.folder_id, db)

    await db.execute(
        delete(KeywordFolderItem).where(KeywordFolderItem.keyword_id.in_(keyword_ids))
    )
    if payload.folder_id:
        db.add_all(
            KeywordFolderItem(keyword_id=keyword_id, folder_id=payload.folder_id)
            for keyword_id in keyword_ids
        )
    await db.commit()
    return KeywordBulkResult(affected_count=len(keyword_ids))


@router.post("/keywords/bulk-delete", response_model=KeywordBulkResult)
async def delete_keywords(
    account_id: uuid.UUID,
    payload: KeywordDeleteRequest,
    db: AsyncSession = Depends(get_db),
) -> KeywordBulkResult:
    await _require_account(account_id, db)
    keyword_ids = list(dict.fromkeys(payload.keyword_ids))
    result = await db.execute(
        delete(AccountKeyword)
        .where(
            AccountKeyword.account_id == account_id,
            AccountKeyword.id.in_(keyword_ids),
        )
        .returning(AccountKeyword.id)
    )
    affected_count = len(result.scalars().all())
    await db.commit()
    return KeywordBulkResult(affected_count=affected_count)
