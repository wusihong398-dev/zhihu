import asyncio
import json
import random
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.account import AccountStatus, ZhihuAccount
from app.models.article import Article, ArticleStatus
from app.models.article_job import (
    ArticleJob,
    ArticleJobStatus,
    ArticleJobType,
    ArticleOutputMode,
)
from app.models.keyword import AccountKeyword
from app.models.product import PromotedProduct
from app.models.system_setting import SystemSetting
from app.models.user_ai_provider import UserAIProviderConfig
from app.services.ai_providers import (
    AIProviderError,
    PROVIDERS,
    generate_article_content,
)
from app.services.secret_box import decrypt_secret
from app.services.zhihu_publisher import (
    ZhihuLoginRequired,
    ZhihuPublishError,
    publish_article_to_zhihu,
)


_tasks: dict[uuid.UUID, asyncio.Task] = {}
_terminal_statuses = {
    ArticleJobStatus.stopped,
    ArticleJobStatus.completed,
    ArticleJobStatus.failed,
}


def article_content_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def start_article_job(job_id: uuid.UUID) -> None:
    current = _tasks.get(job_id)
    if current and not current.done():
        return
    task = asyncio.create_task(run_article_job(job_id))
    _tasks[job_id] = task
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))


async def close_article_jobs() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


async def recover_article_jobs() -> None:
    """Leave interrupted jobs resumable after a process or server restart."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(ArticleJob).where(
                ArticleJob.status.in_(
                    [ArticleJobStatus.pending, ArticleJobStatus.running]
                )
            )
        )
        jobs = list(result.scalars())
        for job in jobs:
            job.status = ArticleJobStatus.paused
            job.error_message = "服务重启后任务已暂停，可点击继续执行"
        if jobs:
            await db.commit()


async def _wait_for_permission(job_id: uuid.UUID) -> ArticleJobStatus:
    while True:
        async with SessionLocal() as db:
            job = await db.get(ArticleJob, job_id)
            if job is None:
                return ArticleJobStatus.stopped
            current = job.status
        if current == ArticleJobStatus.paused:
            await asyncio.sleep(0.5)
            continue
        return current


async def _publish_delay() -> None:
    async with SessionLocal() as db:
        settings = await db.get(SystemSetting, 1)
        minimum = settings.publish_interval_min if settings else 5
        maximum = settings.publish_interval_max if settings else 12
    if maximum > 0:
        await asyncio.sleep(random.uniform(minimum, maximum))


async def _set_failed(job_id: uuid.UUID, message: str) -> None:
    async with SessionLocal() as db:
        job = await db.get(ArticleJob, job_id)
        if job and job.status not in _terminal_statuses:
            job.status = ArticleJobStatus.failed
            job.error_message = message[:2000]
            job.completed_at = datetime.now(UTC)
            await db.commit()


async def _begin_job(job_id: uuid.UUID) -> ArticleJob | None:
    async with SessionLocal() as db:
        job = await db.get(ArticleJob, job_id)
        if job is None or job.status in _terminal_statuses:
            return None
        job.status = ArticleJobStatus.running
        job.error_message = None
        await db.commit()
        return job


async def run_article_job(job_id: uuid.UUID) -> None:
    try:
        job = await _begin_job(job_id)
        if job is None:
            return
        if job.job_type == ArticleJobType.generate:
            await _run_generation_job(job_id)
        else:
            await _run_publish_job(job_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _set_failed(job_id, f"任务执行失败：{exc}")


async def _finish_if_active(job: ArticleJob) -> None:
    if job.status not in _terminal_statuses:
        job.status = ArticleJobStatus.completed
        job.current_item = None
        job.completed_at = datetime.now(UTC)
        if job.failed_count:
            job.error_message = (
                f"任务已完成，其中 {job.failed_count} 篇失败；请在文章列表查看原因"
            )


def _expand_prompt(template: str, keyword: str, product: PromotedProduct) -> str:
    values = {
        "关键词": keyword,
        "商品名称": product.name,
        "商品分类": product.category,
        "商品简介": product.description,
        "商品卖点": product.selling_points,
        "目标人群": product.target_audience,
        "推广链接": product.promotion_url,
        "内容要求": product.content_requirements,
        "禁用表述": product.forbidden_terms,
    }
    result = template
    for name, value in values.items():
        result = result.replace(f"{{{name}}}", value or "未填写")
    return result


async def _run_generation_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        job = await db.get(ArticleJob, job_id)
        payload = json.loads(job.payload_json)
        account = await db.get(ZhihuAccount, uuid.UUID(payload["account_id"]))
        product = await db.get(PromotedProduct, uuid.UUID(payload["product_id"]))
        config_result = await db.execute(
            select(UserAIProviderConfig).where(
                UserAIProviderConfig.user_id == job.user_id,
                UserAIProviderConfig.provider == payload["provider"],
            )
        )
        config = config_result.scalar_one_or_none()
        keyword_ids = [uuid.UUID(value) for value in payload["keyword_ids"]]
        keyword_result = await db.execute(
            select(AccountKeyword).where(AccountKeyword.id.in_(keyword_ids))
        )
        keywords = {item.id: item for item in keyword_result.scalars()}
        if account is None or product is None or config is None:
            raise RuntimeError("生成任务所需的账号、商品或 AI 配置已不存在")
        definition = PROVIDERS.get(payload["provider"])
        if definition is None or not config.enabled or not config.api_key_encrypted:
            raise RuntimeError("AI 平台未启用或 API Key 已删除")
        api_key = decrypt_secret(config.api_key_encrypted)
        specs = [
            keywords[keyword_id]
            for keyword_id in keyword_ids
            if keyword_id in keywords
            for _ in range(payload["articles_per_keyword"])
        ]
        completed_at_start = job.completed_count

    for index, keyword in enumerate(
        specs[completed_at_start:], start=completed_at_start
    ):
        permission = await _wait_for_permission(job_id)
        if permission in _terminal_statuses:
            return
        async with SessionLocal() as db:
            job = await db.get(ArticleJob, job_id)
            job.status = ArticleJobStatus.running
            job.current_item = f"生成：{keyword.keyword}（{index + 1}/{len(specs)}）"
            await db.commit()

        title_instruction = _expand_prompt(
            payload["title_prompt"], keyword.keyword, product
        )
        content_instruction = _expand_prompt(
            payload["content_prompt"], keyword.keyword, product
        )
        if product.content_requirements:
            content_instruction += f"\n补充要求：{product.content_requirements}"
        if product.forbidden_terms:
            content_instruction += f"\n不得出现：{product.forbidden_terms}"
        generation_error = None
        title = ""
        content = ""
        try:
            title, content = await generate_article_content(
                definition,
                api_key,
                payload["model"],
                title_instruction=title_instruction,
                content_instruction=content_instruction,
                min_length=payload["min_length"],
                max_length=payload["max_length"],
            )
        except AIProviderError as exc:
            generation_error = str(exc)

        article = Article(
            id=uuid.uuid4(),
            account_id=account.id,
            keyword_id=keyword.id,
            product_id=product.id,
            keyword_text=keyword.keyword,
            product_name=product.name,
            provider=payload["provider"],
            model=payload["model"],
            title_prompt=payload["title_prompt"],
            content_prompt=payload["content_prompt"],
            title=title or f"生成失败：{keyword.keyword}",
            content=content,
            content_length=article_content_length(content),
        )
        forbidden_terms = [
            item.strip()
            for item in re.split(r"[,，;；\n]+", product.forbidden_terms)
            if item.strip()
        ]
        found_term = next((term for term in forbidden_terms if term in content), None)
        if generation_error:
            article.status = ArticleStatus.failed
            article.error_message = generation_error
        elif article.content_length < payload["min_length"]:
            article.status = ArticleStatus.failed
            article.error_message = f"正文仅 {article.content_length} 字，少于最低 {payload['min_length']} 字"
        elif article.content_length > payload["max_length"]:
            article.status = ArticleStatus.failed
            article.error_message = (
                f"正文 {article.content_length} 字，超过最高 {payload['max_length']} 字"
            )
        elif found_term:
            article.status = ArticleStatus.failed
            article.error_message = f"正文包含禁用表述：{found_term}"
        else:
            article.status = (
                ArticleStatus.ready
                if payload["output_mode"] == ArticleOutputMode.immediate.value
                else ArticleStatus.draft
            )

        if article.status == ArticleStatus.ready:
            if not account.enabled:
                article.status = ArticleStatus.failed
                article.error_message = "知乎账号已停用，文章未发布"
            elif len(article.title.strip()) > 100:
                article.status = ArticleStatus.failed
                article.error_message = "知乎文章标题不能超过 100 个字符"
            else:
                try:
                    published_url = await publish_article_to_zhihu(account, article)
                    article.status = ArticleStatus.published
                    article.published_url = published_url
                    article.published_at = datetime.now(UTC)
                    account.status = AccountStatus.online
                except ZhihuLoginRequired as exc:
                    account.status = AccountStatus.offline
                    article.status = ArticleStatus.failed
                    article.error_message = str(exc)
                except ZhihuPublishError as exc:
                    article.status = ArticleStatus.failed
                    article.error_message = str(exc)
            article.publish_attempted_at = datetime.now(UTC)

        async with SessionLocal() as db:
            db.add(article)
            db.add(account)
            current_job = await db.get(ArticleJob, job_id)
            current_job.completed_count += 1
            if article.status == ArticleStatus.failed:
                current_job.failed_count += 1
            else:
                current_job.success_count += 1
            await db.commit()

    async with SessionLocal() as db:
        job = await db.get(ArticleJob, job_id)
        await _finish_if_active(job)
        await db.commit()


async def _run_publish_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        job = await db.get(ArticleJob, job_id)
        payload = json.loads(job.payload_json)
        article_ids = [uuid.UUID(value) for value in payload["article_ids"]]
        completed_at_start = job.completed_count

    for index, article_id in enumerate(
        article_ids[completed_at_start:], start=completed_at_start
    ):
        permission = await _wait_for_permission(job_id)
        if permission in _terminal_statuses:
            return
        async with SessionLocal() as db:
            job = await db.get(ArticleJob, job_id)
            article = await db.get(Article, article_id)
            if article is None:
                job.completed_count += 1
                job.failed_count += 1
                job.error_message = "队列中的文章已被删除"
                await db.commit()
                continue
            account = await db.get(ZhihuAccount, article.account_id)
            job.status = ArticleJobStatus.running
            job.current_item = (
                f"发布：{article.title}（{index + 1}/{len(article_ids)}）"
            )
            await db.commit()

        failure = None
        login_failed = False
        published_url = None
        if account is None or not account.enabled:
            failure = "知乎账号不存在或已停用"
        elif not article.title.strip() or not article.content.strip():
            failure = "文章标题和正文不能为空"
        elif len(article.title.strip()) > 100:
            failure = "知乎文章标题不能超过 100 个字符"
        else:
            try:
                article.status = ArticleStatus.ready
                published_url = await publish_article_to_zhihu(account, article)
            except ZhihuLoginRequired as exc:
                failure = str(exc)
                login_failed = True
            except ZhihuPublishError as exc:
                failure = str(exc)

        async with SessionLocal() as db:
            current_article = await db.get(Article, article_id)
            job = await db.get(ArticleJob, job_id)
            if current_article is None:
                job.failed_count += 1
                job.completed_count += 1
                job.error_message = "文章在发布过程中被删除，无法保存发布结果"
                await db.commit()
                continue
            current_account = await db.get(ZhihuAccount, current_article.account_id)
            current_article.publish_attempted_at = datetime.now(UTC)
            if failure:
                current_article.status = ArticleStatus.failed
                current_article.error_message = failure
                job.failed_count += 1
                if login_failed and current_account:
                    current_account.status = AccountStatus.offline
            else:
                current_article.status = ArticleStatus.published
                current_article.published_url = published_url
                current_article.published_at = datetime.now(UTC)
                current_article.error_message = None
                if current_account:
                    current_account.status = AccountStatus.online
                job.success_count += 1
            job.completed_count += 1
            await db.commit()

        if index + 1 < len(article_ids) and await _wait_for_permission(job_id) not in _terminal_statuses:
            await _publish_delay()

    async with SessionLocal() as db:
        job = await db.get(ArticleJob, job_id)
        await _finish_if_active(job)
        await db.commit()
