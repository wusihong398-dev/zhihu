import json
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.account import AccountStatus, ZhihuAccount
from app.models.answer import AnswerStatus, ZhihuAnswer
from app.models.answer_job import AnswerJob, AnswerJobStatus, AnswerJobType
from app.models.answer_prompt import AnswerPromptFolder, AnswerPromptTemplate
from app.models.product import PromotedProduct
from app.models.question import ZhihuQuestion
from app.models.user import User
from app.models.user_ai_provider import UserAIProviderConfig
from app.schemas.qa import (
    AnswerBulkRequest,
    AnswerBulkResult,
    AnswerCreate,
    AnswerGenerateRequest,
    AnswerJobRead,
    AnswerListResponse,
    AnswerPromptTemplateCreate,
    AnswerPromptTemplateListResponse,
    AnswerPromptTemplateRead,
    AnswerPromptTemplateUpdate,
    AnswerPublishJobCreate,
    AnswerRead,
    AnswerUpdate,
    AutoAnswerRunResponse,
    AutoAnswerSummary,
    QuestionBulkRequest,
    QuestionCollectRequest,
    QuestionCollectResponse,
    QuestionListResponse,
    QuestionRead,
)
from app.schemas.article_prompt import (
    PromptFolderCreate,
    PromptFolderRead,
    PromptFolderUpdate,
)
from app.services.access_control import get_account_for_user
from app.services.ai_providers import PROVIDERS
from app.services.answer_jobs import answer_content_length, start_answer_job
from app.services.secret_box import decrypt_secret
from app.services.zhihu_question_collector import (
    ZhihuQuestionCollectionError,
    ZhihuQuestionLoginRequired,
    collect_zhihu_questions,
)


router = APIRouter(tags=["qa operations"], dependencies=[Depends(require_active_user)])


def _clean_prompt_template_name(value: str) -> tuple[str, str]:
    name = " ".join(value.split())
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    return name, name.casefold()


def _clean_answer_prompt(value: str) -> str:
    prompt = value.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="回答提示词不能为空")
    return prompt


async def _answer_prompt_template_for_user(
    template_id: uuid.UUID, user: User, db: AsyncSession
) -> AnswerPromptTemplate:
    template = await db.get(AnswerPromptTemplate, template_id)
    if template is None or template.user_id != user.id:
        raise HTTPException(status_code=404, detail="回答提示词模板不存在")
    return template


async def _answer_prompt_folder_for_user(
    folder_id: uuid.UUID, user: User, db: AsyncSession
) -> AnswerPromptFolder:
    folder = await db.get(AnswerPromptFolder, folder_id)
    if folder is None or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="问答提示词文件夹不存在")
    return folder


async def _resolve_answer_prompt_folder(
    folder_id: uuid.UUID | None,
    folder_name: str | None,
    user: User,
    db: AsyncSession,
) -> uuid.UUID | None:
    if folder_id is not None:
        return (await _answer_prompt_folder_for_user(folder_id, user, db)).id
    if folder_name and folder_name.strip():
        name, normalized_name = _clean_prompt_template_name(folder_name)
        folder = await db.scalar(
            select(AnswerPromptFolder).where(
                AnswerPromptFolder.user_id == user.id,
                AnswerPromptFolder.normalized_name == normalized_name,
            )
        )
        if folder is None:
            folder = AnswerPromptFolder(
                user_id=user.id, name=name, normalized_name=normalized_name
            )
            db.add(folder)
            await db.flush()
        return folder.id
    return None


def _answer_prompt_template_read(
    template: AnswerPromptTemplate, folder_name: str | None = None
) -> AnswerPromptTemplateRead:
    return AnswerPromptTemplateRead.model_validate(template).model_copy(
        update={"folder_name": folder_name}
    )


