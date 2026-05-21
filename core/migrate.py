"""One-time migration: add user_id columns, create default users, backfill existing data."""

import logging

from sqlalchemy import text, inspect

from config import settings
from core.auth import hash_password
from models.user import User

logger = logging.getLogger(__name__)


async def run_migrations(engine, db_session_factory):
    """Run auth migrations on startup. Safe to call multiple times."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with engine.begin() as conn:
        # 1. Create users + api_keys tables if they don't exist
        from models.conversation import Base
        from models import user  # noqa — ensure User/ApiKey in metadata
        await conn.run_sync(Base.metadata.create_all)

        # 2. Check if user_id columns exist on existing tables
        def _has_column(connection, table: str, column: str) -> bool:
            insp = inspect(connection)
            return column in [c["name"] for c in insp.get_columns(table)]

        tables_needing_user_id = ["conversations", "credentials", "macros", "spaces"]

        for table in tables_needing_user_id:
            try:
                has_col = await conn.run_sync(lambda c: _has_column(c, table, "user_id"))
                if not has_col:
                    logger.info(f"Adding user_id column to {table}")
                    await conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)"
                    ))
            except Exception as e:
                logger.warning(f"Migration for {table}.user_id: {e}")

    # 3. Create default admin user if no users exist
    async with db_session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(User))
        existing = result.scalars().first()

        if not existing:
            logger.info("Creating default admin user")
            admin = User(
                username="admin",
                email="admin@alphabetty.local",
                password_hash=hash_password("admin"),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)

            # Backfill: assign all existing data to admin
            admin_id = admin.id
            async with engine.begin() as conn:
                for table in tables_needing_user_id:
                    try:
                        await conn.execute(text(
                            f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"
                        ), {"uid": admin_id})
                    except Exception as e:
                        logger.warning(f"Backfill {table}: {e}")

            logger.info(f"Created admin user (id={admin.id}) and backfilled existing data")

    # 4. Create demo user if enabled and doesn't exist
    if settings.demo_enabled:
        async with db_session_factory() as db:
            result = await db.execute(select(User).where(User.username == settings.demo_username))
            demo = result.scalar_one_or_none()
            if not demo:
                logger.info("Creating demo user")
                demo = User(
                    username=settings.demo_username,
                    password_hash=hash_password(settings.demo_password),
                    role="user",
                    is_active=True,
                )
                db.add(demo)
                await db.commit()
                logger.info(f"Created demo user '{settings.demo_username}'")
