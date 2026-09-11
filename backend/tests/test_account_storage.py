import uuid
from pathlib import Path

from app.services.account_storage import ACCOUNT_SUBDIRECTORIES


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

