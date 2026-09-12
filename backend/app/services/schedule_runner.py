import asyncio
import json
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.account import ZhihuAccount
from app.models.article import Article, ArticleStatus
from app.models.keyword import AccountKeyword
from app.models.keyword_folder import KeywordFolderItem
from app.models.question import QuestionStatus, ZhihuQuestion
from app.models.schedule import OperationSchedule, ScheduleRunStatus, ScheduleTaskType
from app.models.user import User
from app.schemas.article import (
    DEFAULT_CONTENT_PROMPT,
    DEFAULT_TITLE_PROMPT,
    ArticleGenerateRequest,
    ArticlePublishJobCreate,
)
from app.schemas.keyword import KeywordJobCreate
from app.schemas.qa import (
    DEFAULT_ANSWER_PROMPT,
    AnswerGenerateRequest,
    QuestionCollectRequest,
)


_loop_task: asyncio.Task | None = None
_running: set[uuid.UUID] = set()


def calculate_next_run(
    schedule: OperationSchedule, now: datetime | None = None
) -> datetime:
    try:
        timezone = ZoneInfo(schedule_timezone(schedule))
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Asia/Shanghai")
    current = (now or datetime.now(UTC)).astimezone(timezone)
    weekdays = {int(item) for item in schedule.weekdays.split(",") if item.strip().isdigit()}
    weekdays = weekdays or set(range(7))
    for offset in range(8):
        day = current.date() + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        candidate = datetime.combine(
            day, time(schedule.hour, schedule.minute), tzinfo=timezone
        )
        if candidate > current:
            return candidate.astimezone(UTC)
    return (current + timedelta(days=1)).astimezone(UTC)


def schedule_timezone(schedule: OperationSchedule) -> str:
    value = getattr(schedule, "_account_timezone", None)
    return value or "Asia/Shanghai"


