import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user, user_is_expired
from app.db.session import get_db
from app.models.account import AccountStatus, ZhihuAccount
from app.models.article import Article, ArticleStatus
from app.models.article_job import ArticleJob, ArticleJobStatus
from app.models.answer import AnswerStatus, ZhihuAnswer
from app.models.answer_job import AnswerJob, AnswerJobStatus
from app.models.local_media import LocalMediaAsset
from app.models.local_publisher import (
    LocalAnswerPublishTask,
    LocalArticlePublishTask,
    LocalPublisherDevice,
)
from app.models.user import User, UserRole
from app.schemas.local_publisher import (
    LocalPublisherAccountRead,
    LocalPublisherDeviceCreate,
    LocalPublisherDeviceCreated,
    LocalPublisherDeviceRead,
    LocalPublisherHeartbeatRead,
    LocalPublisherTaskRead,
    LocalPublisherTaskResult,
)
from app.services.local_publisher import create_device_token, hash_device_token
from app.services.local_media import public_asset_url


router = APIRouter(prefix="/local-publisher", tags=["local-publisher"])
LEASE_SECONDS = 300


async def require_local_device(
    x_totod_device_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> LocalPublisherDevice:
    if not x_totod_device_token:
        raise HTTPException(status_code=401, detail="缺少本地发布客户端密钥")
    device = await db.scalar(
        select(LocalPublisherDevice).where(
            LocalPublisherDevice.token_hash == hash_device_token(x_totod_device_token),
            LocalPublisherDevice.enabled.is_(True),
        )
    )
    if device is None:
        raise HTTPException(status_code=401, detail="本地发布客户端密钥无效或已停用")
    user = await db.get(User, device.user_id)
    if user is None or not user.is_active or user_is_expired(user):
        raise HTTPException(status_code=403, detail="所属系统用户已停用或到期")
    device.last_seen_at = datetime.now(UTC)
    await db.commit()
    return device


@router.get("/devices", response_model=list[LocalPublisherDeviceRead])
async def list_devices(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> list[LocalPublisherDevice]:
    return list(
        (
            await db.execute(
                select(LocalPublisherDevice)
                .where(LocalPublisherDevice.user_id == user.id)
                .order_by(LocalPublisherDevice.created_at.desc())
            )
        ).scalars()
    )


@router.post(
    "/devices",
    response_model=LocalPublisherDeviceCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_device(
    payload: LocalPublisherDeviceCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalPublisherDeviceCreated:
    token, token_hash = create_device_token()
    device = LocalPublisherDevice(
        user_id=user.id, name=payload.name.strip(), token_hash=token_hash
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    saved = LocalPublisherDeviceRead.model_validate(device)
    return LocalPublisherDeviceCreated(token=token, **saved.model_dump())


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> Response:
    device = await db.get(LocalPublisherDevice, device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail="本地发布设备不存在")
    await db.delete(device)
    await db.commit()
    return Response(status_code=204)


@router.get("/client/accounts", response_model=list[LocalPublisherAccountRead])
async def client_accounts(
    db: AsyncSession = Depends(get_db),
    device: LocalPublisherDevice = Depends(require_local_device),
) -> list[LocalPublisherAccountRead]:
    user = await db.get(User, device.user_id)
    filters = [ZhihuAccount.enabled.is_(True)]
    if user is None or user.role != UserRole.admin:
        filters.append(ZhihuAccount.owner_user_id == device.user_id)
    accounts = list(
        (
            await db.execute(
                select(ZhihuAccount)
                .where(*filters)
                .order_by(ZhihuAccount.created_at, ZhihuAccount.id)
            )
        ).scalars()
    )
    return [
        LocalPublisherAccountRead(
            id=item.id,
            display_name=item.display_name,
            remark=item.remark,
            timezone=item.timezone,
            answer_publish_mode=item.answer_publish_mode,
        )
        for item in accounts
    ]


@router.post("/client/tasks/claim", response_model=LocalPublisherTaskRead | None)
async def claim_task(
    account_id: uuid.UUID | None = Query(default=None),
    task_types: str = Query(default="answer"),
    db: AsyncSession = Depends(get_db),
    device: LocalPublisherDevice = Depends(require_local_device),
) -> LocalPublisherTaskRead | None:
    now = datetime.now(UTC)
    user = await db.get(User, device.user_id)
    requested = {
        value.strip().lower()
        for value in task_types.split(",")
        if value.strip().lower() in {"answer", "article"}
    }
    if not requested:
        requested = {"answer"}

    answer_task = None
    article_task = None
    if "answer" in requested:
        await db.execute(
            update(LocalAnswerPublishTask)
            .where(
                LocalAnswerPublishTask.status == "leased",
                LocalAnswerPublishTask.lease_expires_at < now,
            )
            .values(status="queued", leased_device_id=None, lease_expires_at=None)
        )
        answer_filters = [
            LocalAnswerPublishTask.status == "queued",
            AnswerJob.status.in_([AnswerJobStatus.pending, AnswerJobStatus.running]),
        ]
        if user is None or user.role != UserRole.admin:
            answer_filters.append(LocalAnswerPublishTask.user_id == device.user_id)
        answer_query = (
            select(LocalAnswerPublishTask)
            .join(AnswerJob, AnswerJob.id == LocalAnswerPublishTask.job_id)
            .where(*answer_filters)
        )
        if account_id is not None:
            answer_query = answer_query.where(
                LocalAnswerPublishTask.account_id == account_id
            )
        answer_task = await db.scalar(
            answer_query.order_by(LocalAnswerPublishTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    if "article" in requested:
        await db.execute(
            update(LocalArticlePublishTask)
            .where(
                LocalArticlePublishTask.status == "leased",
                LocalArticlePublishTask.lease_expires_at < now,
            )
            .values(status="queued", leased_device_id=None, lease_expires_at=None)
        )
        article_filters = [
            LocalArticlePublishTask.status == "queued",
            ArticleJob.status.in_([ArticleJobStatus.pending, ArticleJobStatus.running]),
        ]
        if user is None or user.role != UserRole.admin:
            article_filters.append(LocalArticlePublishTask.user_id == device.user_id)
        article_query = (
            select(LocalArticlePublishTask)
            .join(ArticleJob, ArticleJob.id == LocalArticlePublishTask.job_id)
            .where(*article_filters)
        )
        if account_id is not None:
            article_query = article_query.where(
                LocalArticlePublishTask.account_id == account_id
            )
        article_task = await db.scalar(
            article_query.order_by(LocalArticlePublishTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    candidates = [
        ("answer", answer_task),
        ("article", article_task),
    ]
    available = [(kind, task) for kind, task in candidates if task is not None]
    if not available:
        await db.commit()
        return None
    kind, task = min(available, key=lambda item: item[1].created_at)
    account = await db.get(ZhihuAccount, task.account_id)
    item = await db.get(
        ZhihuAnswer if kind == "answer" else Article,
        task.answer_id if kind == "answer" else task.article_id,
    )
    job = await db.get(AnswerJob if kind == "answer" else ArticleJob, task.job_id)
    if (
        account is None
        or item is None
        or job is None
        or not account.enabled
        or account.answer_publish_mode != "local"
    ):
        task.status = "failed"
        task.error_message = "账号或发布内容不存在，或账号已切换发布方式"
        task.completed_at = now
        await db.commit()
        return None
    expires = now + timedelta(seconds=LEASE_SECONDS)
    task.status = "leased"
    task.leased_device_id = device.id
    task.lease_expires_at = expires
    task.attempt_count += 1
    job.status = (
        AnswerJobStatus.running if kind == "answer" else ArticleJobStatus.running
    )
    label = item.question_title if kind == "answer" else item.title
    job.current_item = f"本地发布：{label}"
    await db.commit()
    if kind == "answer":
        return LocalPublisherTaskRead(
            id=task.id,
            job_id=task.job_id,
            task_type="answer",
            answer_id=task.answer_id,
            account_id=task.account_id,
            account_name=account.display_name,
            question_title=item.question_title,
            question_url=item.question_url,
            target_url=item.question_url,
            content=item.content,
            attempt_count=task.attempt_count,
            lease_expires_at=expires,
        )
    image_url = None
    if item.local_image_id:
        image = await db.get(LocalMediaAsset, item.local_image_id)
        if image is not None:
            image_url = public_asset_url(image)
    return LocalPublisherTaskRead(
        id=task.id,
        job_id=task.job_id,
        task_type="article",
        article_id=task.article_id,
        account_id=task.account_id,
        account_name=account.display_name,
        article_title=item.title,
        target_url="https://zhuanlan.zhihu.com/write",
        content=item.content,
        image_url=image_url,
        attempt_count=task.attempt_count,
        lease_expires_at=expires,
    )


async def _leased_task(
    task_id: uuid.UUID, device: LocalPublisherDevice, db: AsyncSession
) -> tuple[str, LocalAnswerPublishTask | LocalArticlePublishTask]:
    task = await db.get(LocalAnswerPublishTask, task_id)
    kind = "answer"
    if task is None:
        task = await db.get(LocalArticlePublishTask, task_id)
        kind = "article"
    if (
        task is None
        or task.leased_device_id != device.id
        or task.status != "leased"
    ):
        raise HTTPException(status_code=409, detail="任务租约无效或已结束")
    return kind, task


@router.post(
    "/client/tasks/{task_id}/heartbeat",
    response_model=LocalPublisherHeartbeatRead,
)
async def heartbeat_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    device: LocalPublisherDevice = Depends(require_local_device),
) -> LocalPublisherHeartbeatRead:
    _, task = await _leased_task(task_id, device, db)
    task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    await db.commit()
    return LocalPublisherHeartbeatRead(lease_expires_at=task.lease_expires_at)


@router.post("/client/tasks/{task_id}/result", status_code=status.HTTP_204_NO_CONTENT)
async def finish_task(
    task_id: uuid.UUID,
    payload: LocalPublisherTaskResult,
    db: AsyncSession = Depends(get_db),
    device: LocalPublisherDevice = Depends(require_local_device),
) -> Response:
    kind, task = await _leased_task(task_id, device, db)
    if kind == "article":
        return await _finish_article_task(task, payload, db)
    answer = await db.get(ZhihuAnswer, task.answer_id)
    job = await db.get(AnswerJob, task.job_id)
    if answer is None or job is None:
        raise HTTPException(status_code=409, detail="任务关联的回答或批次已不存在")
    now = datetime.now(UTC)
    if payload.success:
        published_url = (payload.published_url or "").strip()
        if not re.search(r"/question/\d+/answer/\d+", published_url):
            raise HTTPException(status_code=422, detail="发布成功时必须回传知乎回答公开地址")
        answer.status = AnswerStatus.published
        answer.published_url = published_url
        answer.published_at = now
        answer.publish_attempted_at = now
        answer.error_message = None
        task.status = "completed"
        task.result_url = published_url
        task.error_message = None
        job.success_count += 1
    else:
        failure = (payload.error_message or "本地客户端未确认发布成功").strip()
        answer.status = AnswerStatus.failed
        answer.publish_attempted_at = now
        answer.error_message = failure[:2000]
        task.status = "failed"
        task.error_message = failure[:2000]
        job.failed_count += 1
    task.completed_at = now
    task.lease_expires_at = None
    job.completed_count += 1
    if job.completed_count >= job.total_count:
        job.status = AnswerJobStatus.completed
        job.current_item = None
        job.completed_at = now
        job.error_message = (
            f"任务完成，其中 {job.failed_count} 个回答失败，请查看原因"
            if job.failed_count
            else None
        )
    else:
        job.current_item = "等待 Windows 本地发布客户端领取下一项"
    await db.commit()
    return Response(status_code=204)


async def _finish_article_task(
    task: LocalArticlePublishTask,
    payload: LocalPublisherTaskResult,
    db: AsyncSession,
) -> Response:
    article = await db.get(Article, task.article_id)
    job = await db.get(ArticleJob, task.job_id)
    account = await db.get(ZhihuAccount, task.account_id)
    if article is None or job is None:
        raise HTTPException(status_code=409, detail="任务关联的文章或批次已不存在")
    now = datetime.now(UTC)
    if payload.success:
        published_url = (payload.published_url or "").strip()
        if not re.search(r"zhuanlan\.zhihu\.com/p/\d+", published_url):
            raise HTTPException(status_code=422, detail="发布成功时必须回传知乎文章公开地址")
        article.status = ArticleStatus.published
        article.published_url = published_url
        article.published_at = now
        article.publish_attempted_at = now
        article.error_message = None
        task.status = "completed"
        task.result_url = published_url
        task.error_message = None
        job.success_count += 1
        if account is not None:
            account.status = AccountStatus.online
    else:
        failure = (payload.error_message or "本地客户端未确认文章发布成功").strip()
        article.status = ArticleStatus.failed
        article.publish_attempted_at = now
        article.error_message = failure[:2000]
        task.status = "failed"
        task.error_message = failure[:2000]
        job.failed_count += 1
        detail = f"• {article.title}：{failure}"
        existing = (job.error_message or "").strip()
        job.error_message = (
            f"{existing}\n{detail}" if existing else detail
        )[:8000]
    task.completed_at = now
    task.lease_expires_at = None
    job.completed_count += 1
    if job.completed_count >= job.total_count:
        job.status = ArticleJobStatus.completed
        job.current_item = None
        job.completed_at = now
        if job.failed_count:
            details = (job.error_message or "").strip()
            job.error_message = (
                f"任务完成，其中 {job.failed_count} 篇文章失败。失败明细：\n{details}"
            )[:8000]
        else:
            job.error_message = None
    else:
        job.current_item = "等待 Windows 本地发布客户端领取下一篇文章"
    await db.commit()
    return Response(status_code=204)
