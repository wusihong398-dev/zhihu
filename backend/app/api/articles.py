import asyncio
import json
import re
import unicodedata
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.account import AccountStatus, ZhihuAccount
from app.models.article import Article, ArticleStatus
from app.models.article_job import ArticleJob, ArticleJobStatus, ArticleJobType
from app.models.keyword import AccountKeyword
from app.models.product import PromotedProduct
from app.models.user import User, UserRole
from app.models.user_ai_provider import UserAIProviderConfig
from app.schemas.article import (
    ArticleBulkRequest,
    ArticleBulkResult,
    ArticleCreate,
    ArticleGenerateRequest,
    ArticleGenerateResponse,
    ArticleJobRead,
    ArticleListResponse,
    ArticlePublishJobCreate,
    ArticleRead,
    ArticleSyncResponse,
    ArticleUpdate,
)
from app.services.access_control import get_account_for_user
from app.services.article_jobs import start_article_job
from app.services.ai_providers import (
    AIProviderError,
    PROVIDERS,
    generate_article_content,
)
from app.services.secret_box import decrypt_secret
from app.services.zhihu_article_sync import (
    ZhihuArticleSyncError,
    ZhihuArticleSyncLoginRequired,
    fetch_zhihu_published_articles,
)
from app.services.zhihu_publisher import (
    ZhihuLoginRequired,
    ZhihuPublishError,
    publish_article_to_zhihu,
)

router = APIRouter(tags=["articles"], dependencies=[Depends(require_active_user)])


def _content_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def _sync_title_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _article_read(article: Article, account_name: str = "") -> ArticleRead:
    return ArticleRead.model_validate(article).model_copy(
        update={"account_name": account_name}
    )


def _job_read(job: ArticleJob) -> ArticleJobRead:
    percent = (
        0
        if not job.total_count
        else min(100, round(job.completed_count * 100 / job.total_count))
    )
    return ArticleJobRead.model_validate(job).model_copy(
        update={"progress_percent": percent}
    )


async def _get_article(
    account_id: uuid.UUID, article_id: uuid.UUID, db: AsyncSession
) -> Article:
    result = await db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.account_id == account_id,
        )
    )
    article = result.scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    return article


def _user_account_filter(user: User):
    if user.role == UserRole.admin:
        return None
    return ZhihuAccount.owner_user_id == user.id