def _account_day_start(account: ZhihuAccount) -> datetime:
    try:
        timezone = ZoneInfo(account.timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Asia/Shanghai")
    local = datetime.now(timezone)
    return datetime.combine(local.date(), time.min, tzinfo=timezone).astimezone(UTC)


async def _keyword_ids(account_id: uuid.UUID, config: dict, db) -> list[uuid.UUID]:
    limit = min(50, max(1, int(config.get("batch_size", 10))))
    query = select(AccountKeyword.id).where(AccountKeyword.account_id == account_id)
    folder_id = config.get("folder_id")
    if folder_id:
        query = query.join(
            KeywordFolderItem, KeywordFolderItem.keyword_id == AccountKeyword.id
        ).where(KeywordFolderItem.folder_id == uuid.UUID(folder_id))
    return list((await db.execute(query.order_by(AccountKeyword.created_at.asc()).limit(limit))).scalars())


async def _question_ids(account_id: uuid.UUID, config: dict, db) -> list[uuid.UUID]:
    limit = min(50, max(1, int(config.get("batch_size", 10))))
    return list(
        (
            await db.execute(
                select(ZhihuQuestion.id)
                .where(
                    ZhihuQuestion.account_id == account_id,
                    ZhihuQuestion.status == QuestionStatus.collected,
                )
                .order_by(ZhihuQuestion.discovered_at.asc())
                .limit(limit)
            )
        ).scalars()
    )


async def _dispatch(schedule_id: uuid.UUID) -> tuple[str, str | None]:
    async with SessionLocal() as db:
        schedule = await db.get(OperationSchedule, schedule_id)
        if schedule is None:
            raise RuntimeError("计划已删除")
        user = await db.get(User, schedule.user_id)
        account = await db.get(ZhihuAccount, schedule.account_id)
        if user is None or not user.is_active:
            raise RuntimeError("所属系统用户已停用")
        if account is None or not account.enabled:
            raise RuntimeError("知乎账号不存在或已停用")
        config = json.loads(schedule.config_json or "{}")

        if schedule.task_type == ScheduleTaskType.keyword_collect:
            from app.api.keywords import create_keyword_job

            background = BackgroundTasks()
            result = await create_keyword_job(
                account.id,
                KeywordJobCreate(
                    seed_keyword=str(config.get("seed_keyword", "")).strip(),
                    source=config.get("source", "both"),
                    target_count=int(config.get("target_count", 50)),
                    folder_id=config.get("folder_id") or None,
                ),
                background,
                db,
            )
            asyncio.create_task(background())
            return f"关键词采集任务已创建：{result.seed_keyword}", str(result.id)

        if schedule.task_type == ScheduleTaskType.article_generate:
            from app.api.articles import create_generation_job

            keyword_ids = await _keyword_ids(account.id, config, db)
            if not keyword_ids:
                raise RuntimeError("指定关键词库没有可用关键词")
            result = await create_generation_job(
                account.id,
                ArticleGenerateRequest(
                    keyword_ids=keyword_ids,
                    product_id=config.get("product_id"),
                    provider=str(config.get("provider", "")),
                    model=config.get("model") or None,
                    articles_per_keyword=int(config.get("articles_per_keyword", 1)),
                    min_length=int(config.get("min_length", 800)),
                    max_length=int(config.get("max_length", 1500)),
                    title_prompt=str(config.get("title_prompt") or DEFAULT_TITLE_PROMPT),
                    content_prompt=str(config.get("content_prompt") or DEFAULT_CONTENT_PROMPT),
                    output_mode=config.get("output_mode", "draft"),
                ),
                db,
                user,
            )
            return f"文章生成任务已创建，共 {result.total_count} 篇", str(result.id)

        if schedule.task_type == ScheduleTaskType.article_publish:
            from app.api.articles import create_publish_job

            attempted = await db.scalar(
                select(func.count(Article.id)).where(
                    Article.account_id == account.id,
                    Article.publish_attempted_at >= _account_day_start(account),
                )
            ) or 0
            limit = min(
                max(0, account.daily_article_limit - attempted),
                max(1, int(config.get("batch_size", account.daily_article_limit or 1))),
            )
            ids = list(
                (
                    await db.execute(
                        select(Article.id)
                        .where(Article.account_id == account.id, Article.status == ArticleStatus.ready)
                        .order_by(Article.created_at.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            if not ids:
                raise RuntimeError("没有待发布文章或今日额度已用完")
            result = await create_publish_job(ArticlePublishJobCreate(article_ids=ids), db, user)
            return f"文章发布任务已创建，共 {result.total_count} 篇", str(result.id)

        if schedule.task_type == ScheduleTaskType.question_collect:
            from app.api.qa import collect_questions

            keywords = config.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [item.strip() for item in keywords.splitlines() if item.strip()]
            result = await collect_questions(
                account.id,
                QuestionCollectRequest(
                    keywords=keywords, target_count=int(config.get("target_count", 20))
                ),
                db,
                user,
            )
            return f"问题采集完成，新增 {result.added_count} 个", None

        if schedule.task_type == ScheduleTaskType.answer_generate:
            from app.api.qa import create_answer_generation_job

            question_ids = await _question_ids(account.id, config, db)
            if not question_ids:
                raise RuntimeError("问题库没有待处理问题")
            result = await create_answer_generation_job(
                account.id,
                AnswerGenerateRequest(
                    question_ids=question_ids,
                    product_id=config.get("product_id"),
                    provider=str(config.get("provider", "")),
                    model=config.get("model") or None,
                    min_length=int(config.get("min_length", 300)),
                    max_length=int(config.get("max_length", 800)),
                    prompt=str(config.get("prompt") or DEFAULT_ANSWER_PROMPT),
                    ready_after_generate=bool(config.get("ready_after_generate", True)),
                ),
                db,
                user,
            )
            return f"回答生成任务已创建，共 {result.total_count} 个", str(result.id)

        from app.api.qa import run_auto_answer

        result = await run_auto_answer(account.id, db, user)
        if result.job is None:
            raise RuntimeError("没有待发布回答或今日额度已用完")
        return f"回答发布任务已创建，共 {result.queued_count} 个", str(result.job.id)


async def run_schedule(schedule_id: uuid.UUID, manual: bool = False) -> None:
    if schedule_id in _running:
        return
    _running.add(schedule_id)
    try:
        async with SessionLocal() as db:
            schedule = await db.get(OperationSchedule, schedule_id)
            if schedule is None or (not manual and not schedule.enabled):
                return
            account = await db.get(ZhihuAccount, schedule.account_id)
            if account:
                schedule._account_timezone = account.timezone
            schedule.last_status = ScheduleRunStatus.running
            schedule.last_started_at = datetime.now(UTC)
            schedule.last_message = "正在执行"
            schedule.next_run_at = calculate_next_run(schedule)
            await db.commit()
        try:
            message, reference_id = await _dispatch(schedule_id)
            status = ScheduleRunStatus.success
        except HTTPException as exc:
            message = str(exc.detail)
            reference_id = None
            status = ScheduleRunStatus.skipped if exc.status_code == 409 else ScheduleRunStatus.failed
        except Exception as exc:
            message = str(exc)
            reference_id = None
            status = ScheduleRunStatus.failed
        async with SessionLocal() as db:
            schedule = await db.get(OperationSchedule, schedule_id)
            if schedule:
                schedule.last_status = status
                schedule.last_message = message[:2000]
                schedule.last_reference_id = reference_id
                schedule.last_completed_at = datetime.now(UTC)
                await db.commit()
    finally:
        _running.discard(schedule_id)


async def _scheduler_loop() -> None:
    while True:
        try:
            now = datetime.now(UTC)
            async with SessionLocal() as db:
                ids = list(
                    (
                        await db.execute(
                            select(OperationSchedule.id).where(
                                OperationSchedule.enabled.is_(True),
                                OperationSchedule.next_run_at.is_not(None),
                                OperationSchedule.next_run_at <= now,
                            )
                        )
                    ).scalars()
                )
            for schedule_id in ids:
                asyncio.create_task(run_schedule(schedule_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(20)


async def start_scheduler() -> None:
    global _loop_task
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(_scheduler_loop())


async def close_scheduler() -> None:
    global _loop_task
    if _loop_task:
        _loop_task.cancel()
        await asyncio.gather(_loop_task, return_exceptions=True)
        _loop_task = None
