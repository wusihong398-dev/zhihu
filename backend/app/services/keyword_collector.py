import asyncio
import heapq
import itertools
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.keyword import AccountKeyword, KeywordSource
from app.models.keyword_job import (
    KeywordCollectionJob,
    KeywordJobSource,
    KeywordJobStatus,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


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
                values.extend(node.get_text(" ", strip=True) for node in container.select("a"))
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
            if query and text:
                values.append(text)
    if not values:
        label = soup.find(string=re.compile(r"用户还搜索了|相关搜索|People also search for|Related searches", re.I))
        container = label.parent.parent.parent if label and label.parent else None
        if container:
            values.extend(node.get_text(" ", strip=True) for node in container.select("a[href*='search']"))
    return _clean_candidates(values)


async def _fetch_related(client: httpx.AsyncClient, engine: str, keyword: str) -> list[str]:
    if engine == "baidu":
        url = f"https://www.baidu.com/s?wd={quote_plus(keyword)}"
        parser = parse_baidu_related
    else:
        url = f"https://www.google.com/search?q={quote_plus(keyword)}&hl=zh-CN&num=10"
        parser = parse_google_related
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return parser(response.text)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{engine} 搜索请求失败：{type(last_error).__name__}")


async def run_keyword_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        job = await session.get(KeywordCollectionJob, job_id)
        if job is None:
            return
        job.status = KeywordJobStatus.running
        job.error_message = None
        await session.commit()

        existing_result = await session.execute(
            select(AccountKeyword.normalized_keyword).where(
                AccountKeyword.account_id == job.account_id
            )
        )
        existing = set(existing_result.scalars())
        seed_normalized = normalize_keyword(job.seed_keyword)
        seen_candidates = {seed_normalized}
        visited_queries: set[tuple[str, str]] = set()
        queue: list[tuple[int, int, int, str, str, str | None]] = []
        sequence = itertools.count()
        engines = (
            ("baidu", "google")
            if job.source == KeywordJobSource.both
            else (job.source.value,)
        )
        for engine in engines:
            heapq.heappush(
                queue,
                (0, len(job.seed_keyword), next(sequence), engine, job.seed_keyword, None),
            )

        errors: list[str] = []
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

                    try:
                        candidates = await _fetch_related(client, engine, query)
                    except RuntimeError as exc:
                        errors.append(str(exc))
                        await session.commit()
                        continue

                    source = KeywordSource.baidu if engine == "baidu" else KeywordSource.google
                    for candidate in candidates:
                        normalized = normalize_keyword(candidate)
                        if normalized not in seen_candidates:
                            seen_candidates.add(normalized)
                            heapq.heappush(
                                queue,
                                (depth + 1, len(candidate), next(sequence), engine, candidate, query),
                            )
                        if normalized in existing or normalized == seed_normalized:
                            continue
                        session.add(
                            AccountKeyword(
                                account_id=job.account_id,
                                keyword=candidate,
                                normalized_keyword=normalized,
                                source=source,
                                seed_keyword=job.seed_keyword,
                                parent_keyword=query,
                                depth=depth + 1,
                            )
                        )
                        existing.add(normalized)
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
                job.error_message = "搜索页面没有返回足够的相关词，可能触发了搜索引擎验证"
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
