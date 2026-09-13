import io
import uuid
import zipfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole
from app.services.local_media import (
    LOCAL_IMAGE_MARKER,
    expand_local_image_prompt,
    insert_local_image,
    text_content_length,
)


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)
JPEG = b"\xff\xd8\xff\xe0" + b"test-image-content" + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-content"


def _create_user(client: TestClient, prefix: str) -> dict:
    return client.post(
        "/api/users",
        json={
            "username": f"{prefix}_{uuid.uuid4().hex[:8]}",
            "password": "test-password-123",
        },
    ).json()


def test_local_media_upload_extract_and_user_isolation() -> None:
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            first = _create_user(client, "media_first")
            second = _create_user(client, "media_second")
            current = {
                "user": SimpleNamespace(id=uuid.UUID(first["id"]), role=UserRole.user)
            }
            app.dependency_overrides[require_active_user] = lambda: current["user"]

            folder_response = client.post(
                "/api/local-media/folders", json={"name": "产品图片"}
            )
            assert folder_response.status_code == 201
            folder_id = folder_response.json()["id"]

            uploaded = client.post(
                "/api/local-media/upload",
                data={"folder_id": folder_id},
                files=[("files", ("商品图.jpg", JPEG, "image/jpeg"))],
            )
            assert uploaded.status_code == 201
            image = uploaded.json()["items"][0]
            assert image["kind"] == "image"
            assert image["public_url"].endswith(image["id"])
            public = client.get(f"/api/local-media/public/{image['id']}")
            assert public.status_code == 200
            assert public.content == JPEG

            archive_content = io.BytesIO()
            with zipfile.ZipFile(archive_content, "w") as archive:
                archive.writestr("nested/第一张.png", PNG)
                archive.writestr("第二张.jpg", JPEG)
                archive.writestr("说明.txt", "不是图片")
            archive_content.seek(0)
            archive_upload = client.post(
                "/api/local-media/upload",
                data={"folder_id": folder_id},
                files=[
                    (
                        "files",
                        ("产品图.zip", archive_content.getvalue(), "application/zip"),
                    )
                ],
            )
            assert archive_upload.status_code == 201
            archive_id = archive_upload.json()["items"][0]["id"]
            extracted = client.post(f"/api/local-media/{archive_id}/extract")
            assert extracted.status_code == 200
            assert extracted.json() == {"extracted_count": 2, "skipped_count": 1}
            listed = client.get(f"/api/local-media?folder_id={folder_id}").json()
            assert listed["total"] == 4

            current["user"] = SimpleNamespace(
                id=uuid.UUID(second["id"]), role=UserRole.user
            )
            assert client.get("/api/local-media").json()["total"] == 0
            forbidden = client.post(
                "/api/local-media/bulk-delete", json={"asset_ids": [image["id"]]}
            )
            assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.pop(require_active_user, None)


def test_local_image_prompt_and_markdown_insertion() -> None:
    expanded = expand_local_image_prompt("正文这里放{本地图片}", True)
    assert LOCAL_IMAGE_MARKER in expanded
    asset = SimpleNamespace(id=uuid.uuid4(), original_name="产品图.jpg")
    content = insert_local_image(f"第一段。\n\n{LOCAL_IMAGE_MARKER}\n\n第二段。", asset)
    assert "![产品图.jpg](https://totod.cn/api/local-media/public/" in content
    assert LOCAL_IMAGE_MARKER not in content
    assert text_content_length(content) == len("第一段。第二段。")