@router.get("/answer-prompt-folders", response_model=list[PromptFolderRead])
async def list_answer_prompt_folders(
    db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)
) -> list[PromptFolderRead]:
    counts = (
        select(
            AnswerPromptTemplate.folder_id,
            func.count(AnswerPromptTemplate.id).label("template_count"),
        )
        .where(AnswerPromptTemplate.user_id == user.id)
        .group_by(AnswerPromptTemplate.folder_id)
        .subquery()
    )
    result = await db.execute(
        select(
            AnswerPromptFolder,
            func.coalesce(counts.c.template_count, 0),
        )
        .outerjoin(counts, counts.c.folder_id == AnswerPromptFolder.id)
        .where(AnswerPromptFolder.user_id == user.id)
        .order_by(AnswerPromptFolder.name.asc())
    )
    return [
        PromptFolderRead.model_validate(folder).model_copy(
            update={"template_count": template_count}
        )
        for folder, template_count in result
    ]


@router.post(
    "/answer-prompt-folders",
    response_model=PromptFolderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_answer_prompt_folder(
    payload: PromptFolderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptFolderRead:
    name, normalized_name = _clean_prompt_template_name(payload.name)
    folder = AnswerPromptFolder(
        user_id=user.id, name=name, normalized_name=normalized_name
    )
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="同名问答提示词文件夹已存在"
        ) from exc
    await db.refresh(folder)
    return PromptFolderRead.model_validate(folder)


@router.patch(
    "/answer-prompt-folders/{folder_id}", response_model=PromptFolderRead
)
async def update_answer_prompt_folder(
    folder_id: uuid.UUID,
    payload: PromptFolderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> PromptFolderRead:
    folder = await _answer_prompt_folder_for_user(folder_id, user, db)
    folder.name, folder.normalized_name = _clean_prompt_template_name(payload.name)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="同名问答提示词文件夹已存在"
        ) from exc
    await db.refresh(folder)
    template_count = await db.scalar(
        select(func.count(AnswerPromptTemplate.id)).where(
            AnswerPromptTemplate.folder_id == folder.id,
            AnswerPromptTemplate.user_id == user.id,
        )
    )
    return PromptFolderRead.model_validate(folder).model_copy(
        update={"template_count": template_count or 0}
    )


@router.delete(
    "/answer-prompt-folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_answer_prompt_folder(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    folder = await _answer_prompt_folder_for_user(folder_id, user, db)
    await db.delete(folder)
    await db.commit()


@router.get(
    "/answer-prompt-templates", response_model=AnswerPromptTemplateListResponse
)
async def list_answer_prompt_templates(
    folder_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerPromptTemplateListResponse:
    filters = [AnswerPromptTemplate.user_id == user.id]
    if folder_id is not None:
        await _answer_prompt_folder_for_user(folder_id, user, db)
        filters.append(AnswerPromptTemplate.folder_id == folder_id)
    result = await db.execute(
        select(AnswerPromptTemplate, AnswerPromptFolder.name)
        .outerjoin(
            AnswerPromptFolder,
            AnswerPromptFolder.id == AnswerPromptTemplate.folder_id,
        )
        .where(*filters)
        .order_by(
            AnswerPromptTemplate.updated_at.desc(),
            AnswerPromptTemplate.name.asc(),
        )
    )
    items = [
        _answer_prompt_template_read(template, folder_name)
        for template, folder_name in result
    ]
    return AnswerPromptTemplateListResponse(items=items, total=len(items))


@router.post(
    "/answer-prompt-templates",
    response_model=AnswerPromptTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_answer_prompt_template(
    payload: AnswerPromptTemplateCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerPromptTemplateRead:
    name, normalized_name = _clean_prompt_template_name(payload.name)
    folder_id = await _resolve_answer_prompt_folder(
        payload.folder_id, payload.folder_name, user, db
    )
    template = AnswerPromptTemplate(
        user_id=user.id,
        folder_id=folder_id,
        name=name,
        normalized_name=normalized_name,
        prompt=_clean_answer_prompt(payload.prompt),
    )
    db.add(template)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="同名回答提示词模板已存在"
        ) from exc
    await db.refresh(template)
    folder_name = None
    if template.folder_id:
        folder_name = (await db.get(AnswerPromptFolder, template.folder_id)).name
    return _answer_prompt_template_read(template, folder_name)


@router.patch(
    "/answer-prompt-templates/{template_id}",
    response_model=AnswerPromptTemplateRead,
)
async def update_answer_prompt_template(
    template_id: uuid.UUID,
    payload: AnswerPromptTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerPromptTemplateRead:
    template = await _answer_prompt_template_for_user(template_id, user, db)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        template.name, template.normalized_name = _clean_prompt_template_name(
            changes["name"]
        )
    if "folder_id" in changes or "folder_name" in changes:
        template.folder_id = await _resolve_answer_prompt_folder(
            changes.get("folder_id"), changes.get("folder_name"), user, db
        )
    if "prompt" in changes:
        template.prompt = _clean_answer_prompt(changes["prompt"])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="同名回答提示词模板已存在"
        ) from exc
    await db.refresh(template)
    folder_name = None
    if template.folder_id:
        folder_name = (await db.get(AnswerPromptFolder, template.folder_id)).name
    return _answer_prompt_template_read(template, folder_name)


@router.delete(
    "/answer-prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_answer_prompt_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    template = await _answer_prompt_template_for_user(template_id, user, db)
    await db.delete(template)
    await db.commit()


def _job_read(job: AnswerJob) -> AnswerJobRead:
    percent = 0 if not job.total_count else min(100, round(job.completed_count * 100 / job.total_count))
    return AnswerJobRead.model_validate(job).model_copy(update={"progress_percent": percent})


def _answer_read(answer: ZhihuAnswer, account_name: str = "") -> AnswerRead:
    return AnswerRead.model_validate(answer).model_copy(update={"account_name": account_name})


async def _answer_for_account(
    account_id: uuid.UUID, answer_id: uuid.UUID, db: AsyncSession
) -> ZhihuAnswer:
    answer = await db.scalar(
        select(ZhihuAnswer).where(
            ZhihuAnswer.id == answer_id, ZhihuAnswer.account_id == account_id
        )
    )
    if answer is None:
        raise HTTPException(status_code=404, detail="回答不存在")
    return answer


def _account_day_bounds(account: ZhihuAccount) -> tuple[datetime, datetime]:
    try:
        timezone = ZoneInfo(account.timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(timezone)
    start_local = datetime.combine(now.date(), time.min, tzinfo=timezone)
    return start_local.astimezone(UTC), (start_local + timedelta(days=1)).astimezone(UTC)


async def _quota_used_today(account: ZhihuAccount, db: AsyncSession) -> int:
    """Count only answers that Zhihu confirmed as successfully published.

    ``publish_attempted_at`` is retained for diagnostics and display, but a failed
    attempt must not consume the account's daily answer quota.
    """
    start, end = _account_day_bounds(account)
    return (
        await db.scalar(
            select(func.count(ZhihuAnswer.id)).where(
                ZhihuAnswer.account_id == account.id,
                ZhihuAnswer.status == AnswerStatus.published,
                ZhihuAnswer.published_at >= start,
                ZhihuAnswer.published_at < end,
            )
        )
        or 0
    )


@router.post(
    "/accounts/{account_id}/questions/collect",
    response_model=QuestionCollectResponse,
)
async def collect_questions(
    account_id: uuid.UUID,
    payload: QuestionCollectRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> QuestionCollectResponse:
    account = await get_account_for_user(account_id, user, db)
    if not account.enabled:
        raise HTTPException(status_code=400, detail="知乎账号已停用")
    try:
        candidates = await collect_zhihu_questions(account, payload.keywords, payload.target_count)
    except ZhihuQuestionLoginRequired as exc:
        account.status = AccountStatus.offline
        await db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ZhihuQuestionCollectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    remote_ids = [item.question_id for item in candidates]
    existing = set(
        (
            await db.execute(
                select(ZhihuQuestion.zhihu_question_id).where(
                    ZhihuQuestion.account_id == account_id,
                    ZhihuQuestion.zhihu_question_id.in_(remote_ids),
                )
            )
        ).scalars()
    )
    added: list[ZhihuQuestion] = []
    for item in candidates:
        if item.question_id in existing:
            continue
        question = ZhihuQuestion(
            account_id=account_id,
            zhihu_question_id=item.question_id,
            title=item.title,
            url=item.url,
            keyword_text=item.keyword,
            excerpt=item.excerpt,
            answer_count=item.answer_count,
            follower_count=item.follower_count,
        )
        db.add(question)
        added.append(question)
    account.status = AccountStatus.online
    await db.commit()
    for item in added:
        await db.refresh(item)
    return QuestionCollectResponse(
        scanned_count=len(candidates),
        added_count=len(added),
        duplicate_count=len(candidates) - len(added),
        items=[QuestionRead.model_validate(item) for item in added],
    )


@router.get("/accounts/{account_id}/questions", response_model=QuestionListResponse)
async def list_questions(
    account_id: uuid.UUID,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> QuestionListResponse:
    await get_account_for_user(account_id, user, db)
    filters = [ZhihuQuestion.account_id == account_id]
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(ZhihuQuestion.title.ilike(pattern), ZhihuQuestion.keyword_text.ilike(pattern))
        )
    total = await db.scalar(select(func.count(ZhihuQuestion.id)).where(*filters))
    items = list(
        (
            await db.execute(
                select(ZhihuQuestion)
                .where(*filters)
                .order_by(ZhihuQuestion.discovered_at.desc(), ZhihuQuestion.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    return QuestionListResponse(items=items, total=total or 0)


@router.post("/accounts/{account_id}/questions/bulk-delete", response_model=AnswerBulkResult)
async def delete_questions(
    account_id: uuid.UUID,
    payload: QuestionBulkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerBulkResult:
    await get_account_for_user(account_id, user, db)
    result = await db.execute(
        delete(ZhihuQuestion)
        .where(
            ZhihuQuestion.account_id == account_id,
            ZhihuQuestion.id.in_(set(payload.question_ids)),
        )
        .returning(ZhihuQuestion.id)
    )
    ids = list(result.scalars())
    await db.commit()
    return AnswerBulkResult(affected_count=len(ids))


@router.get("/accounts/{account_id}/answers", response_model=AnswerListResponse)
async def list_answers(
    account_id: uuid.UUID,
    q: str = Query(default="", max_length=255),
    answer_status: AnswerStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerListResponse:
    account = await get_account_for_user(account_id, user, db)
    filters = [ZhihuAnswer.account_id == account_id]
    if answer_status:
        filters.append(ZhihuAnswer.status == answer_status)
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                ZhihuAnswer.question_title.ilike(pattern),
                ZhihuAnswer.keyword_text.ilike(pattern),
                ZhihuAnswer.product_name.ilike(pattern),
            )
        )
    total = await db.scalar(select(func.count(ZhihuAnswer.id)).where(*filters))
    items = list(
        (
            await db.execute(
                select(ZhihuAnswer)
                .where(*filters)
                .order_by(ZhihuAnswer.created_at.desc(), ZhihuAnswer.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    return AnswerListResponse(
        items=[_answer_read(item, account.display_name) for item in items], total=total or 0
    )


@router.post(
    "/accounts/{account_id}/answers", response_model=AnswerRead, status_code=status.HTTP_201_CREATED
)
async def create_answer(
    account_id: uuid.UUID,
    payload: AnswerCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerRead:
    account = await get_account_for_user(account_id, user, db)
    question = await db.get(ZhihuQuestion, payload.question_id)
    if question is None or question.account_id != account_id:
        raise HTTPException(status_code=404, detail="问题不存在")
    product = await db.get(PromotedProduct, payload.product_id) if payload.product_id else None
    if payload.product_id and (product is None or product.account_id != account_id):
        raise HTTPException(status_code=404, detail="推广商品不存在")
    if payload.status == AnswerStatus.published:
        raise HTTPException(status_code=400, detail="只有知乎真实发布成功后才能标记为已发布")
    answer = ZhihuAnswer(
        account_id=account_id,
        question_id=question.id,
        product_id=product.id if product else None,
        question_title=question.title,
        question_url=question.url,
        keyword_text=question.keyword_text,
        product_name=product.name if product else "",
        content=payload.content,
        content_length=answer_content_length(payload.content),
        status=payload.status,
    )
    db.add(answer)
    await db.commit()
    await db.refresh(answer)
    return _answer_read(answer, account.display_name)


@router.post(
    "/accounts/{account_id}/answer-jobs/generate",
    response_model=AnswerJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_answer_generation_job(
    account_id: uuid.UUID,
    payload: AnswerGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerJobRead:
    await get_account_for_user(account_id, user, db)
    definition = PROVIDERS.get(payload.provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="AI 平台不存在")
    config = await db.scalar(
        select(UserAIProviderConfig).where(
            UserAIProviderConfig.user_id == user.id,
            UserAIProviderConfig.provider == payload.provider,
        )
    )
    if config is None or not config.enabled or not config.api_key_encrypted:
        raise HTTPException(status_code=400, detail="请先在 AI 配置中启用平台并保存 API Key")
    try:
        decrypt_secret(config.api_key_encrypted)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    model = (payload.model or config.model).strip()
    product = await db.get(PromotedProduct, payload.product_id)
    if product is None or product.account_id != account_id or not product.enabled:
        raise HTTPException(status_code=404, detail="启用的推广商品不存在")
    owned = set(
        (
            await db.execute(
                select(ZhihuQuestion.id).where(
                    ZhihuQuestion.account_id == account_id,
                    ZhihuQuestion.id.in_(set(payload.question_ids)),
                )
            )
        ).scalars()
    )
    if len(owned) != len(set(payload.question_ids)):
        raise HTTPException(status_code=404, detail="部分问题不存在或不属于当前账号")
    active = await db.scalar(
        select(func.count(AnswerJob.id)).where(
            AnswerJob.account_id == account_id,
            AnswerJob.job_type == AnswerJobType.generate,
            AnswerJob.status.in_([AnswerJobStatus.pending, AnswerJobStatus.running, AnswerJobStatus.paused]),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该账号已有未结束的回答生成任务")
    job_payload = payload.model_dump(mode="json")
    job_payload["model"] = model
    job = AnswerJob(
        user_id=user.id,
        account_id=account_id,
        job_type=AnswerJobType.generate,
        total_count=len(payload.question_ids),
        payload_json=json.dumps(job_payload, ensure_ascii=False),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    start_answer_job(job.id)
    return _job_read(job)


@router.get("/answer-jobs/latest", response_model=AnswerJobRead | None)
async def latest_answer_job(
    account_id: uuid.UUID,
    job_type: AnswerJobType = Query(alias="type"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerJobRead | None:
    await get_account_for_user(account_id, user, db)
    job = await db.scalar(
        select(AnswerJob)
        .where(
            AnswerJob.user_id == user.id,
            AnswerJob.account_id == account_id,
            AnswerJob.job_type == job_type,
        )
        .order_by(AnswerJob.created_at.desc(), AnswerJob.id.desc())
        .limit(1)
    )
    return _job_read(job) if job else None


@router.get("/answer-jobs/{job_id}", response_model=AnswerJobRead)
async def get_answer_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerJobRead:
    job = await db.get(AnswerJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="回答任务不存在")
    return _job_read(job)


async def _control_job(
    job_id: uuid.UUID, action: str, user: User, db: AsyncSession
) -> AnswerJobRead:
    job = await db.get(AnswerJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="回答任务不存在")
    if action == "pause" and job.status == AnswerJobStatus.running:
        job.status = AnswerJobStatus.paused
    elif action == "resume" and job.status in {AnswerJobStatus.paused, AnswerJobStatus.pending}:
        job.status = AnswerJobStatus.running
        job.error_message = None
        start_answer_job(job.id)
    elif action == "stop" and job.status not in {
        AnswerJobStatus.completed,
        AnswerJobStatus.failed,
        AnswerJobStatus.stopped,
    }:
        job.status = AnswerJobStatus.stopped
        job.completed_at = datetime.now(UTC)
    else:
        raise HTTPException(status_code=409, detail="当前任务状态不能执行此操作")
    await db.commit()
    await db.refresh(job)
    return _job_read(job)


@router.post("/answer-jobs/{job_id}/pause", response_model=AnswerJobRead)
async def pause_answer_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> AnswerJobRead:
    return await _control_job(job_id, "pause", user, db)


@router.post("/answer-jobs/{job_id}/resume", response_model=AnswerJobRead)
async def resume_answer_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> AnswerJobRead:
    return await _control_job(job_id, "resume", user, db)


@router.post("/answer-jobs/{job_id}/stop", response_model=AnswerJobRead)
async def stop_answer_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> AnswerJobRead:
    return await _control_job(job_id, "stop", user, db)


@router.post("/answer-jobs/publish/start", response_model=AnswerJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_answer_publish_job(
    payload: AnswerPublishJobCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerJobRead:
    answers = list(
        (
            await db.execute(select(ZhihuAnswer).where(ZhihuAnswer.id.in_(set(payload.answer_ids))))
        ).scalars()
    )
    if len(answers) != len(set(payload.answer_ids)):
        raise HTTPException(status_code=404, detail="部分回答不存在")
    account_ids = {item.account_id for item in answers}
    if len(account_ids) != 1:
        raise HTTPException(status_code=400, detail="一次只能发布同一个知乎账号的回答")
    account_id = next(iter(account_ids))
    account = await get_account_for_user(account_id, user, db)
    if any(item.status == AnswerStatus.published for item in answers):
        raise HTTPException(status_code=400, detail="所选回答中包含已发布回答")
    quota_used = await _quota_used_today(account, db)
    remaining = max(0, account.daily_answer_limit - quota_used)
    if len(answers) > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"今日剩余回答额度为 {remaining} 个，请减少选择数量或调整账号额度",
        )
    active = await db.scalar(
        select(func.count(AnswerJob.id)).where(
            AnswerJob.account_id == account_id,
            AnswerJob.job_type == AnswerJobType.publish,
            AnswerJob.status.in_([AnswerJobStatus.pending, AnswerJobStatus.running, AnswerJobStatus.paused]),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该账号已有未结束的回答发布任务")
    job = AnswerJob(
        user_id=user.id,
        account_id=account_id,
        job_type=AnswerJobType.publish,
        total_count=len(answers),
        payload_json=json.dumps({"answer_ids": [str(item.id) for item in answers]}),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    start_answer_job(job.id)
    return _job_read(job)


@router.get("/accounts/{account_id}/auto-answer/summary", response_model=AutoAnswerSummary)
async def auto_answer_summary(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AutoAnswerSummary:
    account = await get_account_for_user(account_id, user, db)
    quota_used = await _quota_used_today(account, db)
    ready = await db.scalar(
        select(func.count(ZhihuAnswer.id)).where(
            ZhihuAnswer.account_id == account_id, ZhihuAnswer.status == AnswerStatus.ready
        )
    )
    return AutoAnswerSummary(
        daily_limit=account.daily_answer_limit,
        # Kept for API compatibility; this value now means successful
        # publications that consumed quota, not all attempts.
        attempted_today=quota_used,
        remaining_today=max(0, account.daily_answer_limit - quota_used),
        ready_count=ready or 0,
        published_today=quota_used,
    )


@router.post("/accounts/{account_id}/auto-answer/run", response_model=AutoAnswerRunResponse)
async def run_auto_answer(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AutoAnswerRunResponse:
    account = await get_account_for_user(account_id, user, db)
    quota_used = await _quota_used_today(account, db)
    remaining = max(0, account.daily_answer_limit - quota_used)
    if remaining == 0:
        return AutoAnswerRunResponse(
            daily_limit=account.daily_answer_limit,
            attempted_today=quota_used,
            queued_count=0,
            job=None,
        )
    answer_ids = list(
        (
            await db.execute(
                select(ZhihuAnswer.id)
                .where(
                    ZhihuAnswer.account_id == account_id,
                    ZhihuAnswer.status == AnswerStatus.ready,
                )
                .order_by(ZhihuAnswer.created_at.asc())
                .limit(remaining)
            )
        ).scalars()
    )
    if not answer_ids:
        return AutoAnswerRunResponse(
            daily_limit=account.daily_answer_limit,
            attempted_today=quota_used,
            queued_count=0,
            job=None,
        )
    active = await db.scalar(
        select(func.count(AnswerJob.id)).where(
            AnswerJob.account_id == account_id,
            AnswerJob.job_type == AnswerJobType.publish,
            AnswerJob.status.in_([AnswerJobStatus.pending, AnswerJobStatus.running, AnswerJobStatus.paused]),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该账号已有未结束的回答发布任务")
    job = AnswerJob(
        user_id=user.id,
        account_id=account_id,
        job_type=AnswerJobType.publish,
        total_count=len(answer_ids),
        payload_json=json.dumps({"answer_ids": [str(value) for value in answer_ids]}),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    start_answer_job(job.id)
    return AutoAnswerRunResponse(
        daily_limit=account.daily_answer_limit,
        attempted_today=quota_used,
        queued_count=len(answer_ids),
        job=_job_read(job),
    )


@router.get("/accounts/{account_id}/answers/{answer_id}", response_model=AnswerRead)
async def get_answer(account_id: uuid.UUID, answer_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> AnswerRead:
    account = await get_account_for_user(account_id, user, db)
    return _answer_read(await _answer_for_account(account_id, answer_id, db), account.display_name)


@router.patch("/accounts/{account_id}/answers/{answer_id}", response_model=AnswerRead)
async def update_answer(
    account_id: uuid.UUID,
    answer_id: uuid.UUID,
    payload: AnswerUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> AnswerRead:
    account = await get_account_for_user(account_id, user, db)
    answer = await _answer_for_account(account_id, answer_id, db)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] == AnswerStatus.published:
        raise HTTPException(status_code=400, detail="只有知乎真实发布成功后才能标记为已发布")
    if "content" in data:
        answer.content = data["content"]
        answer.content_length = answer_content_length(data["content"])
    if "status" in data:
        answer.status = data["status"]
        if answer.status != AnswerStatus.failed:
            answer.error_message = None
    await db.commit()
    await db.refresh(answer)
    return _answer_read(answer, account.display_name)


@router.delete("/accounts/{account_id}/answers/{answer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_answer(account_id: uuid.UUID, answer_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> None:
    await get_account_for_user(account_id, user, db)
    answer = await _answer_for_account(account_id, answer_id, db)
    await db.delete(answer)
    await db.commit()


@router.post("/accounts/{account_id}/answers/bulk-status", response_model=AnswerBulkResult)
async def bulk_answer_status(account_id: uuid.UUID, payload: AnswerBulkRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> AnswerBulkResult:
    await get_account_for_user(account_id, user, db)
    if payload.status not in {AnswerStatus.draft, AnswerStatus.ready}:
        raise HTTPException(status_code=400, detail="批量操作只能转为草稿或待发布")
    result = await db.execute(
        update(ZhihuAnswer)
        .where(ZhihuAnswer.account_id == account_id, ZhihuAnswer.id.in_(set(payload.answer_ids)))
        .values(status=payload.status, error_message=None)
        .returning(ZhihuAnswer.id)
    )
    ids = list(result.scalars())
    await db.commit()
    return AnswerBulkResult(affected_count=len(ids))


@router.post("/accounts/{account_id}/answers/bulk-delete", response_model=AnswerBulkResult)
async def bulk_answer_delete(account_id: uuid.UUID, payload: AnswerBulkRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)) -> AnswerBulkResult:
    await get_account_for_user(account_id, user, db)
    result = await db.execute(
        delete(ZhihuAnswer)
        .where(ZhihuAnswer.account_id == account_id, ZhihuAnswer.id.in_(set(payload.answer_ids)))
        .returning(ZhihuAnswer.id)
    )
    ids = list(result.scalars())
    await db.commit()
    return AnswerBulkResult(affected_count=len(ids))
