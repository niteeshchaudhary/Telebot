from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    CLOSED = "closed"
    DEAD = "dead"


class SessionBase(SQLModel):
    name: str = SQLField(index=True, unique=True, max_length=100)
    cwd: str = SQLField(max_length=500)
    status: SessionStatus = SQLField(default=SessionStatus.IDLE)
    opencode_session_id: str | None = SQLField(default=None, index=True, max_length=100)
    model: str | None = SQLField(default=None, max_length=200)
    mode: str | None = SQLField(default=None, max_length=50)
    last_output: str | None = SQLField(default=None)
    last_output_fetched_at: datetime | None = SQLField(default=None)


class Session(SessionBase, table=True):
    __tablename__ = "sessions"

    id: int | None = SQLField(default=None, primary_key=True)
    created_at: datetime = SQLField(default_factory=datetime.utcnow)
    last_used: datetime = SQLField(default_factory=datetime.utcnow)


class SessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cwd: str = Field(default="~", max_length=500)
    opencode_session_id: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class SessionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cwd: str | None = Field(default=None, max_length=500)
    status: SessionStatus | None = None
    model: str | None = Field(default=None, max_length=200)
    mode: str | None = Field(default=None, max_length=50)
    last_output: str | None = Field(default=None)
    last_output_fetched_at: datetime | None = Field(default=None)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cwd: str
    status: SessionStatus
    opencode_session_id: str | None
    model: str | None
    mode: str | None
    last_output: str | None
    last_output_fetched_at: datetime | None
    created_at: datetime
    last_used: datetime


class UserSessionState(BaseModel):
    user_id: int
    current_session_id: int | None = None
    allowed: bool = True


class UserStateBase(SQLModel):
    user_id: int = SQLField(index=True, unique=True)
    current_session_id: int | None = SQLField(default=None, index=True)


class UserState(UserStateBase, table=True):
    __tablename__ = "user_states"

    id: int | None = SQLField(default=None, primary_key=True)
    created_at: datetime = SQLField(default_factory=datetime.utcnow)
    updated_at: datetime = SQLField(default_factory=datetime.utcnow)


class UserSettings(BaseModel):
    allowed_user_ids: list[int] = Field(default_factory=list)


class StreamChunk(BaseModel):
    text: str
    is_error: bool = False


class CommandResult(BaseModel):
    success: bool
    message: str
    session_id: int | None = None
    session_name: str | None = None
