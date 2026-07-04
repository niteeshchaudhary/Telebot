from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models import Session as SessionModel
from app.models import SessionCreate, SessionStatus, UserState


class Database:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or settings.database_url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self.database_url,
                echo=settings.log_level == "DEBUG",
                pool_pre_ping=True,
            )
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                self.engine, class_=AsyncSession, expire_on_commit=False
            )
        return self._session_factory

    async def init_db(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, session_data: SessionCreate) -> SessionModel:
        session = SessionModel.model_validate(session_data)
        self.session.add(session)
        await self.session.flush()
        await self.session.refresh(session)
        return session

    async def get(self, session_id: int) -> SessionModel | None:
        return await self.session.get(SessionModel, session_id)

    async def get_by_name(self, name: str) -> SessionModel | None:
        result = await self.session.exec(
            select(SessionModel).where(SessionModel.name == name)
        )
        return result.first()

    async def list(self, status: SessionStatus | None = None) -> list[SessionModel]:
        query = select(SessionModel).order_by(SessionModel.last_used.desc())  # type: ignore
        if status:
            query = query.where(SessionModel.status == status)
        result = await self.session.exec(query)
        return list(result.all())

    async def update(self, session: SessionModel, data: Mapping[str, object]) -> SessionModel:
        for key, value in data.items():
            setattr(session, key, value)
        self.session.add(session)
        await self.session.flush()
        await self.session.refresh(session)
        return session

    async def delete(self, session: SessionModel) -> None:
        await self.session.delete(session)


class UserStateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_current_session(self, user_id: int) -> int | None:
        result = await self.session.exec(
            select(UserState).where(UserState.user_id == user_id)
        )
        user_state = result.first()
        return user_state.current_session_id if user_state else None

    async def set_current_session(self, user_id: int, session_id: int | None) -> None:
        result = await self.session.exec(
            select(UserState).where(UserState.user_id == user_id)
        )
        user_state = result.first()

        if user_state:
            user_state.current_session_id = session_id
            user_state.updated_at = datetime.utcnow()
        else:
            user_state = UserState(user_id=user_id, current_session_id=session_id)
            self.session.add(user_state)

        await self.session.flush()


db = Database()
