import asyncio
import json
import random
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.account import AccountStatus, ZhihuAccount
from app.models.answer import AnswerStatus, ZhihuAnswer
from app.models.answer_job import AnswerJob, AnswerJobStatus, AnswerJobType
from app.models.local_publisher import LocalAnswerPublishTask
from app.models.product import PromotedProduct
from app.models.question import QuestionStatus, ZhihuQuestion
from app.models.system_setting import SystemSetting
from app.models.user_ai_provider import UserAIProviderConfig
from app.services.ai_providers import AIProviderError, PROVIDERS, generate_answer_content
from app.services.secret_box import decrypt_secret
from app.services.zhihu_answer_publisher import (
    ZhihuAnswerLoginRequired,
    ZhihuAnswerPublishError,
    ZhihuAnswerRiskControlError,
    publish_answer_to_zhihu,
)


_tasks: dict[uuid.UUID, asyncio.Task] = {}
_terminal = {
    AnswerJobStatus.stopped,
    AnswerJobStatus.completed,
    AnswerJobStatus.failed,
}


def answer_content_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def start_answer_job(job_id: uuid.UUID) -> None:
    current = _tasks.get(job_id)
    if current and not current.done():
        return
    task = asyncio.create_task(run_answer_job(job_id))
    _tasks[job_id] = task
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))


async def close_answer_jobs() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


async def recover_answer_jobs() -> None:
    async with SessionLocal() as db:
        result = await db.execute(
            select(AnswerJob).where(
                AnswerJob.status.in_([AnswerJobStatus.pending, AnswerJobStatus.running])
            )
        )
        jobs = list(result.scalars())
        for job in jobs:
            local_task_count = await db.scalar(
                select(LocalAnswerPublishTask.id)
                .where(
                    LocalAnswerPublishTask.job_id == job.id,
                    LocalAnswerPublishTask.status.in_(["queued", "leased"]),
                )
                .limit(1)
            )
            if local_task_count:
                job.status = AnswerJobStatus.pending
                job.current_item = "等待 Windows 本地发布客户端领取任务"
                job.error_message = None
            else:
                job.status = AnswerJobStatus.paused
                job.error_message = "服务重启后任务已暂停，可点击继续执行"
        if jobs:
            await db.commit()


async def _permission(job_id: uuid.UUID) -> AnswerJobStatus:
    while True:
        async with SessionLocal() as db:
            job = await db.get(AnswerJob, job_id)
            if job is None:
                return AnswerJobStatus.stopped
            status = job.status
        if status == AnswerJobStatus.paused:
            await asyncio.sleep(0.5)
            continue
        return status


async def _fail_job(job_id: uuid.UUID, message: str) -> None:
    async with SessionLocal() as db:
        job = await db.get(AnswerJob, job_id)
        if job and job.status not in _terminal:
            job.status = AnswerJobStatus.failed
            job.error_message = message[:2000]
            job.completed_at = datetime.now(UTC)
            await db.commit()


