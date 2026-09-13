import asyncio
import heapq
import itertools
import json
import random
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.keyword import AccountKeyword, KeywordSource
from app.models.keyword_folder import KeywordFolderItem, KeywordJobDestination
from app.models.keyword_job import (
    KeywordCollectionJob,
    KeywordJobSource,
    KeywordJobStatus,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
ENGINE_NAMES = {"baidu": "百度", "google": "谷歌"}
REQUEST_DELAY_RANGE = (1.0, 1.8)


@dataclass(frozen=True)
class RelatedResult:
    candidates: list[str]
    page_blocked: bool = False
    page_empty: bool = False
    used_suggestions: bool = False


def normalize_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _clean_candidates(values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n·-—|")
        normalized = normalize_keyword(cleaned)
        if 1 < len(cleaned) <= 255 and normalized not in unique:
            unique[normalized] = cleaned
    return sorted(unique.values(), key=lambda item: (len(item), item.casefold()))


def parse_baidu_related(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    values: list[str] = []
    selectors = (
        "#rs a",
        ".rs a",
        "[class*='related'] a",
        "[class*='rs-link']",
    )
    for selector in selectors:
        values.extend(node.get_text(" ", strip=True) for node in soup.select(selector))
    if not values:
        label = soup.find(string=re.compile(r"相关搜索|大家还在搜"))
        if label:
            container = label.parent.parent if label.parent else None
            if container:
                values.extend(
                    node.get_text(" ", strip=True) for node in container.select("a")
                )
    return _clean_candidates(values)


def parse_google_related(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    values: list[str] = []
    containers = soup.select("#botstuff, #bres, [aria-label*='相关'], [data-snhf='0']")
    for container in containers:
        for node in container.select("a[href]"):
            href = node.get("href", "")
            parsed = urlparse(href)
            query = parse_qs(parsed.query).get("q", [])
            text = node.get_text(" ", strip=True)
            if query:
                values.append(text or query[0])
    if not values:
        label = soup.find(
            string=re.compile(
                r"用户还搜索了|相关搜索|People also search for|Related searches", re.I
            )
        )
        container = label.parent.parent.parent if label and label.parent else None
        if container:
            values.extend(
                node.get_text(" ", strip=True)
                for node in container.select("a[href*='search']")
            )
    return _clean_candidates(values)


def parse_baidu_suggestions(payload: str) -> list[str]:
    match = re.search(r"\((\{.*\})\)\s*;?\s*$", payload.strip(), re.S)
    raw = match.group(1) if match else payload
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _clean_candidates(data.get("s", [])) if isinstance(data, dict) else []


def parse_google_suggestions(payload: str) -> list[str]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return []
    return _clean_candidates(data[1])


def is_verification_page(engine: str, response: httpx.Response) -> bool:
    url = str(response.url).casefold()
    body = response.text.casefold()
    if engine == "baidu":
        markers = ("百度安全验证", "请输入验证码", "wappass.baidu.com/static/captcha")
    else:
        markers = (
            "our systems have detected unusual traffic",
            "detected unusual traffic",
            "unusual traffic from your computer network",
            "recaptcha",
        )
    return "/sorry/" in url or any(marker.casefold() in body for marker in markers)


async def _fetch_suggestions(
    client: httpx.AsyncClient, engine: str, keyword: str
) -> list[str]:
    if engine == "baidu":
        url = "https://suggestion.baidu.com/su"
        params = {"wd": keyword, "json": "1", "p": "3"}
        parser = parse_baidu_suggestions
    else:
        url = "https://suggestqueries.google.com/complete/search"
        params = {"client": "firefox", "hl": "zh-CN", "q": keyword}
        parser = parse_google_suggestions
    response = await client.get(url, params=params)
    response.raise_for_status()
    return parser(response.text)


async def _fetch_related(
    client: httpx.AsyncClient, engine: str, keyword: str
) -> RelatedResult:
    if engine == "baidu":
        url = "https://www.baidu.com/s"
        params = {"wd": keyword, "ie": "utf-8"}
        parser = parse_baidu_related
    else:
        url = "https://www.google.com/search"
        params = {"q": keyword, "hl": "zh-CN", "num": "10", "filter": "0"}
        parser = parse_google_related

    page_candidates: list[str] = []
    page_blocked = False
    page_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            page_blocked = is_verification_page(engine, response)
            if not page_blocked:
                page_candidates = parser(response.text)
            break
        except httpx.HTTPError as exc:
            page_error = exc
            if attempt == 0:
                await asyncio.sleep(2.0)

    # 网页底部相关搜索始终优先；被验证、请求失败或结果偏少时，仅用同一搜索
    # 引擎的公开搜索联想补足，避免任务因一次页面结构变化直接中止。
    suggestions: list[str] = []
    used_suggestions = (
        page_blocked or page_error is not None or len(page_candidates) < 4
    )
    if used_suggestions:
        try:
            suggestions = await _fetch_suggestions(client, engine, keyword)
        except httpx.HTTPError as exc:
            if not page_candidates:
                reason = (
                    "触发验证" if page_blocked else type(page_error or exc).__name__
                )
                raise RuntimeError(
                    f"{ENGINE_NAMES[engine]}网页不可用（{reason}），搜索联想接口也请求失败"
                ) from exc

    candidates = _clean_candidates([*page_candidates, *suggestions])
    return RelatedResult(
        candidates=candidates,
        page_blocked=page_blocked,
        page_empty=not page_blocked and not page_candidates,
        used_suggestions=used_suggestions and bool(suggestions),
    )


async def run_keyword_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        job = await session.get(KeywordCollectionJob, job_id)
        if job is None:
            return
        job.status = KeywordJobStatus.running
        job.error_message = None
        await session.commit()

        existing_result = await session.execute(
            select(AccountKeyword).where(
                AccountKeyword.account_id == job.account_id
            )
        )
        existing = {
            item.normalized_keyword: item for item in existing_result.scalars()
        }
        destination = await session.get(KeywordJobDestination, job.id)
        destination_folder_id = destination.folder_id if destination else None
        seed_normalized = normalize_keyword(job.seed_keyword)
        # 每个引擎分别扩展。同一个词需要分别进入百度和谷歌搜索，不能因为
        # 百度先发现了它，就跳过谷歌对该词的相关搜索。
        seen_candidates_by_engine: dict[str, set[str]] = {}
        visited_queries: set[tuple[str, str]] = set()
        queue: list[tuple[int, int, int, str, str, str | None]] = []
        sequence = itertools.count()
        engines = (
            ("baidu", "google")
            if job.source == KeywordJobSource.both
            else (job.source.value,)
        )
        for engine in engines:
            seen_candidates_by_engine[engine] = {seed_normalized}
            heapq.heappush(
                queue,
                (
                    0,
                    len(job.seed_keyword),
                    next(sequence),
                    engine,
                    job.seed_keyword,
                    None,
                ),
            )

        errors: list[str] = []
        blocked_pages = {engine: 0 for engine in engines}
        empty_pages = {engine: 0 for engine in engines}
        fallback_pages = {engine: 0 for engine in engines}
        max_searches = min(300, max(20, job.target_count * 3))
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=20, follow_redirects=True
            ) as client:
                while queue and job.collected_count < job.target_count:
                    depth, _, _, engine, query, _ = heapq.heappop(queue)
                    query_key = (engine, normalize_keyword(query))
                    if query_key in visited_queries or depth > 6:
                        continue
                    if job.searched_count >= max_searches:
                        errors.append("已达到单次任务最大搜索页数")
                        break
                    visited_queries.add(query_key)
                    job.current_keyword = query
                    job.searched_count += 1
                    await session.commit()

                    if job.searched_count > 1:
                        await asyncio.sleep(random.uniform(*REQUEST_DELAY_RANGE))

                    try:
                        result = await _fetch_related(client, engine, query)
                    except RuntimeError as exc:
                        errors.append(str(exc))
                        await session.commit()
                        continue

                    blocked_pages[engine] += int(result.page_blocked)
                    empty_pages[engine] += int(result.page_empty)
                    fallback_pages[engine] += int(result.used_suggestions)

                    source = (
                        KeywordSource.baidu
                        if engine == "baidu"
                        else KeywordSource.google
                    )
                    for candidate in result.candidates:
                        normalized = normalize_keyword(candidate)
                        if normalized not in seen_candidates_by_engine[engine]:
                            seen_candidates_by_engine[engine].add(normalized)
                            heapq.heappush(
                                queue,
                                (
                                    depth + 1,
                                    len(candidate),
                                    next(sequence),
                                    engine,
                                    candidate,
                                    query,
                                ),
                            )
                        if normalized == seed_normalized:
                            continue
                        existing_keyword = existing.get(normalized)
                        if existing_keyword:
                            if existing_keyword.is_recycled:
                                existing_keyword.is_recycled = False
                                existing_keyword.recycled_at = None
                                job.collected_count += 1
                            if job.collected_count >= job.target_count:
                                break
                            continue
                        keyword_id = uuid.uuid4()
                        keyword_item = AccountKeyword(
                            id=keyword_id,
                            account_id=job.account_id,
                            keyword=candidate,
                            normalized_keyword=normalized,
                            source=source,
                            seed_keyword=job.seed_keyword,
                            parent_keyword=query,
                            depth=depth + 1,
                        )
                        session.add(keyword_item)
                        if destination_folder_id:
                            session.add(
                                KeywordFolderItem(
                                    keyword_id=keyword_id,
                                    folder_id=destination_folder_id,
                                )
                            )
                        existing[normalized] = keyword_item
                        job.collected_count += 1
                        if job.collected_count >= job.target_count:
                            break
                    await session.commit()

            job.current_keyword = None
            job.completed_at = datetime.now(UTC)
            if job.collected_count >= job.target_count:
                job.status = KeywordJobStatus.completed
            elif job.collected_count > 0:
                job.status = KeywordJobStatus.partial
            else:
                job.status = KeywordJobStatus.failed
            if errors:
                job.error_message = "；".join(dict.fromkeys(errors))[:2000]
            elif job.status != KeywordJobStatus.completed:
                shortage = job.target_count - job.collected_count
                details: list[str] = [f"仍缺少 {shortage} 个关键词"]
                for engine in engines:
                    engine_details: list[str] = []
                    if blocked_pages[engine]:
                        engine_details.append(f"{blocked_pages[engine]} 页触发验证")
                    if empty_pages[engine]:
                        engine_details.append(
                            f"{empty_pages[engine]} 页未解析到网页相关词"
                        )
                    if fallback_pages[engine]:
                        engine_details.append(
                            f"{fallback_pages[engine]} 页已用搜索联想补充"
                        )
                    if engine_details:
                        details.append(
                            f"{ENGINE_NAMES[engine]}：" + "，".join(engine_details)
                        )
                if len(details) == 1:
                    details.append("已遍历现有相关词，未发现更多不重复关键词")
                job.error_message = "；".join(details)[:2000]
            await session.commit()
        except Exception as exc:
            await session.rollback()
            job = await session.get(KeywordCollectionJob, job_id)
            if job:
                job.status = KeywordJobStatus.failed
                job.current_keyword = None
                job.error_message = f"任务异常：{type(exc).__name__}"
                job.completed_at = datetime.now(UTC)
                await session.commit()
