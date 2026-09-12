import uuid
from pathlib import Path

from app.core.config import settings
from app.services.account_storage import (
    ACCOUNT_SUBDIRECTORIES,
    delete_account_storage,
    initialize_account_storage,
)


def test_account_storage_layout_is_account_scoped() -> None:
    account_id = uuid.uuid4()
    profile_key = str(uuid.uuid4())
    root = Path("/var/lib/totod/accounts") / str(account_id) / profile_key

    assert str(account_id) in str(root)
    assert profile_key in str(root)
    assert set(ACCOUNT_SUBDIRECTORIES) == {
        "browser-profile",
        "screenshots",
        "logs",
        "exports",
    }


def test_delete_account_storage_removes_only_selected_profile(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "account_data_root", tmp_path)
    selected_id = uuid.uuid4()
    selected_key = str(uuid.uuid4())
    other_id = uuid.uuid4()
    other_key = str(uuid.uuid4())
    selected = initialize_account_storage(selected_id, selected_key)
    other = initialize_account_storage(other_id, other_key)
    (selected / "screenshots" / "failure.png").write_bytes(b"image")

    delete_account_storage(selected_id, selected_key)

    assert not selected.exists()
    assert other.is_dir()
