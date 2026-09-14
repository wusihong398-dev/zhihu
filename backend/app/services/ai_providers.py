import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderDefinition:
    provider: str
    display_name: str
    base_url: str
    models: tuple[str, ...]


class AIProviderError(RuntimeError):
    pass


# httpx 的 read timeout 是“连续无数据”的超时；若上游持续发送少量数据，
# 它可能一直不触发。另设整次调用的墙钟上限，保证后台任务一定会结束。
ARTICLE_GENERATION_DEADLINE_SECONDS = 150


PROVIDERS: dict[str, ProviderDefinition] = {
    "openai": ProviderDefinition(
        provider="openai",
        display_name="OpenAI / ChatGPT",
        base_url="https://api.openai.com/v1",
        models=("gpt-5.5", "gpt-5-mini", "gpt-4.1-mini"),
    ),
    "deepseek": ProviderDefinition(
        provider="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        models=("deepseek-flash", "deepseek-v4-pro"),
    ),
    "volcengine": ProviderDefinition(
        provider="volcengine",
        display_name="火山方舟",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        models=("doubao-seed-2-1-pro-260628", "doubao-seed-1-6-250615"),
    ),
}


async def test_provider(
    definition: ProviderDefinition, api_key: str, model: str
) -> tuple[bool, str, int]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                f"{definition.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "只回复 OK"}],
                    "stream": False,
                },
            )
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.is_success:
            return True, "连接成功，模型可以正常调用", latency_ms
        try:
            detail = response.json()
            message = detail.get("error", {}).get("message") or detail.get("message")
        except (ValueError, AttributeError):
            message = None
        return False, f"平台返回 {response.status_code}：{message or '请检查 API Key 和模型'}", latency_ms
    except httpx.TimeoutException:
        return False, "连接超时，请稍后重试", 30000
    except httpx.HTTPError as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return False, f"连接失败：{type(exc).__name__}", latency_ms


def _extract_article_json(raw: str) -> tuple[str, str]:
    cleaned = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.S | re.I)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise AIProviderError("模型没有返回可识别的文章 JSON")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIProviderError("模型返回的文章 JSON 格式不正确") from exc
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not title or not content:
        raise AIProviderError("模型返回内容缺少标题或正文")
    return title[:300], content


def _extract_answer_json(raw: str) -> str:
    cleaned = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.S | re.I)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise AIProviderError("模型没有返回可识别的回答 JSON")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIProviderError("模型返回的回答 JSON 格式不正确") from exc
    content = str(payload.get("content", "")).strip()
    if not content:
        raise AIProviderError("模型返回内容缺少回答正文")
    return content


async def generate_article_content(
    definition: ProviderDefinition,
    api_key: str,
    model: str,
    *,
    title_instruction: str,
    content_instruction: str,
    min_length: int,
    max_length: int,
) -> tuple[str, str]:
    system_prompt = (
        "你是知乎内容编辑。请严格返回 JSON 对象，不要使用 Markdown 代码块。"
        "JSON 仅包含 title 和 content 两个字符串字段。正文使用自然段，内容真实、克制，"
        "不得虚构经历、数据、资质或效果承诺。"
    )
    user_prompt = (
        f"标题要求：\n{title_instruction}\n\n"
        f"正文要求：\n{content_instruction}\n\n"
        f"正文字数范围：{min_length} 至 {max_length} 个中文字符。"
    )
    try:
        async with asyncio.timeout(ARTICLE_GENERATION_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=15, read=120, write=30, pool=15),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{definition.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": False,
                        "temperature": 0.8,
                    },
                )
    except TimeoutError as exc:
        raise AIProviderError(
            f"AI 平台生成文章超过 {ARTICLE_GENERATION_DEADLINE_SECONDS} 秒，已自动终止；请稍后重试或更换模型"
        ) from exc
    except httpx.TimeoutException as exc:
        raise AIProviderError("AI 平台响应超时") from exc
    except httpx.HTTPError as exc:
        raise AIProviderError(f"AI 平台连接失败：{type(exc).__name__}") from exc
    if not response.is_success:
        try:
            payload = response.json()
            message = payload.get("error", {}).get("message") or payload.get("message")
        except (ValueError, AttributeError):
            message = None
        raise AIProviderError(
            f"AI 平台返回 {response.status_code}：{message or '请检查 API Key 和模型'}"
        )
    try:
        raw = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI 平台返回结构不正确") from exc
    return _extract_article_json(raw)


async def generate_answer_content(
    definition: ProviderDefinition,
    api_key: str,
    model: str,
    *,
    question_title: str,
    question_excerpt: str,
    instruction: str,
    min_length: int,
    max_length: int,
) -> str:
    system_prompt = (
        "你是知乎问答内容编辑。请严格返回 JSON 对象，不要使用 Markdown 代码块，"
        "JSON 仅包含 content 字符串字段。回答应先解决问题，再自然提及相关商品；"
        "不得虚构亲身经历、数据、资质、医疗或法律效果，不得作保证性承诺。"
    )
    user_prompt = (
        f"知乎问题：{question_title}\n"
        f"问题补充：{question_excerpt or '无'}\n\n"
        f"回答要求：\n{instruction}\n\n"
        f"回答字数范围：{min_length} 至 {max_length} 个中文字符。"
    )
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(
                f"{definition.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "temperature": 0.7,
                },
            )
    except httpx.TimeoutException as exc:
        raise AIProviderError("AI 平台响应超时") from exc
    except httpx.HTTPError as exc:
        raise AIProviderError(f"AI 平台连接失败：{type(exc).__name__}") from exc
    if not response.is_success:
        try:
            payload = response.json()
            message = payload.get("error", {}).get("message") or payload.get("message")
        except (ValueError, AttributeError):
            message = None
        raise AIProviderError(
            f"AI 平台返回 {response.status_code}：{message or '请检查 API Key 和模型'}"
        )
    try:
        raw = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI 平台返回结构不正确") from exc
    return _extract_answer_json(raw)
