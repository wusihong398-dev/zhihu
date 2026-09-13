import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import ZhihuAccount
from app.models.keyword import AccountKeyword
from app.models.keyword_folder import KeywordFolder, KeywordFolderItem
from app.models.product import PromotedProduct
from app.models.schedule import OperationSchedule
from app.services.schedule_runner import calculate_next_run


@dataclass(frozen=True)
class AccountConfigSyncResult:
    products: int
    keyword_folders: int
    keywords: int
    schedules: int


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _remap_schedule_config(
    value: str,
    product_ids: dict[str, str],
    folder_ids: dict[str, str],
) -> str:
    try:
        config = json.loads(value or "{}")
    except (TypeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    if config.get("product_id") in product_ids:
        config["product_id"] = product_ids[config["product_id"]]
    if config.get("folder_id") in folder_ids:
        config["folder_id"] = folder_ids[config["folder_id"]]
    return json.dumps(config, ensure_ascii=False)


async def sync_account_configuration(
    source: ZhihuAccount,
    target: ZhihuAccount,
    schedule_user_id: uuid.UUID,
    db: AsyncSession,
) -> AccountConfigSyncResult:
    """Merge a source account's reusable operating configuration into a target.

    Login profiles, cookies, generated content, questions, answers and publish
    history are intentionally excluded so every Zhihu identity remains isolated.
    """
    target.daily_article_limit = source.daily_article_limit
    target.daily_answer_limit = source.daily_answer_limit
    target.recycle_keywords_after_use = source.recycle_keywords_after_use
    target.auto_restore_keywords = source.auto_restore_keywords
    target.keyword_restore_threshold = source.keyword_restore_threshold
    target.timezone = source.timezone

    source_products = list(
        (
            await db.execute(
                select(PromotedProduct).where(
                    PromotedProduct.account_id == source.id
                )
            )
        ).scalars()
    )
    target_products = list(
        (
            await db.execute(
                select(PromotedProduct).where(
                    PromotedProduct.account_id == target.id
                )
            )
        ).scalars()
    )
    products_by_name = {
        _normalized(item.name): item for item in target_products
    }
    product_ids: dict[str, str] = {}
    product_fields = (
        "name",
        "category",
        "description",
        "selling_points",
        "target_audience",
        "promotion_url",
        "content_requirements",
        "forbidden_terms",
        "enabled",
    )
    for source_product in source_products:
        key = _normalized(source_product.name)
        target_product = products_by_name.get(key)
        if target_product is None:
            target_product = PromotedProduct(account_id=target.id)
            db.add(target_product)
            products_by_name[key] = target_product
        for field in product_fields:
            setattr(target_product, field, getattr(source_product, field))
        await db.flush()
        product_ids[str(source_product.id)] = str(target_product.id)

    source_folders = list(
        (
            await db.execute(
                select(KeywordFolder).where(KeywordFolder.account_id == source.id)
            )
        ).scalars()
    )
    target_folders = list(
        (
            await db.execute(
                select(KeywordFolder).where(KeywordFolder.account_id == target.id)
            )
        ).scalars()
    )
    folders_by_name = {item.normalized_name: item for item in target_folders}
    folder_ids: dict[str, str] = {}
    for source_folder in source_folders:
        target_folder = folders_by_name.get(source_folder.normalized_name)
        if target_folder is None:
            target_folder = KeywordFolder(
                account_id=target.id,
                name=source_folder.name,
                normalized_name=source_folder.normalized_name,
            )
            db.add(target_folder)
            folders_by_name[source_folder.normalized_name] = target_folder
        else:
            target_folder.name = source_folder.name
        await db.flush()
        folder_ids[str(source_folder.id)] = str(target_folder.id)

    source_keywords = list(
        (
            await db.execute(
                select(AccountKeyword).where(AccountKeyword.account_id == source.id)
            )
        ).scalars()
    )
    target_keywords = list(
        (
            await db.execute(
                select(AccountKeyword).where(AccountKeyword.account_id == target.id)
            )
        ).scalars()
    )
    keywords_by_text = {
        item.normalized_keyword: item for item in target_keywords
    }
    keyword_ids: dict[uuid.UUID, uuid.UUID] = {}
    keyword_fields = (
        "keyword",
        "normalized_keyword",
        "source",
        "seed_keyword",
        "parent_keyword",
        "depth",
        "is_recycled",
        "used_count",
        "last_used_at",
        "recycled_at",
    )
    for source_keyword in source_keywords:
        target_keyword = keywords_by_text.get(source_keyword.normalized_keyword)
        if target_keyword is None:
            target_keyword = AccountKeyword(account_id=target.id)
            db.add(target_keyword)
            keywords_by_text[source_keyword.normalized_keyword] = target_keyword
        for field in keyword_fields:
            setattr(target_keyword, field, getattr(source_keyword, field))
        await db.flush()
        keyword_ids[source_keyword.id] = target_keyword.id

    source_folder_items = {
        keyword_id: folder_id
        for keyword_id, folder_id in (
            await db.execute(
                select(
                    KeywordFolderItem.keyword_id,
                    KeywordFolderItem.folder_id,
                )
                .join(
                    AccountKeyword,
                    AccountKeyword.id == KeywordFolderItem.keyword_id,
                )
                .where(AccountKeyword.account_id == source.id)
            )
        ).all()
    }
    target_folder_items = {
        item.keyword_id: item
        for item in (
            (
                await db.execute(
                    select(KeywordFolderItem)
                    .join(
                        AccountKeyword,
                        AccountKeyword.id == KeywordFolderItem.keyword_id,
                    )
                    .where(AccountKeyword.account_id == target.id)
                )
            ).scalars()
        )
    }
    for source_keyword_id, target_keyword_id in keyword_ids.items():
        source_folder_id = source_folder_items.get(source_keyword_id)
        current_item = target_folder_items.get(target_keyword_id)
        if source_folder_id is None:
            if current_item is not None:
                await db.delete(current_item)
            continue
        target_folder_id = uuid.UUID(folder_ids[str(source_folder_id)])
        if current_item is None:
            db.add(
                KeywordFolderItem(
                    keyword_id=target_keyword_id,
                    folder_id=target_folder_id,
                )
            )
        else:
            current_item.folder_id = target_folder_id

    source_schedules = list(
        (
            await db.execute(
                select(OperationSchedule).where(
                    OperationSchedule.account_id == source.id
                )
            )
        ).scalars()
    )
    target_schedules = list(
        (
            await db.execute(
                select(OperationSchedule).where(
                    OperationSchedule.account_id == target.id
                )
            )
        ).scalars()
    )
    schedules_by_key = {
        (item.task_type, _normalized(item.name)): item
        for item in target_schedules
    }
    for source_schedule in source_schedules:
        key = (source_schedule.task_type, _normalized(source_schedule.name))
        target_schedule = schedules_by_key.get(key)
        if target_schedule is None:
            target_schedule = OperationSchedule(
                user_id=schedule_user_id,
                account_id=target.id,
                name=source_schedule.name,
                task_type=source_schedule.task_type,
            )
            db.add(target_schedule)
            schedules_by_key[key] = target_schedule
        target_schedule.enabled = source_schedule.enabled
        target_schedule.hour = source_schedule.hour
        target_schedule.minute = source_schedule.minute
        target_schedule.weekdays = source_schedule.weekdays
        target_schedule.config_json = _remap_schedule_config(
            source_schedule.config_json, product_ids, folder_ids
        )
        target_schedule._account_timezone = target.timezone
        target_schedule.next_run_at = (
            calculate_next_run(target_schedule) if target_schedule.enabled else None
        )

    await db.flush()
    return AccountConfigSyncResult(
        products=len(source_products),
        keyword_folders=len(source_folders),
        keywords=len(source_keywords),
        schedules=len(source_schedules),
    )