@router.get("/articles", response_model=ArticleListResponse)
async def list_articles(
    account_id: uuid.UUID | None = Query(default=None),
    q: str = Query(default="", max_length=255),
    article_status: ArticleStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleListResponse:
    filters = []
    owner_filter = _user_account_filter(user)
    if owner_filter is not None:
        filters.append(owner_filter)
    if account_id:
        await get_account_for_user(account_id, user, db)
        filters.append(Article.account_id == account_id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                Article.title.ilike(pattern),
                Article.keyword_text.ilike(pattern),
                Article.product_name.ilike(pattern),
            )
        )
    if article_status:
        filters.append(Article.status == article_status)
    base = select(Article, ZhihuAccount.display_name).join(
        ZhihuAccount, ZhihuAccount.id == Article.account_id
    )
    count_query = select(func.count(Article.id)).join(
        ZhihuAccount, ZhihuAccount.id == Article.account_id
    )
    total = await db.scalar(count_query.where(*filters))
    result = await db.execute(
        base.where(*filters)
        .order_by(Article.created_at.desc(), Article.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return ArticleListResponse(
        items=[
            _article_read(article, account_name) for article, account_name in result
        ],
        total=total or 0,
    )


@router.post(
    "/accounts/{account_id}/articles",
    response_model=ArticleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_article(
    account_id: uuid.UUID,
    payload: ArticleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleRead:
    account = await get_account_for_user(account_id, user, db)
    if payload.keyword_id:
        keyword = await db.get(AccountKeyword, payload.keyword_id)
        if keyword is None or keyword.account_id != account_id:
            raise HTTPException(status_code=404, detail="关键词不存在")
    if payload.product_id:
        product = await db.get(PromotedProduct, payload.product_id)
        if product is None or product.account_id != account_id:
            raise HTTPException(status_code=404, detail="推广商品不存在")
    article = Article(
        account_id=account_id,
        content_length=_content_length(payload.content),
        **payload.model_dump(),
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)
    return _article_read(article, account.display_name)


@router.post(
    "/accounts/{account_id}/articles/generate",
    response_model=ArticleGenerateResponse,
)
async def generate_articles(
    account_id: uuid.UUID,
    payload: ArticleGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleGenerateResponse:
    account = await get_account_for_user(account_id, user, db)
    definition = PROVIDERS.get(payload.provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="AI 平台不存在")
    config_result = await db.execute(
        select(UserAIProviderConfig).where(
            UserAIProviderConfig.user_id == user.id,
            UserAIProviderConfig.provider == payload.provider,
        )
    )
    config = config_result.scalar_one_or_none()
    if config is None or not config.enabled or not config.api_key_encrypted:
        raise HTTPException(
            status_code=400, detail="请先在 AI 配置中启用平台并保存 API Key"
        )
    try:
        api_key = decrypt_secret(config.api_key_encrypted)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    model = (payload.model or config.model).strip()
    product_result = await db.execute(
        select(PromotedProduct).where(
            PromotedProduct.id == payload.product_id,
            PromotedProduct.account_id == account_id,
            PromotedProduct.enabled.is_(True),
        )
    )
    product = product_result.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="启用的推广商品不存在")
    keyword_result = await db.execute(
        select(AccountKeyword).where(
            AccountKeyword.account_id == account_id,
            AccountKeyword.id.in_(payload.keyword_ids),
        )
    )
    keywords_by_id = {item.id: item for item in keyword_result.scalars()}
    if len(keywords_by_id) != len(set(payload.keyword_ids)):
        raise HTTPException(status_code=404, detail="部分关键词不存在或不属于当前账号")

    generated: list[Article] = []
    forbidden_terms = [
        item.strip()
        for item in re.split(r"[,，;；\n]+", product.forbidden_terms)
        if item.strip()
    ]
    variables = {
        "商品名称": product.name,
        "商品分类": product.category,
        "商品简介": product.description,
        "商品卖点": product.selling_points,
        "目标人群": product.target_audience,
        "推广链接": product.promotion_url,
        "内容要求": product.content_requirements,
        "禁用表述": product.forbidden_terms,
    }

    def expand(template: str, keyword: str) -> str:
        values = {**variables, "关键词": keyword}
        result = template
        for name, value in values.items():
            result = result.replace(f"{{{name}}}", value or "未填写")
        return result

    generation_specs = []
    for keyword_id in payload.keyword_ids:
        keyword = keywords_by_id[keyword_id]
        for _ in range(payload.articles_per_keyword):
            title_instruction = expand(payload.title_prompt, keyword.keyword)
            content_instruction = expand(payload.content_prompt, keyword.keyword)
            if product.content_requirements:
                content_instruction += f"\n补充要求：{product.content_requirements}"
            if product.forbidden_terms:
                content_instruction += f"\n不得出现：{product.forbidden_terms}"
            generation_specs.append((keyword, title_instruction, content_instruction))

    semaphore = asyncio.Semaphore(5)

    async def generate_one(title_instruction: str, content_instruction: str):
        try:
            async with semaphore:
                title, content = await generate_article_content(
                    definition,
                    api_key,
                    model,
                    title_instruction=title_instruction,
                    content_instruction=content_instruction,
                    min_length=payload.min_length,
                    max_length=payload.max_length,
                )
            return title, content, None
        except AIProviderError as exc:
            return "", "", str(exc)

    generation_results = await asyncio.gather(
        *(
            generate_one(title_instruction, content_instruction)
            for _, title_instruction, content_instruction in generation_specs
        )
    )
    for (keyword, _, _), (title, content, generation_error) in zip(
        generation_specs, generation_results, strict=True
    ):
        article = Article(
            account_id=account_id,
            keyword_id=keyword.id,
            product_id=product.id,
            keyword_text=keyword.keyword,
            product_name=product.name,
            provider=payload.provider,
            model=model,
            title_prompt=payload.title_prompt,
            content_prompt=payload.content_prompt,
        )
        if generation_error:
            article.status = ArticleStatus.failed
            article.title = f"生成失败：{keyword.keyword}"
            article.error_message = generation_error
        else:
            article.title = title
            article.content = content
            article.content_length = _content_length(article.content)
            found_term = next(
                (term for term in forbidden_terms if term in article.content), None
            )
            if article.content_length < payload.min_length:
                article.status = ArticleStatus.failed
                article.error_message = f"正文仅 {article.content_length} 字，少于最低 {payload.min_length} 字"
            elif article.content_length > payload.max_length:
                article.status = ArticleStatus.failed
                article.error_message = f"正文 {article.content_length} 字，超过最高 {payload.max_length} 字"
            elif found_term:
                article.status = ArticleStatus.failed
                article.error_message = f"正文包含禁用表述：{found_term}"
            else:
                article.status = ArticleStatus.draft
        db.add(article)
        generated.append(article)
    await db.flush()
    await db.commit()
    for article in generated:
        await db.refresh(article)
    success_count = sum(item.status != ArticleStatus.failed for item in generated)
    return ArticleGenerateResponse(
        items=[_article_read(item, account.display_name) for item in generated],
        requested_count=len(generated),
        success_count=success_count,
        failed_count=len(generated) - success_count,
    )


@router.post(
    "/accounts/{account_id}/article-jobs/generate",
    response_model=ArticleJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_generation_job(
    account_id: uuid.UUID,
    payload: ArticleGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead:
    await get_account_for_user(account_id, user, db)
    definition = PROVIDERS.get(payload.provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="AI 平台不存在")
    config_result = await db.execute(
        select(UserAIProviderConfig).where(
            UserAIProviderConfig.user_id == user.id,
            UserAIProviderConfig.provider == payload.provider,
        )
    )
    config = config_result.scalar_one_or_none()
    if config is None or not config.enabled or not config.api_key_encrypted:
        raise HTTPException(
            status_code=400, detail="请先在 AI 配置中启用平台并保存 API Key"
        )
    try:
        decrypt_secret(config.api_key_encrypted)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    model = (payload.model or config.model).strip()
    if not model:
        raise HTTPException(status_code=422, detail="请选择 AI 模型")
    product = await db.get(PromotedProduct, payload.product_id)
    if product is None or product.account_id != account_id or not product.enabled:
        raise HTTPException(status_code=404, detail="启用的推广商品不存在")
    keyword_result = await db.execute(
        select(AccountKeyword.id).where(
            AccountKeyword.account_id == account_id,
            AccountKeyword.id.in_(payload.keyword_ids),
        )
    )
    if len(list(keyword_result.scalars())) != len(set(payload.keyword_ids)):
        raise HTTPException(status_code=404, detail="部分关键词不存在或不属于当前账号")
    active = await db.scalar(
        select(func.count(ArticleJob.id)).where(
            ArticleJob.user_id == user.id,
            ArticleJob.account_id == account_id,
            ArticleJob.job_type == ArticleJobType.generate,
            ArticleJob.status.in_(
                [
                    ArticleJobStatus.pending,
                    ArticleJobStatus.running,
                    ArticleJobStatus.paused,
                ]
            ),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该账号已有未结束的文章生成任务")
    job_payload = payload.model_dump(mode="json")
    job_payload["account_id"] = str(account_id)
    job_payload["model"] = model
    job = ArticleJob(
        user_id=user.id,
        account_id=account_id,
        job_type=ArticleJobType.generate,
        status=ArticleJobStatus.pending,
        output_mode=payload.output_mode,
        total_count=len(payload.keyword_ids) * payload.articles_per_keyword,
        payload_json=json.dumps(job_payload, ensure_ascii=False),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    start_article_job(job.id)
    return _job_read(job)


@router.post(
    "/article-jobs/publish",
    response_model=ArticleJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_publish_job(
    payload: ArticlePublishJobCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead:
    owned_ids = await _owned_article_ids(payload.article_ids, user, db)
    if len(owned_ids) != len(set(payload.article_ids)):
        raise HTTPException(status_code=404, detail="部分文章不存在或无权操作")
    published_count = await db.scalar(
        select(func.count(Article.id)).where(
            Article.id.in_(owned_ids), Article.status == ArticleStatus.published
        )
    )
    if published_count:
        raise HTTPException(
            status_code=400, detail="所选文章中包含已发布文章，请取消选择后重试"
        )
    active = await db.scalar(
        select(func.count(ArticleJob.id)).where(
            ArticleJob.user_id == user.id,
            ArticleJob.job_type == ArticleJobType.publish,
            ArticleJob.status.in_(
                [
                    ArticleJobStatus.pending,
                    ArticleJobStatus.running,
                    ArticleJobStatus.paused,
                ]
            ),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="已有未结束的文章发布任务")
    job = ArticleJob(
        user_id=user.id,
        job_type=ArticleJobType.publish,
        status=ArticleJobStatus.pending,
        total_count=len(owned_ids),
        payload_json=json.dumps({"article_ids": [str(value) for value in owned_ids]}),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    start_article_job(job.id)
    return _job_read(job)


@router.post(
    "/accounts/{account_id}/articles/sync",
    response_model=ArticleSyncResponse,
)
async def sync_published_articles(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleSyncResponse:
    account = await get_account_for_user(account_id, user, db)
    try:
        remote_articles = await fetch_zhihu_published_articles(account)
    except ZhihuArticleSyncLoginRequired as exc:
        account.status = AccountStatus.offline
        await db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ZhihuArticleSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    result = await db.execute(
        select(Article)
        .where(
            Article.account_id == account_id,
            Article.status.in_([ArticleStatus.failed, ArticleStatus.published]),
        )
        .order_by(Article.created_at.desc(), Article.id.desc())
    )
    local_articles = list(result.scalars())
    remote_by_title: dict[str, list] = {}
    for remote in remote_articles:
        remote_by_title.setdefault(_sync_title_key(remote.title), []).append(remote)

    matched_count = 0
    already_synced_count = 0
    matched_local_ids: set[uuid.UUID] = set()
    for article in local_articles:
        candidates = remote_by_title.get(_sync_title_key(article.title), [])
        if not candidates:
            continue
        remote = candidates.pop(0)
        matched_local_ids.add(article.id)
        if (
            article.status == ArticleStatus.published
            and article.published_url == remote.url
        ):
            already_synced_count += 1
            continue
        published_at = (
            remote.published_at or article.publish_attempted_at or datetime.now(UTC)
        )
        article.status = ArticleStatus.published
        article.published_url = remote.url
        article.published_at = published_at
        article.publish_attempted_at = article.publish_attempted_at or published_at
        article.error_message = None
        matched_count += 1

    account.status = AccountStatus.online
    await db.commit()
    unmatched_failed_count = sum(
        1
        for article in local_articles
        if article.status == ArticleStatus.failed and article.id not in matched_local_ids
    )
    return ArticleSyncResponse(
        scanned_count=len(remote_articles),
        matched_count=matched_count,
        already_synced_count=already_synced_count,
        unmatched_failed_count=unmatched_failed_count,
    )


@router.get("/article-jobs/latest", response_model=ArticleJobRead | None)
async def latest_article_job(
    job_type: ArticleJobType = Query(alias="type"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead | None:
    result = await db.execute(
        select(ArticleJob)
        .where(ArticleJob.user_id == user.id, ArticleJob.job_type == job_type)
        .order_by(ArticleJob.created_at.desc(), ArticleJob.id.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    return _job_read(job) if job else None


@router.get("/article-jobs/{job_id}", response_model=ArticleJobRead)
async def get_article_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead:
    job = await db.get(ArticleJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="文章任务不存在")
    return _job_read(job)


async def _controlled_job(
    job_id: uuid.UUID, user: User, db: AsyncSession
) -> ArticleJob:
    job = await db.get(ArticleJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="文章任务不存在")
    return job


@router.post("/article-jobs/{job_id}/pause", response_model=ArticleJobRead)
async def pause_article_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead:
    job = await _controlled_job(job_id, user, db)
    if job.status not in {ArticleJobStatus.pending, ArticleJobStatus.running}:
        raise HTTPException(status_code=409, detail="当前任务状态不能暂停")
    job.status = ArticleJobStatus.paused
    await db.commit()
    await db.refresh(job)
    return _job_read(job)


@router.post("/article-jobs/{job_id}/resume", response_model=ArticleJobRead)
async def resume_article_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead:
    job = await _controlled_job(job_id, user, db)
    if job.status != ArticleJobStatus.paused:
        raise HTTPException(status_code=409, detail="只有暂停中的任务可以继续")
    job.status = ArticleJobStatus.running
    job.error_message = None
    await db.commit()
    await db.refresh(job)
    start_article_job(job.id)
    return _job_read(job)


@router.post("/article-jobs/{job_id}/stop", response_model=ArticleJobRead)
async def stop_article_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleJobRead:
    job = await _controlled_job(job_id, user, db)
    if job.status in {
        ArticleJobStatus.stopped,
        ArticleJobStatus.completed,
        ArticleJobStatus.failed,
    }:
        raise HTTPException(status_code=409, detail="任务已经结束")
    job.status = ArticleJobStatus.stopped
    job.current_item = None
    job.completed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(job)
    return _job_read(job)


@router.get("/accounts/{account_id}/articles/{article_id}", response_model=ArticleRead)
async def get_article(
    account_id: uuid.UUID,
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleRead:
    account = await get_account_for_user(account_id, user, db)
    article = await _get_article(account_id, article_id, db)
    return _article_read(article, account.display_name)


@router.patch(
    "/accounts/{account_id}/articles/{article_id}", response_model=ArticleRead
)
async def update_article(
    account_id: uuid.UUID,
    article_id: uuid.UUID,
    payload: ArticleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleRead:
    account = await get_account_for_user(account_id, user, db)
    article = await _get_article(account_id, article_id, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(article, field, value)
    if payload.content is not None:
        article.content_length = _content_length(payload.content)
    if article.status != ArticleStatus.failed:
        article.error_message = None
    await db.commit()
    await db.refresh(article)
    return _article_read(article, account.display_name)


@router.post(
    "/accounts/{account_id}/articles/{article_id}/publish",
    response_model=ArticleRead,
)
async def publish_article(
    account_id: uuid.UUID,
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleRead:
    account = await get_account_for_user(account_id, user, db)
    article = await _get_article(account_id, article_id, db)
    if not account.enabled:
        raise HTTPException(status_code=400, detail="知乎账号已停用")
    if article.status != ArticleStatus.ready:
        raise HTTPException(status_code=400, detail="请先将文章状态设置为待发布")
    if not article.title.strip() or not article.content.strip():
        raise HTTPException(status_code=400, detail="文章标题和正文不能为空")
    if len(article.title.strip()) > 100:
        raise HTTPException(status_code=400, detail="知乎文章标题不能超过 100 个字符")
    try:
        published_url = await publish_article_to_zhihu(account, article)
    except ZhihuLoginRequired as exc:
        account.status = AccountStatus.offline
        article.status = ArticleStatus.failed
        article.error_message = str(exc)
        article.publish_attempted_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ZhihuPublishError as exc:
        article.status = ArticleStatus.failed
        article.error_message = str(exc)
        article.publish_attempted_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    account.status = AccountStatus.online
    article.status = ArticleStatus.published
    article.published_url = published_url
    article.published_at = datetime.now(UTC)
    article.publish_attempted_at = article.published_at
    article.error_message = None
    await db.commit()
    await db.refresh(article)
    return _article_read(article, account.display_name)


@router.delete(
    "/accounts/{account_id}/articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_article(
    account_id: uuid.UUID,
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    await get_account_for_user(account_id, user, db)
    article = await _get_article(account_id, article_id, db)
    await db.delete(article)
    await db.commit()


async def _owned_article_ids(
    article_ids: list[uuid.UUID], user: User, db: AsyncSession
) -> list[uuid.UUID]:
    query = select(Article.id).join(ZhihuAccount, ZhihuAccount.id == Article.account_id)
    filters = [Article.id.in_(article_ids)]
    owner_filter = _user_account_filter(user)
    if owner_filter is not None:
        filters.append(owner_filter)
    result = await db.execute(query.where(*filters))
    return list(result.scalars())


@router.post("/articles/bulk-status", response_model=ArticleBulkResult)
async def bulk_article_status(
    payload: ArticleBulkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleBulkResult:
    if payload.status is None:
        raise HTTPException(status_code=422, detail="请选择文章状态")
    owned_ids = await _owned_article_ids(payload.article_ids, user, db)
    if len(owned_ids) != len(set(payload.article_ids)):
        raise HTTPException(status_code=404, detail="部分文章不存在")
    result = await db.execute(
        update(Article).where(Article.id.in_(owned_ids)).values(status=payload.status)
    )
    await db.commit()
    return ArticleBulkResult(affected_count=result.rowcount or 0)


@router.post("/articles/bulk-delete", response_model=ArticleBulkResult)
async def bulk_delete_articles(
    payload: ArticleBulkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ArticleBulkResult:
    owned_ids = await _owned_article_ids(payload.article_ids, user, db)
    if len(owned_ids) != len(set(payload.article_ids)):
        raise HTTPException(status_code=404, detail="部分文章不存在")
    result = await db.execute(delete(Article).where(Article.id.in_(owned_ids)))
    await db.commit()
    return ArticleBulkResult(affected_count=result.rowcount or 0)