async def _finish(job_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        job = await db.get(AnswerJob, job_id)
        if job and job.status not in _terminal:
            job.status = AnswerJobStatus.completed
            job.current_item = None
            job.completed_at = datetime.now(UTC)
            if job.failed_count:
                job.error_message = f"任务完成，其中 {job.failed_count} 个回答失败，请查看原因"
            await db.commit()


async def _publish_delay() -> None:
    async with SessionLocal() as db:
        settings = await db.get(SystemSetting, 1)
        minimum = settings.publish_interval_min if settings else 5
        maximum = settings.publish_interval_max if settings else 12
    if maximum > 0:
        await asyncio.sleep(random.uniform(minimum, maximum))


def _expand_prompt(template: str, question: ZhihuQuestion, product: PromotedProduct) -> str:
    values = {
        "问题标题": question.title,
        "问题补充": question.excerpt,
        "关键词": question.keyword_text,
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


async def run_answer_job(job_id: uuid.UUID) -> None:
    try:
        async with SessionLocal() as db:
            job = await db.get(AnswerJob, job_id)
            if job is None or job.status in _terminal:
                return
            job.status = AnswerJobStatus.running
            job.error_message = None
            await db.commit()
            job_type = job.job_type
        if job_type == AnswerJobType.generate:
            await _run_generation(job_id)
        else:
            await _run_publish(job_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _fail_job(job_id, f"任务执行失败：{exc}")


async def _run_generation(job_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        job = await db.get(AnswerJob, job_id)
        payload = json.loads(job.payload_json)
        account = await db.get(ZhihuAccount, job.account_id)
        product = await db.get(PromotedProduct, uuid.UUID(payload["product_id"]))
        config = await db.scalar(
            select(UserAIProviderConfig).where(
                UserAIProviderConfig.user_id == job.user_id,
                UserAIProviderConfig.provider == payload["provider"],
            )
        )
        question_ids = [uuid.UUID(value) for value in payload["question_ids"]]
        questions = {
            item.id: item
            for item in (
                await db.execute(select(ZhihuQuestion).where(ZhihuQuestion.id.in_(question_ids)))
            ).scalars()
        }
        definition = PROVIDERS.get(payload["provider"])
        if account is None or product is None or config is None or definition is None:
            raise RuntimeError("生成任务所需的账号、商品或 AI 配置已不存在")
        if not config.enabled or not config.api_key_encrypted:
            raise RuntimeError("AI 平台未启用或 API Key 已删除")
        api_key = decrypt_secret(config.api_key_encrypted)
        start = job.completed_count

    for index, question_id in enumerate(question_ids[start:], start=start):
        if await _permission(job_id) in _terminal:
            return
        question = questions.get(question_id)
        if question is None:
            async with SessionLocal() as db:
                current = await db.get(AnswerJob, job_id)
                current.completed_count += 1
                current.failed_count += 1
                await db.commit()
            continue
        async with SessionLocal() as db:
            current = await db.get(AnswerJob, job_id)
            current.status = AnswerJobStatus.running
            current.current_item = f"生成：{question.title}（{index + 1}/{len(question_ids)}）"
            await db.commit()

        instruction = _expand_prompt(payload["prompt"], question, product)
        if product.content_requirements:
            instruction += f"\n补充要求：{product.content_requirements}"
        if product.forbidden_terms:
            instruction += f"\n不得出现：{product.forbidden_terms}"
        failure = None
        content = ""
        try:
            content = await generate_answer_content(
                definition,
                api_key,
                payload["model"],
                question_title=question.title,
                question_excerpt=question.excerpt,
                instruction=instruction,
                min_length=payload["min_length"],
                max_length=payload["max_length"],
            )
        except AIProviderError as exc:
            failure = str(exc)
        length = answer_content_length(content)
        forbidden = [
            item.strip()
            for item in re.split(r"[,，;；\n]+", product.forbidden_terms)
            if item.strip()
        ]
        found_term = next((term for term in forbidden if term in content), None)
        if not failure and length < payload["min_length"]:
            failure = f"回答仅 {length} 字，少于最低 {payload['min_length']} 字"
        elif not failure and length > payload["max_length"]:
            failure = f"回答 {length} 字，超过最高 {payload['max_length']} 字"
        elif not failure and found_term:
            failure = f"回答包含禁用表述：{found_term}"

        answer = ZhihuAnswer(
            account_id=account.id,
            question_id=question.id,
            product_id=product.id,
            question_title=question.title,
            question_url=question.url,
            keyword_text=question.keyword_text,
            product_name=product.name,
            content=content,
            content_length=length,
            provider=payload["provider"],
            model=payload["model"],
            prompt=payload["prompt"],
            status=(
                AnswerStatus.failed
                if failure
                else AnswerStatus.ready if payload["ready_after_generate"] else AnswerStatus.draft
            ),
            error_message=failure,
        )
        async with SessionLocal() as db:
            db.add(answer)
            saved_question = await db.get(ZhihuQuestion, question.id)
            if saved_question and not failure:
                saved_question.status = QuestionStatus.answered
            current = await db.get(AnswerJob, job_id)
            current.completed_count += 1
            if failure:
                current.failed_count += 1
            else:
                current.success_count += 1
            await db.commit()
    await _finish(job_id)


async def _run_publish(job_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        job = await db.get(AnswerJob, job_id)
        answer_ids = [uuid.UUID(value) for value in json.loads(job.payload_json)["answer_ids"]]
        start = job.completed_count

    for index, answer_id in enumerate(answer_ids[start:], start=start):
        if await _permission(job_id) in _terminal:
            return
        async with SessionLocal() as db:
            answer = await db.get(ZhihuAnswer, answer_id)
            current = await db.get(AnswerJob, job_id)
            if answer is None:
                current.completed_count += 1
                current.failed_count += 1
                await db.commit()
                continue
            account = await db.get(ZhihuAccount, answer.account_id)
            current.status = AnswerJobStatus.running
            current.current_item = f"发布：{answer.question_title}（{index + 1}/{len(answer_ids)}）"
            await db.commit()

        failure = None
        published_url = None
        login_failed = False
        risk_controlled = False
        if account is None or not account.enabled:
            failure = "知乎账号不存在或已停用"
        elif not answer.content.strip():
            failure = "回答正文不能为空"
        else:
            try:
                published_url = await publish_answer_to_zhihu(account, answer)
            except ZhihuAnswerLoginRequired as exc:
                failure = str(exc)
                login_failed = True
            except ZhihuAnswerRiskControlError as exc:
                failure = str(exc)
                risk_controlled = True
            except ZhihuAnswerPublishError as exc:
                failure = str(exc)

        async with SessionLocal() as db:
            current_answer = await db.get(ZhihuAnswer, answer_id)
            current = await db.get(AnswerJob, job_id)
            if current_answer is None:
                current.completed_count += 1
                current.failed_count += 1
                await db.commit()
                continue
            current_account = await db.get(ZhihuAccount, current_answer.account_id)
            current_answer.publish_attempted_at = datetime.now(UTC)
            if failure:
                current_answer.status = AnswerStatus.failed
                current_answer.error_message = failure
                current.failed_count += 1
                if login_failed and current_account:
                    current_account.status = AccountStatus.offline
            else:
                current_answer.status = AnswerStatus.published
                current_answer.published_url = published_url
                current_answer.published_at = datetime.now(UTC)
                current_answer.error_message = None
                current.success_count += 1
                if current_account:
                    current_account.status = AccountStatus.online
            current.completed_count += 1
            if risk_controlled:
                current.status = AnswerJobStatus.failed
                current.error_message = failure
                current.current_item = None
                current.completed_at = datetime.now(UTC)
            await db.commit()
        if risk_controlled:
            return
        if index + 1 < len(answer_ids) and await _permission(job_id) not in _terminal:
            await _publish_delay()
    await _finish(job_id)
