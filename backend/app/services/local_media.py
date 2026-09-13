import re
import uuid
from pathlib import Path

from app.core.config import settings
from app.models.local_media import LocalMediaAsset


LOCAL_IMAGE_VARIABLE = "{本地图片}"
LOCAL_IMAGE_MARKER = "[[TOTOD_LOCAL_IMAGE]]"
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^\)]+\)")


def user_media_root(user_id: uuid.UUID) -> Path:
    root = settings.media_data_root / str(user_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def asset_path(asset: LocalMediaAsset) -> Path:
    root = user_media_root(asset.user_id).resolve()
    path = (root / asset.storage_key).resolve()
    if path.parent != root:
        raise ValueError("图片存储路径校验失败")
    return path


def public_asset_url(asset: LocalMediaAsset) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/local-media/public/{asset.id}"


def expand_local_image_prompt(template: str, enabled: bool) -> str:
    if not enabled:
        return template
    instruction = (
        f"请在正文最适合配图的位置单独保留标记 {LOCAL_IMAGE_MARKER}，"
        "标记只出现一次，不要解释这个标记。"
    )
    return template.replace(LOCAL_IMAGE_VARIABLE, instruction)


def insert_local_image(content: str, asset: LocalMediaAsset | None) -> str:
    if asset is None:
        return content.replace(LOCAL_IMAGE_MARKER, "").strip()
    markdown = f"![{asset.original_name}]({public_asset_url(asset)})"
    if LOCAL_IMAGE_MARKER in content:
        content = content.replace(LOCAL_IMAGE_MARKER, markdown, 1)
        return content.replace(LOCAL_IMAGE_MARKER, "").strip()
    paragraphs = re.split(r"(\n\s*\n)", content, maxsplit=1)
    if len(paragraphs) >= 3:
        return f"{paragraphs[0]}{paragraphs[1]}{markdown}\n\n{paragraphs[2]}".strip()
    return f"{content.rstrip()}\n\n{markdown}".strip()


def text_content_length(value: str) -> int:
    return len(re.sub(r"\s+", "", MARKDOWN_IMAGE_RE.sub("", value)))
