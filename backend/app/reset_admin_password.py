import asyncio
import getpass

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal, create_schema
from app.models.user import User
import app.models  # noqa: F401 - registers database models


async def reset_admin_password() -> None:
    await create_schema()
    username = input("需要重置的管理员账号: ").strip()
    if not username:
        raise SystemExit("管理员账号不能为空")

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            raise SystemExit("管理员账号不存在")

        password = getpass.getpass("新密码（至少12位）: ")
        confirmation = getpass.getpass("再次输入新密码: ")
        if len(password) < 12:
            raise SystemExit("管理员密码不能少于12位")
        if password != confirmation:
            raise SystemExit("两次输入的密码不一致")

        user.password_hash = hash_password(password)
        await session.commit()
    print("管理员密码重置成功")


if __name__ == "__main__":
    asyncio.run(reset_admin_password())
