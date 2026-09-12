import asyncio
import uuid


_account_browser_locks: dict[uuid.UUID, asyncio.Lock] = {}


def get_account_browser_lock(account_id: uuid.UUID) -> asyncio.Lock:
    lock = _account_browser_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _account_browser_locks[account_id] = lock
    return lock
