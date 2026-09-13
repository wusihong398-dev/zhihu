import hashlib
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.answer_job import AnswerJob, AnswerJobStatus
from app.models.local_publisher import LocalAnswerPublishTask


def create_device_token() -> tuple[str, str]:
    token = f"tpd_{secrets.token_urlsafe(32)}"
    return token, hash_device_token(token)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def enqueue_local_answer_tasks(
    job: AnswerJob,
    answer_ids: list[uuid.UUID],
    db: AsyncSession,
) -> None:
    await db.flush()
    for answer_id in answer_ids:
        db.add(
            LocalAnswerPublishTask(
                job_id=job.id,
                user_id=job.user_id,
                account_id=job.account_id,
                answer_id=answer_id,
            )
        )
    job.status = AnswerJobStatus.pending
    job.current_item = "等待 Windows 本地发布客户端领取任务"


async def job_has_local_tasks(job_id: uuid.UUID, db: AsyncSession) -> bool:
    from sqlalchemy import func, select

    count = await db.scalar(
        select(func.count(LocalAnswerPublishTask.id)).where(
            LocalAnswerPublishTask.job_id == job_id
        )
    )
    return bool(count)

