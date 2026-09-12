import csv
import io
import json
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user, require_admin
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.article_job import ArticleJob
from app.models.answer_job import AnswerJob
from app.models.keyword_job import KeywordCollectionJob
from app.models.schedule import OperationSchedule
from app.models.system_setting import SystemSetting
from app.models.user import User, UserRole
from app.schemas.operations import (
    OperationLogList,
    OperationLogRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    SystemSettingRead,
    SystemSettingUpdate,
)
from app.services.access_control import get_account_for_user
from app.services.schedule_runner import calculate_next_run, run_schedule


router = APIRouter(tags=["operations"])


def _parse_json(value: str) -> dict:
    try:
        result = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def _schedule_read(schedule: OperationSchedule, account_name: str) -> ScheduleRead:
    return ScheduleRead(
        id=schedule.id,
        user_id=schedule.user_id,
        account_id=schedule.account_id,
        account_name=account_name,
        name=schedule.name,
        task_type=schedule.task_type,
        enabled=schedule.enabled,
        hour=schedule.hour,
        minute=schedule.minute,
        weekdays=[int(item) for item in schedule.weekdays.split(",") if item],
        config=_parse_json(schedule.config_json),
        next_run_at=schedule.next_run_at,
        last_started_at=schedule.last_started_at,
        last_completed_at=schedule.last_completed_at,
        last_status=schedule.last_status,
        last_message=schedule.last_message,
        last_reference_id=schedule.last_reference_id,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _validate_config(task_type: str, config: dict) -> None:
    required = {
        "keyword_collect": ("seed_keyword",),
        "article_generate": ("product_id", "provider"),
        "article_publish": (),
        "question_collect": ("keywords",),
        "answer_generate": ("product_id", "provider"),
        "answer_publish": (),
    }[task_type]
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise HTTPException(status_code=422, detail=f"计划配置缺少：{', '.join(missing)}")


@router.get("/schedules", response_model=list[ScheduleRead])
async def list_schedules(
    account_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> list[ScheduleRead]:
    query = select(OperationSchedule, ZhihuAccount.display_name).join(
        ZhihuAccount, ZhihuAccount.id == OperationSchedule.account_id
    )
    if user.role != UserRole.admin:
        query = query.where(OperationSchedule.user_id == user.id)
    if account_id:
        await get_account_for_user(account_id, user, db)
        query = query.where(OperationSchedule.account_id == account_id)
    rows = (await db.execute(query.order_by(OperationSchedule.created_at.desc()))).all()
    return [_schedule_read(schedule, account_name) for schedule, account_name in rows]


@router.post("/schedules", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ScheduleRead:
    account = await get_account_for_user(payload.account_id, user, db)
    _validate_config(payload.task_type.value, payload.config)
    try:
        ZoneInfo(account.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="账号时区无效，请先修改知乎账号") from exc
    schedule = OperationSchedule(
        user_id=account.owner_user_id or user.id,
        account_id=account.id,
        name=" ".join(payload.name.split()),
        task_type=payload.task_type,
        enabled=payload.enabled,
        hour=payload.hour,
        minute=payload.minute,
        weekdays=",".join(str(value) for value in payload.weekdays),
        config_json=json.dumps(payload.config, ensure_ascii=False),
    )
    schedule._account_timezone = account.timezone
    schedule.next_run_at = calculate_next_run(schedule) if schedule.enabled else None
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return _schedule_read(schedule, account.display_name)


async def _owned_schedule(
    schedule_id: uuid.UUID, user: User, db: AsyncSession
) -> tuple[OperationSchedule, ZhihuAccount]:
    schedule = await db.get(OperationSchedule, schedule_id)
    if schedule is None or (user.role != UserRole.admin and schedule.user_id != user.id):
        raise HTTPException(status_code=404, detail="定时计划不存在")
    account = await db.get(ZhihuAccount, schedule.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="知乎账号不存在")
    return schedule, account


@router.patch("/schedules/{schedule_id}", response_model=ScheduleRead)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ScheduleRead:
    schedule, account = await _owned_schedule(schedule_id, user, db)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        schedule.name = " ".join(data.pop("name").split())
    if "weekdays" in data:
        schedule.weekdays = ",".join(str(value) for value in data.pop("weekdays"))
    if "config" in data:
        config = data.pop("config")
        _validate_config(schedule.task_type.value, config)
        schedule.config_json = json.dumps(config, ensure_ascii=False)
    for key, value in data.items():
        setattr(schedule, key, value)
    schedule._account_timezone = account.timezone
    schedule.next_run_at = calculate_next_run(schedule) if schedule.enabled else None
    await db.commit()
    await db.refresh(schedule)
    return _schedule_read(schedule, account.display_name)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    schedule, _ = await _owned_schedule(schedule_id, user, db)
    await db.delete(schedule)
    await db.commit()


@router.post("/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_schedule_now(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> dict[str, str]:
    await _owned_schedule(schedule_id, user, db)
    import asyncio

    asyncio.create_task(run_schedule(schedule_id, manual=True))
    return {"status": "started"}


async def _operation_logs(user: User, db: AsyncSession) -> list[OperationLogRead]:
    settings = await _settings(db)
    account_query = select(ZhihuAccount.id, ZhihuAccount.display_name)
    if user.role != UserRole.admin:
        account_query = account_query.where(ZhihuAccount.owner_user_id == user.id)
    accounts = dict((await db.execute(account_query)).all())
    if not accounts:
        return []
    account_ids = set(accounts)
    items: list[OperationLogRead] = []
    keyword_jobs = list((await db.execute(select(KeywordCollectionJob).where(KeywordCollectionJob.account_id.in_(account_ids)))).scalars())
    for job in keyword_jobs:
        items.append(OperationLogRead(id=str(job.id), account_id=job.account_id, account_name=accounts[job.account_id], category="keyword", task_name=f"关键词采集：{job.seed_keyword}", status=job.status.value, total_count=job.target_count, success_count=job.collected_count, failed_count=0 if job.status.value in {"completed", "running", "pending"} else 1, message=job.error_message, started_at=job.created_at, completed_at=job.completed_at))
    article_jobs = list((await db.execute(select(ArticleJob).where(ArticleJob.account_id.in_(account_ids)))).scalars())
    for job in article_jobs:
        items.append(OperationLogRead(id=str(job.id), account_id=job.account_id, account_name=accounts.get(job.account_id, ""), category="article", task_name="文章生成" if job.job_type.value == "generate" else "文章发布", status=job.status.value, total_count=job.total_count, success_count=job.success_count, failed_count=job.failed_count, message=job.error_message, started_at=job.created_at, completed_at=job.completed_at))
    answer_jobs = list((await db.execute(select(AnswerJob).where(AnswerJob.account_id.in_(account_ids)))).scalars())
    for job in answer_jobs:
        items.append(OperationLogRead(id=str(job.id), account_id=job.account_id, account_name=accounts.get(job.account_id, ""), category="answer", task_name="回答生成" if job.job_type.value == "generate" else "回答发布", status=job.status.value, total_count=job.total_count, success_count=job.success_count, failed_count=job.failed_count, message=job.error_message, started_at=job.created_at, completed_at=job.completed_at))
    schedules = list((await db.execute(select(OperationSchedule).where(OperationSchedule.account_id.in_(account_ids), OperationSchedule.last_started_at.is_not(None)))).scalars())
    for item in schedules:
        items.append(OperationLogRead(id=f"schedule-{item.id}", account_id=item.account_id, account_name=accounts[item.account_id], category="schedule", task_name=f"定时计划：{item.name}", status=item.last_status.value, total_count=1, success_count=1 if item.last_status.value == "success" else 0, failed_count=1 if item.last_status.value == "failed" else 0, message=item.last_message, started_at=item.last_started_at, completed_at=item.last_completed_at))
    cutoff = datetime.now(UTC).timestamp() - settings.log_retention_days * 86400
    items = [
        item
        for item in items
        if item.started_at.replace(tzinfo=item.started_at.tzinfo or UTC).timestamp() >= cutoff
    ]
    return sorted(items, key=lambda item: item.started_at, reverse=True)


@router.get("/operation-logs", response_model=OperationLogList)
async def list_operation_logs(
    account_id: uuid.UUID | None = None,
    category: str = Query(default="", max_length=32),
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> OperationLogList:
    items = await _operation_logs(user, db)
    if account_id:
        await get_account_for_user(account_id, user, db)
        items = [item for item in items if item.account_id == account_id]
    if category:
        items = [item for item in items if item.category == category]
    if q.strip():
        needle = q.strip().casefold()
        items = [item for item in items if needle in f"{item.task_name} {item.message or ''}".casefold()]
    return OperationLogList(items=items[offset : offset + limit], total=len(items))


@router.get("/operation-logs/export")
async def export_operation_logs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> Response:
    items = await _operation_logs(user, db)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["开始时间", "完成时间", "知乎账号", "任务", "状态", "总数", "成功", "失败", "说明"])
    for item in items:
        writer.writerow([item.started_at.isoformat(), item.completed_at.isoformat() if item.completed_at else "", item.account_name, item.task_name, item.status, item.total_count, item.success_count, item.failed_count, item.message or ""])
    return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="totod-logs-{datetime.now(UTC).date()}.csv"'})


async def _settings(db: AsyncSession) -> SystemSetting:
    value = await db.get(SystemSetting, 1)
    if value is None:
        value = SystemSetting(id=1)
        db.add(value)
        await db.commit()
        await db.refresh(value)
    return value


@router.get("/system-settings", response_model=SystemSettingRead, dependencies=[Depends(require_admin)])
async def get_system_settings(db: AsyncSession = Depends(get_db)) -> SystemSetting:
    return await _settings(db)


@router.put("/system-settings", response_model=SystemSettingRead, dependencies=[Depends(require_admin)])
async def update_system_settings(payload: SystemSettingUpdate, db: AsyncSession = Depends(get_db)) -> SystemSetting:
    try:
        ZoneInfo(payload.default_timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="默认时区无效") from exc
    value = await _settings(db)
    for key, item in payload.model_dump().items():
        setattr(value, key, item)
    await db.commit()
    await db.refresh(value)
    return value
