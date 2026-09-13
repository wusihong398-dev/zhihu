import mimetypes
import re
import tarfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.article import Article
from app.models.local_media import LocalMediaAsset, LocalMediaFolder, LocalMediaKind
from app.models.user import User
from app.schemas.local_media import (
    LocalMediaAssetRead,
    LocalMediaBulkRequest,
    LocalMediaBulkResult,
    LocalMediaExtractResponse,
    LocalMediaFolderCreate,
    LocalMediaFolderRead,
    LocalMediaFolderUpdate,
    LocalMediaListResponse,
    LocalMediaUploadResponse,
)
from app.services.local_media import asset_path, public_asset_url, user_media_root


router = APIRouter(tags=["local-media"])

IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tar.gz", ".tgz"}
MAX_UPLOAD_SIZE = 200 * 1024 * 1024
MAX_IMAGE_SIZE = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
MAX_EXTRACTED_SIZE = 1024 * 1024 * 1024


def _clean_name(value: str) -> tuple[str, str]:
    name = " ".join(value.split())
    if not name:
        raise HTTPException(status_code=422, detail="名称不能为空")
    return name, name.casefold()


def _safe_original_name(value: str | None) -> str:
    name = (value or "未命名文件").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:255] or "未命名文件"


def _compound_suffix(name: str) -> str:
    lowered = name.casefold()
    if lowered.endswith(".tar.gz"):
        return ".tar.gz"
    return Path(lowered).suffix


def _image_signature_valid(data: bytes, extension: str) -> bool:
    checks = {
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        ".gif": data.startswith((b"GIF87a", b"GIF89a")),
        ".bmp": data.startswith(b"BM"),
        ".tif": data.startswith((b"II*\x00", b"MM\x00*")),
        ".tiff": data.startswith((b"II*\x00", b"MM\x00*")),
        ".avif": len(data) >= 12 and data[4:8] == b"ftyp" and b"avif" in data[8:32],
    }
    return checks.get(extension, False)


async def _folder_for_user(
    folder_id: uuid.UUID, user: User, db: AsyncSession
) -> LocalMediaFolder:
    folder = await db.get(LocalMediaFolder, folder_id)
    if folder is None or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="图片文件夹不存在")
    return folder


async def _asset_for_user(
    asset_id: uuid.UUID, user: User, db: AsyncSession
) -> LocalMediaAsset:
    asset = await db.get(LocalMediaAsset, asset_id)
    if asset is None or asset.user_id != user.id:
        raise HTTPException(status_code=404, detail="图片或压缩包不存在")
    return asset


def _asset_read(
    asset: LocalMediaAsset, folder_name: str | None = None
) -> LocalMediaAssetRead:
    return LocalMediaAssetRead.model_validate(asset).model_copy(
        update={
            "folder_name": folder_name,
            "public_url": public_asset_url(asset)
            if asset.kind == LocalMediaKind.image
            else None,
        }
    )


