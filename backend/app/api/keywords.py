import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.keyword import AccountKeyword
from app.models.keyword_job import KeywordCollectionJob, KeywordJobStatus
from app.schemas.keyword import (
    KeywordJobCreate,
    KeywordJobRead,
    KeywordListResponse,
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
    job = KeywordCollectionJob(
        account_id=account_id,
        seed_keyword=payload.seed_keyword.strip(),
        source=payload.source,
        target_count=payload.target_count,
    )
    db.add(job)
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
    count = await db.scalar(select(func.count(AccountKeyword.id)).where(*filters))
    result = await db.execute(
        select(AccountKeyword)
        .where(*filters)
        .order_by(AccountKeyword.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return KeywordListResponse(items=list(result.scalars()), total=count or 0)
