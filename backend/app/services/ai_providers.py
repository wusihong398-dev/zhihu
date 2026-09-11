import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderDefinition:
    provider: str
    display_name: str
    base_url: str
    models: tuple[str, ...]


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