@router.get("/local-media/public/{asset_id}", include_in_schema=False)
async def public_local_image(
    asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> FileResponse:
    asset = await db.get(LocalMediaAsset, asset_id)
    if asset is None or asset.kind != LocalMediaKind.image:
        raise HTTPException(status_code=404, detail="图片不存在")
    path = asset_path(asset)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return FileResponse(
        path,
        media_type=asset.mime_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/local-media/folders", response_model=list[LocalMediaFolderRead])
async def list_local_media_folders(
    db: AsyncSession = Depends(get_db), user: User = Depends(require_active_user)
) -> list[LocalMediaFolderRead]:
    result = await db.execute(
        select(
            LocalMediaFolder,
            func.count(LocalMediaAsset.id).filter(
                LocalMediaAsset.kind == LocalMediaKind.image
            ),
            func.count(LocalMediaAsset.id).filter(
                LocalMediaAsset.kind == LocalMediaKind.archive
            ),
        )
        .outerjoin(LocalMediaAsset, LocalMediaAsset.folder_id == LocalMediaFolder.id)
        .where(LocalMediaFolder.user_id == user.id)
        .group_by(LocalMediaFolder.id)
        .order_by(LocalMediaFolder.name.asc())
    )
    return [
        LocalMediaFolderRead.model_validate(folder).model_copy(
            update={"image_count": image_count, "archive_count": archive_count}
        )
        for folder, image_count, archive_count in result
    ]


@router.post(
    "/local-media/folders",
    response_model=LocalMediaFolderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_local_media_folder(
    payload: LocalMediaFolderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalMediaFolderRead:
    name, normalized_name = _clean_name(payload.name)
    folder = LocalMediaFolder(
        user_id=user.id, name=name, normalized_name=normalized_name
    )
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名图片文件夹已存在") from exc
    await db.refresh(folder)
    return LocalMediaFolderRead.model_validate(folder)


@router.patch("/local-media/folders/{folder_id}", response_model=LocalMediaFolderRead)
async def update_local_media_folder(
    folder_id: uuid.UUID,
    payload: LocalMediaFolderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalMediaFolderRead:
    folder = await _folder_for_user(folder_id, user, db)
    folder.name, folder.normalized_name = _clean_name(payload.name)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名图片文件夹已存在") from exc
    await db.refresh(folder)
    counts = await db.execute(
        select(
            func.count(LocalMediaAsset.id).filter(
                LocalMediaAsset.kind == LocalMediaKind.image
            ),
            func.count(LocalMediaAsset.id).filter(
                LocalMediaAsset.kind == LocalMediaKind.archive
            ),
        ).where(LocalMediaAsset.folder_id == folder.id)
    )
    image_count, archive_count = counts.one()
    return LocalMediaFolderRead.model_validate(folder).model_copy(
        update={"image_count": image_count, "archive_count": archive_count}
    )


@router.delete(
    "/local-media/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_local_media_folder(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> None:
    folder = await _folder_for_user(folder_id, user, db)
    await db.delete(folder)
    await db.commit()


@router.get("/local-media", response_model=LocalMediaListResponse)
async def list_local_media(
    q: str = Query(default="", max_length=255),
    folder_id: uuid.UUID | None = Query(default=None),
    unfiled: bool = Query(default=False),
    kind: LocalMediaKind | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalMediaListResponse:
    filters = [LocalMediaAsset.user_id == user.id]
    if folder_id:
        await _folder_for_user(folder_id, user, db)
        filters.append(LocalMediaAsset.folder_id == folder_id)
    elif unfiled:
        filters.append(LocalMediaAsset.folder_id.is_(None))
    if kind:
        filters.append(LocalMediaAsset.kind == kind)
    if q.strip():
        filters.append(LocalMediaAsset.original_name.ilike(f"%{q.strip()}%"))
    total = await db.scalar(select(func.count(LocalMediaAsset.id)).where(*filters))
    result = await db.execute(
        select(LocalMediaAsset, LocalMediaFolder.name)
        .outerjoin(LocalMediaFolder, LocalMediaFolder.id == LocalMediaAsset.folder_id)
        .where(*filters)
        .order_by(LocalMediaAsset.created_at.desc(), LocalMediaAsset.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return LocalMediaListResponse(
        items=[_asset_read(asset, folder_name) for asset, folder_name in result],
        total=total or 0,
    )


async def _save_upload(
    upload: UploadFile,
    folder_id: uuid.UUID | None,
    user: User,
) -> LocalMediaAsset:
    original_name = _safe_original_name(upload.filename)
    extension = _compound_suffix(original_name)
    if extension in IMAGE_EXTENSIONS:
        kind = LocalMediaKind.image
        mime_type = IMAGE_EXTENSIONS[extension]
    elif extension in ARCHIVE_EXTENSIONS:
        kind = LocalMediaKind.archive
        mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    else:
        raise HTTPException(
            status_code=415,
            detail=f"{original_name} 格式不支持；图片支持 JPG、PNG、WebP、GIF、BMP、TIFF、AVIF，压缩包支持 ZIP、TAR、TAR.GZ、TGZ",
        )
    storage_key = f"{uuid.uuid4().hex}{extension}"
    path = user_media_root(user.id) / storage_key
    size = 0
    header = b""
    try:
        with path.open("xb") as target:
            while chunk := await upload.read(1024 * 1024):
                if not header:
                    header = chunk[:64]
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413, detail=f"{original_name} 超过 200MB"
                    )
                target.write(chunk)
        if not size:
            raise HTTPException(status_code=422, detail=f"{original_name} 是空文件")
        if kind == LocalMediaKind.image:
            if size > MAX_IMAGE_SIZE:
                raise HTTPException(
                    status_code=413, detail=f"{original_name} 超过单张图片 25MB 限制"
                )
            if not _image_signature_valid(header, extension):
                raise HTTPException(
                    status_code=415, detail=f"{original_name} 的文件内容不是有效图片"
                )
        elif extension == ".zip" and not zipfile.is_zipfile(path):
            raise HTTPException(
                status_code=415, detail=f"{original_name} 不是有效 ZIP 压缩包"
            )
        elif extension != ".zip" and not tarfile.is_tarfile(path):
            raise HTTPException(
                status_code=415, detail=f"{original_name} 不是有效 TAR 压缩包"
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return LocalMediaAsset(
        user_id=user.id,
        folder_id=folder_id,
        kind=kind,
        original_name=original_name,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size,
    )


@router.post(
    "/local-media/upload",
    response_model=LocalMediaUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_local_media(
    files: list[UploadFile] = File(...),
    folder_id: uuid.UUID | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalMediaUploadResponse:
    if not files or len(files) > 100:
        raise HTTPException(status_code=422, detail="每次请选择 1 至 100 个文件")
    if folder_id:
        await _folder_for_user(folder_id, user, db)
    assets: list[LocalMediaAsset] = []
    try:
        for upload in files:
            asset = await _save_upload(upload, folder_id, user)
            assets.append(asset)
            db.add(asset)
        await db.commit()
    except Exception:
        await db.rollback()
        for asset in assets:
            asset_path(asset).unlink(missing_ok=True)
        raise
    for asset in assets:
        await db.refresh(asset)
    return LocalMediaUploadResponse(
        items=[_asset_read(asset) for asset in assets], uploaded_count=len(assets)
    )


def _archive_members(asset: LocalMediaAsset):
    path = asset_path(asset)
    if asset.storage_key.endswith(".zip"):
        archive = zipfile.ZipFile(path)
        members = [item for item in archive.infolist() if not item.is_dir()]
        return archive, members, lambda item: archive.open(item)
    archive = tarfile.open(path, mode="r:*")
    members = [item for item in archive.getmembers() if item.isfile()]
    return archive, members, lambda item: archive.extractfile(item)


@router.post(
    "/local-media/{asset_id}/extract", response_model=LocalMediaExtractResponse
)
async def extract_local_media_archive(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalMediaExtractResponse:
    asset = await _asset_for_user(asset_id, user, db)
    if asset.kind != LocalMediaKind.archive:
        raise HTTPException(status_code=422, detail="该文件不是压缩包")
    if asset.extracted:
        raise HTTPException(
            status_code=409, detail="该压缩包已经解压过，如需再次解压请重新上传"
        )
    created: list[LocalMediaAsset] = []
    skipped = 0
    total_size = 0
    archive = None
    try:
        archive, members, opener = _archive_members(asset)
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise HTTPException(status_code=413, detail="压缩包文件数量超过 2000 个")
        for member in members:
            member_name = _safe_original_name(
                member.filename if isinstance(member, zipfile.ZipInfo) else member.name
            )
            extension = _compound_suffix(member_name)
            member_size = (
                member.file_size if isinstance(member, zipfile.ZipInfo) else member.size
            )
            if (
                extension not in IMAGE_EXTENSIONS
                or member_size <= 0
                or member_size > MAX_IMAGE_SIZE
            ):
                skipped += 1
                continue
            total_size += member_size
            if total_size > MAX_EXTRACTED_SIZE:
                raise HTTPException(
                    status_code=413, detail="压缩包解压后的图片总量超过 1GB"
                )
            storage_key = f"{uuid.uuid4().hex}{extension}"
            path = user_media_root(user.id) / storage_key
            source = opener(member)
            if source is None:
                skipped += 1
                continue
            header = b""
            written = 0
            with source, path.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    if not header:
                        header = chunk[:64]
                    written += len(chunk)
                    if written > MAX_IMAGE_SIZE:
                        break
                    target.write(chunk)
            if written != member_size or not _image_signature_valid(header, extension):
                path.unlink(missing_ok=True)
                skipped += 1
                continue
            image = LocalMediaAsset(
                user_id=user.id,
                folder_id=asset.folder_id,
                kind=LocalMediaKind.image,
                original_name=member_name,
                storage_key=storage_key,
                mime_type=IMAGE_EXTENSIONS[extension],
                size_bytes=written,
            )
            created.append(image)
            db.add(image)
        asset.extracted = True
        asset.extracted_at = datetime.now(UTC)
        await db.commit()
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        await db.rollback()
        for image in created:
            asset_path(image).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"压缩包解压失败：{exc}") from exc
    except Exception:
        await db.rollback()
        for image in created:
            asset_path(image).unlink(missing_ok=True)
        raise
    finally:
        if archive is not None:
            archive.close()
    return LocalMediaExtractResponse(
        extracted_count=len(created), skipped_count=skipped
    )


@router.post("/local-media/bulk-delete", response_model=LocalMediaBulkResult)
async def delete_local_media(
    payload: LocalMediaBulkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> LocalMediaBulkResult:
    asset_ids = list(dict.fromkeys(payload.asset_ids))
    result = await db.execute(
        select(LocalMediaAsset).where(
            LocalMediaAsset.user_id == user.id, LocalMediaAsset.id.in_(asset_ids)
        )
    )
    assets = list(result.scalars())
    if len(assets) != len(asset_ids):
        raise HTTPException(status_code=404, detail="部分图片不存在或不属于当前用户")
    referenced = await db.scalar(
        select(func.count(Article.id)).where(Article.local_image_id.in_(asset_ids))
    )
    if referenced:
        raise HTTPException(
            status_code=409, detail="所选图片已被文章引用，不能删除；可先删除对应文章"
        )
    for asset in assets:
        asset_path(asset).unlink(missing_ok=True)
        await db.delete(asset)
    await db.commit()
    return LocalMediaBulkResult(affected_count=len(assets))
