import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.database import Database
from app.models import SessionCreate, SessionResponse, SessionStatus, UserSessionState
from app.services.opencode_session import OpenCodeSession
from app.services.session_manager import SessionManager

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")


class TestModels:
    def test_session_status_enum(self):
        assert SessionStatus.IDLE == "idle"
        assert SessionStatus.RUNNING == "running"
        assert SessionStatus.CLOSED == "closed"
        assert SessionStatus.DEAD == "dead"

    def test_session_create(self):
        session = SessionCreate(name="test", cwd="/home/user")
        assert session.name == "test"
        assert session.cwd == "/home/user"

    def test_session_response(self):
        response = SessionResponse(
            id=1,
            name="test",
            cwd="/home/user",
            status=SessionStatus.RUNNING,
            opencode_session_id="ses_test123",
            model=None,
            mode=None,
            last_output=None,
            last_output_fetched_at=None,
            created_at="2024-01-01T00:00:00",
            last_used="2024-01-01T00:00:00",
        )
        assert response.id == 1
        assert response.name == "test"

    def test_user_session_state(self):
        TEST_USER_ID = 123
        state = UserSessionState(user_id=TEST_USER_ID, current_session_id=1)
        assert state.user_id == TEST_USER_ID
        assert state.current_session_id == 1
        assert state.allowed is True


class TestConfig:
    def test_settings_defaults(self):
        settings = Settings(telegram_bot_token="test_token", opencode_executable="opencode")
        assert settings.telegram_bot_token == "test_token"
        assert settings.opencode_executable == "opencode"
        assert settings.log_level == "INFO"

    def test_settings_allowed_users(self):
        settings = Settings(telegram_bot_token="test_token", allowed_user_ids_str="1,2,3")
        assert settings.allowed_user_ids == [1, 2, 3]

    def test_settings_allowed_users_empty(self):
        settings = Settings(telegram_bot_token="test_token", allowed_user_ids_str="")
        assert settings.allowed_user_ids == []

    def test_settings_default_cwd(self):
        settings = Settings(telegram_bot_token="test_token", default_cwd="~/projects")
        assert settings.default_cwd == "~/projects"


class TestOpenCodeSession:
    @pytest.mark.asyncio
    async def test_session_creation(self):
        session = OpenCodeSession(
            session_id=1,
            name="test",
            cwd=Path("/tmp"),
        )
        assert session.session_id == 1
        assert session.name == "test"
        assert session.cwd == Path("/tmp")
        assert session.status == SessionStatus.IDLE
        assert session.process is None


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_is_user_allowed_empty_list(self):
        settings = Settings(telegram_bot_token="test", allowed_user_ids_str="")
        with patch("app.services.session_manager.settings", settings):
            db = Database("sqlite+aiosqlite:///:memory:")
            manager = SessionManager(db)
            assert manager.is_user_allowed(123) is True
            assert manager.is_user_allowed(999) is True

    @pytest.mark.asyncio
    async def test_is_user_allowed_with_list(self):
        settings = Settings(telegram_bot_token="test", allowed_user_ids_str="1,2,3")
        with patch("app.services.session_manager.settings", settings):
            db = Database("sqlite+aiosqlite:///:memory:")
            manager = SessionManager(db)
            assert manager.is_user_allowed(1) is True
            assert manager.is_user_allowed(2) is True
            assert manager.is_user_allowed(999) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
