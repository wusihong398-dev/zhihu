import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user, user_is_expired
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.answer import AnswerStatus, ZhihuAnswer
from app.models.answer_job import AnswerJob, AnswerJobStatus
from app.models.local_publisher import LocalAnswerPublishTask, LocalPublisherDevice
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
    owner_filter = (
        ZhihuAccount.owner_user_id.is_(None)
        if user and user.role == UserRole.admin
        else ZhihuAccount.owner_user_id == device.user_id
    )
    accounts = list(
        (
            await db.execute(
                select(ZhihuAccount).where(
                    owner_filter,
                    ZhihuAccount.enabled.is_(True),
                    ZhihuAccount.answer_publish_mode == "local",
                )
            )
        ).scalars()
    )
    return [
        LocalPublisherAccountRead(
            id=item.id,
            display_name=item.display_name,
            remark=item.remark,
            timezone=item.timezone,
        )
        for item in accounts
    ]


@router.post("/client/tasks/claim", response_model=LocalPublisherTaskRead | None)
async def claim_task(
    account_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    device: LocalPublisherDevice = Depends(require_local_device),
) -> LocalPublisherTaskRead | None:
    now = datetime.now(UTC)
    await db.execute(
        update(LocalAnswerPublishTask)
        .where(
            LocalAnswerPublishTask.status == "leased",
            LocalAnswerPublishTask.lease_expires_at < now,
        )
        .values(status="queued", leased_device_id=None, lease_expires_at=None)
    )
    task_query = (
        select(LocalAnswerPublishTask)
        .join(AnswerJob, AnswerJob.id == LocalAnswerPublishTask.job_id)
        .where(
            LocalAnswerPublishTask.user_id == device.user_id,
            LocalAnswerPublishTask.status == "queued",
            AnswerJob.status.in_([AnswerJobStatus.pending, AnswerJobStatus.running]),
        )
    )
    if account_id is not None:
        task_query = task_query.where(LocalAnswerPublishTask.account_id == account_id)
    task = await db.scalar(
        task_query.order_by(LocalAnswerPublishTask.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task is None:
        await db.commit()
        return None
    account = await db.get(ZhihuAccount, task.account_id)
    answer = await db.get(ZhihuAnswer, task.answer_id)
    job = await db.get(AnswerJob, task.job_id)
    if (
        account is None
        or answer is None
        or job is None
        or not account.enabled
        or account.answer_publish_mode != "local"
    ):
        task.status = "failed"
        task.error_message = "账号或回答不存在，或账号已切换发布方式"
        task.completed_at = now
        await db.commit()
        return None
    expires = now + timedelta(seconds=LEASE_SECONDS)
    task.status = "leased"
    task.leased_device_id = device.id
    task.lease_expires_at = expires
    task.attempt_count += 1
    job.status = AnswerJobStatus.running
    job.current_item = f"本地发布：{answer.question_title}"
    await db.commit()
    return LocalPublisherTaskRead(
        id=task.id,
        job_id=task.job_id,
        answer_id=task.answer_id,
        account_id=task.account_id,
        account_name=account.display_name,
        question_title=answer.question_title,
        question_url=answer.question_url,
        content=answer.content,
        attempt_count=task.attempt_count,
        lease_expires_at=expires,
    )


async def _leased_task(
    task_id: uuid.UUID, device: LocalPublisherDevice, db: AsyncSession
) -> LocalAnswerPublishTask:
    task = await db.get(LocalAnswerPublishTask, task_id)
    if (
        task is None
        or task.user_id != device.user_id
        or task.leased_device_id != device.id
        or task.status != "leased"
    ):
        raise HTTPException(status_code=409, detail="任务租约无效或已结束")
    return task


@router.post(
    "/client/tasks/{task_id}/heartbeat",
    response_model=LocalPublisherHeartbeatRead,
)
async def heartbeat_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    device: LocalPublisherDevice = Depends(require_local_device),
) -> LocalPublisherHeartbeatRead:
    task = await _leased_task(task_id, device, db)
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
    task = await _leased_task(task_id, device, db)
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
