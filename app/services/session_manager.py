import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from app.config import settings
from app.database import Database, SessionRepository, UserStateRepository
from app.logging import StructuredLogger, get_logger
from app.models import Session, SessionCreate, SessionStatus, UserSessionState
from app.models import Session as SessionModel

logger = StructuredLogger(get_logger(__name__))


class SessionManager:
    def __init__(self, database: Database, opencode_executable: str = "opencode"):
        self._database = database
        self._opencode_executable = opencode_executable
        self._user_states: dict[int, UserSessionState] = {}
        self._lock = asyncio.Lock()

    async def _get_repo(self) -> SessionRepository:
        async with self._database.session() as session:
            return SessionRepository(session)

    async def _get_user_repo(self) -> UserStateRepository:
        async with self._database.session() as session:
            return UserStateRepository(session)

    async def _run_opencode(  # noqa: PLR0912
        self, cwd: Path, session_id: str | None, message: str, model: str | None = None, mode: str | None = None,
        callback=None
    ) -> tuple[str | None, str | None]:
        """Run opencode with a message and return (session_id, output).
        
        Args:
            callback: Optional async callback function to receive incremental updates
                     Signature: async def callback(event_type: str, data: dict) -> None
        """
        cmd = [self._opencode_executable, "run", "--format", "json"]

        if session_id:
            cmd.extend(["--session", session_id])
        else:
            cmd.extend(["--title", "Telegram session"])

        if model:
            cmd.extend(["--model", model])
        
        if mode:
            cmd.extend(["--agent", mode])

        cmd.extend(["--dir", str(cwd), message])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            
            output_lines = []
            
            # Read stdout line-by-line for streaming
            while True:
                if proc.stdout is None:
                    break
                    
                line = await proc.stdout.readline()
                if not line:
                    break
                    
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                
                output_lines.append(line_str)
                
                # Parse and send callback if provided
                if callback:
                    try:
                        data = json.loads(line_str)
                        await callback(data)
                    except json.JSONDecodeError:
                        pass
            
            # Wait for process to complete
            await proc.wait()
            
            # Get stderr
            stderr_data = await proc.stderr.read() if proc.stderr else b""
            error = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
            
            output = "\n".join(output_lines)

            # Parse JSON output to extract session ID if new session
            new_session_id = None
            if not session_id:
                # Check both stdout and stderr for session ID
                combined_output = output + "\n" + error if error else output
                for raw_line in combined_output.strip().split("\n"):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # OpenCode outputs sessionID at top level in various event types
                        if data.get("sessionID"):
                            new_session_id = data["sessionID"]
                            break
                        # Also check for nested session.id (older format)
                        if data.get("type") == "session" and data.get("session", {}).get("id"):
                            new_session_id = data["session"]["id"]
                            break
                    except json.JSONDecodeError:
                        continue

            if output:
                return new_session_id, output
            elif error:
                return new_session_id, error
            else:
                return new_session_id, None

        except TimeoutError:
            logger.exception("opencode_run_timeout", cwd=str(cwd))
            return None, "Timeout: OpenCode took too long to respond (still running...)"
        except Exception as e:
            logger.exception("opencode_run_failed", error=str(e))
            return None, f"Error: {e}"

    async def create_session(self, name: str, cwd: str | None = None) -> Session:
        working_dir = Path(cwd or settings.default_cwd).expanduser().resolve()

        async with self._database.session() as session:
            repo = SessionRepository(session)

            existing = await repo.get_by_name(name)
            if existing:
                raise ValueError(f"Session with name '{name}' already exists")

            # Create session in DB first
            session_data = SessionCreate(name=name, cwd=str(working_dir))
            db_session = await repo.create(session_data)

            assert db_session.id is not None

            # Run opencode to create a new session and get its ID
            opencode_session_id, output = await self._run_opencode(
                working_dir, None, f"Starting session: {name}"
            )

            if not opencode_session_id:
                await repo.delete(db_session)
                raise RuntimeError(f"Failed to create OpenCode session: {output}")

            # Update DB with OpenCode session ID
            update_data: Mapping[str, object] = {
                "opencode_session_id": opencode_session_id,
                "status": SessionStatus.IDLE,
            }
            await repo.update(db_session, update_data)
            db_session.opencode_session_id = opencode_session_id
            db_session.status = SessionStatus.IDLE

            logger.info(
                "session_created",
                session_id=db_session.id,
                name=name,
                opencode_session_id=opencode_session_id,
            )
            return db_session

    async def get_session(self, session_id: int) -> Session | None:
        """Get session info from database."""
        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            if db_session.status in (SessionStatus.CLOSED, SessionStatus.DEAD):
                return None

            return db_session

    async def list_sessions(self) -> list[Session]:
        async with self._database.session() as session:
            repo = SessionRepository(session)
            return await repo.list()

    async def switch_session(self, user_id: int, session_id: int) -> Session | None:
        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            user_repo = UserStateRepository(session)
            await user_repo.set_current_session(user_id, session_id)

            self._user_states[user_id] = UserSessionState(
                user_id=user_id,
                current_session_id=session_id,
            )

            await repo.update(db_session, {"last_used": datetime.utcnow()})
            logger.info("session_switched", user_id=user_id, session_id=session_id)
            return db_session

    async def get_current_session(self, user_id: int) -> Session | None:
        if user_id in self._user_states:
            session_id = self._user_states[user_id].current_session_id
            if session_id:
                return await self.get_session(session_id)

        async with self._database.session() as session:
            user_repo = UserStateRepository(session)
            session_id = await user_repo.get_current_session(user_id)
            if session_id:
                return await self.get_session(session_id)

            return None

    async def close_session(self, session_id: int, force: bool = False) -> bool:
        async with self._lock:
            async with self._database.session() as session:
                repo = SessionRepository(session)
                db_session = await repo.get(session_id)

                if db_session:
                    await repo.update(db_session, {"status": SessionStatus.CLOSED})
                    logger.info("session_closed", session_id=session_id)
                    return True

            return False

    async def delete_session(self, session_id: int) -> bool:
        await self.close_session(session_id, force=True)

        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)
            if db_session:
                await repo.delete(db_session)
                logger.info("session_deleted", session_id=session_id)
                return True
        return False

    async def rename_session(self, session_id: int, new_name: str) -> Session | None:
        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            existing = await repo.get_by_name(new_name)
            if existing and existing.id != session_id:
                raise ValueError(f"Session with name '{new_name}' already exists")

            updated = await repo.update(db_session, {"name": new_name})

            logger.info("session_renamed", session_id=session_id, new_name=new_name)
            return updated

    async def change_directory(self, session_id: int, new_cwd: str) -> Session | None:
        working_dir = Path(new_cwd).expanduser().resolve()

        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            updated = await repo.update(db_session, {"cwd": str(working_dir)})

            logger.info("session_cwd_changed", session_id=session_id, new_cwd=str(working_dir))
            return updated

    async def send_message(self, session_id: int, message: str, callback=None) -> tuple[str | None, str | None]:
        """Send a message to an OpenCode session and return (session_id, output).
        
        Args:
            callback: Optional async callback for incremental updates
        """
        db_session = await self.get_session(session_id)
        if not db_session or not db_session.opencode_session_id:
            return None, "Session not found or not initialized"

        working_dir = Path(db_session.cwd)
        opencode_session_id, output = await self._run_opencode(
            working_dir, db_session.opencode_session_id, message,
            model=db_session.model, mode=db_session.mode,
            callback=callback
        )

        if opencode_session_id and opencode_session_id != db_session.opencode_session_id:
            # Session ID changed (forked), update it
            async with self._database.session() as session:
                repo = SessionRepository(session)
                await repo.update(db_session, {"opencode_session_id": opencode_session_id})
                db_session.opencode_session_id = opencode_session_id

        # Store last output in database
        if output:
            async with self._database.session() as session:
                repo = SessionRepository(session)
                await repo.update(db_session, {
                    "last_output": output,
                    "last_output_fetched_at": datetime.utcnow()
                })
                db_session.last_output = output
                db_session.last_output_fetched_at = datetime.utcnow()

        return opencode_session_id, output

    async def interrupt_session(self, session_id: int) -> bool:
        # With opencode run, there's no persistent process to interrupt
        # Each run is a separate process
        return True

    async def restart_session(self, session_id: int) -> Session | None:
        # Close old session and create a new one with same name/cwd
        await self.close_session(session_id, force=True)

        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            # Create new OpenCode session
            working_dir = Path(db_session.cwd)
            opencode_session_id, output = await self._run_opencode(
                working_dir, None, f"Restarted session: {db_session.name}"
            )

            if not opencode_session_id:
                await repo.update(db_session, {"status": SessionStatus.DEAD})
                return None

            update_data: Mapping[str, object] = {
                "opencode_session_id": opencode_session_id,
                "status": SessionStatus.IDLE,
            }
            await repo.update(db_session, update_data)
            db_session.opencode_session_id = opencode_session_id
            db_session.status = SessionStatus.IDLE

            logger.info("session_restarted", session_id=db_session.id)
            return db_session

    async def restore_sessions(self) -> None:
        # With OpenCode's session system, we just verify the sessions exist
        # No process restoration needed
        async with self._database.session() as session:
            repo = SessionRepository(session)
            sessions = await repo.list()

            for db_session in sessions:
                if (
                    db_session.status in (SessionStatus.RUNNING, SessionStatus.IDLE)
                    and db_session.opencode_session_id
                ):
                    logger.info(
                        "session_restored",
                        session_id=db_session.id,
                        name=db_session.name,
                        opencode_session_id=db_session.opencode_session_id,
                    )

    async def cleanup(self) -> None:
        # No persistent processes to clean up
        pass

    async def get_session_by_identifier(self, identifier: str) -> Session | None:
        # Try as internal ID first
        try:
            session_id = int(identifier)
        except ValueError:
            # Try as name or OpenCode session ID
            async with self._database.session() as session:
                repo = SessionRepository(session)
                # Check by name
                db_session = await repo.get_by_name(identifier)
                if db_session:
                    return db_session
                # Check by OpenCode session ID
                result = await session.exec(
                    select(SessionModel).where(SessionModel.opencode_session_id == identifier)
                )
                db_session = result.first()
                if db_session:
                    return db_session
                # Try to adopt the external OpenCode session
                return await self.adopt_opencode_session(identifier)
        return None

        opencode_session = await self.get_session(session_id)
        if opencode_session is not None:
            async with self._database.session() as session:
                repo = SessionRepository(session)
                return await repo.get(session_id)
            return None

    async def adopt_opencode_session(self, opencode_session_id: str) -> Session | None:
        """Create a telebot session entry for an existing OpenCode session."""
        logger.info("adopt_opencode_session_start", opencode_session_id=opencode_session_id)
        async with self._database.session() as session:
            repo = SessionRepository(session)

            # Check if already exists
            result = await session.exec(
                select(SessionModel).where(SessionModel.opencode_session_id == opencode_session_id)
            )
            existing = result.first()
            if existing:
                logger.info("session_exists", opencode_id=opencode_session_id)
                return existing

            # Get session info from OpenCode
            logger.info("fetching_session", opencode_id=opencode_session_id)
            info = await self._get_opencode_session_info(opencode_session_id)
            if not info:
                logger.warning("no_session_info", opencode_id=opencode_session_id)
                return None

            logger.info("got_session_info", opencode_id=opencode_session_id, info=info)

            name = str(info.get("title") or f"OpenCode-{opencode_session_id[:8]}")
            cwd = str(info.get("directory", str(Path.cwd())))

            session_data = SessionCreate(
                name=name,
                cwd=cwd,
                opencode_session_id=opencode_session_id,
            )
            db_session = await repo.create(session_data)

            # Update with OpenCode session ID
            await repo.update(db_session, {
                "opencode_session_id": opencode_session_id,
                "status": SessionStatus.IDLE,
            })
            db_session.opencode_session_id = opencode_session_id
            db_session.status = SessionStatus.IDLE

            logger.info("adopted_opencode_session",
                session_id=db_session.id,
                opencode_session_id=opencode_session_id,
                name=name
            )
            return db_session

    async def _get_opencode_session_info(
        self, opencode_session_id: str
    ) -> dict[str, object] | None:
        """Get info for a specific OpenCode session via export."""
        try:
            # Use export without --format flag - default format works
            cmd = [self._opencode_executable, "export", opencode_session_id]
            logger.info("get_opencode_session_info_cmd", cmd=cmd)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""
            logger.info(
                "get_opencode_session_info_output",
                opencode_session_id=opencode_session_id,
                stdout=output[:500],
                stderr=stderr_str[:500],
            )

            # Parse export output (single JSON object with "info" field)
            try:
                data = json.loads(output.strip())
                # Export format has "info" field with session metadata
                if isinstance(data, dict) and "info" in data:
                    info = data["info"]
                    if isinstance(info, dict):
                        # Normalize the info to match expected format
                        result = {
                            "id": info.get("id", opencode_session_id),
                            "title": info.get("title", ""),
                            "directory": info.get("directory", ""),
                            "projectID": info.get("projectID", ""),
                            "path": info.get("path", ""),
                            "agent": info.get("agent", ""),
                            "model": info.get("model", {}),
                            "created": info.get("created", ""),
                            "updated": info.get("updated", ""),
                        }
                        logger.info(
                "get_opencode_session_info_parsed",
                opencode_session_id=opencode_session_id,
                result=result,
            )
                        return result
            except json.JSONDecodeError as e:
                logger.warning(
                "get_opencode_session_info_json_error",
                opencode_session_id=opencode_session_id,
                error=str(e),
            )

            else:
                logger.warning(
                    "get_opencode_session_info_no_valid_data",
                    opencode_session_id=opencode_session_id,
                )
                return None
        except Exception as e:
            logger.exception("get_opencode_session_info_failed", error=str(e))
        return None

    async def get_session_model_info(self, session_id: int) -> dict[str, str] | None:
        """Get model and mode info for a session via opencode export."""
        db_session = await self.get_session(session_id)
        if not db_session or not db_session.opencode_session_id:
            return None

        try:
            cmd = [
                self._opencode_executable,
                "export",
                db_session.opencode_session_id,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path(db_session.cwd),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if not output:
                return None

            # Parse output - skip "Exporting session:" prefix line
            lines = output.strip().split("\n")
            json_lines = []
            for line in lines:
                if line.strip().startswith("{") or (json_lines and line.strip()):
                    json_lines.append(line)
            
            if not json_lines:
                return None
            
            data = json.loads("\n".join(json_lines))
            if not isinstance(data, dict) or "info" not in data:
                return None

            info = data["info"]
            if not isinstance(info, dict):
                return None

            model_info = info.get("model", {})
            if not isinstance(model_info, dict):
                model_info = {}

            mode = info.get("agent", "unknown")
            model_id = model_info.get("id", "N/A")
            provider = model_info.get("providerID", "N/A")
            variant = model_info.get("variant", "default")

            return {
                "mode": mode,
                "model": model_id,
                "provider": provider,
                "variant": variant,
            }
        except Exception:
            logger.exception("get_session_model_info_failed", session_id=session_id)
            return None

    async def get_session_info(self, session_id: int) -> dict[str, str] | None:
        """Get current model/mode info - deprecated, use get_session_model_info."""
        return await self.get_session_model_info(session_id)

    async def list_opencode_sessions(self) -> list[dict[str, str]]:
        """List all OpenCode sessions using the CLI."""
        try:
            cmd = [self._opencode_executable, "session", "list", "--format", "json"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            error = stderr.decode("utf-8", errors="replace") if stderr else ""

            if error and not output:
                logger.error("opencode_session_list_failed", error=error)
                return []

            try:
                # Output is a JSON array, not JSONL
                data = json.loads(output.strip())
                if isinstance(data, list):
                    sessions = []
                    for item in data:
                        sessions.append({
                            "id": item.get("id", ""),
                            "title": item.get("title", ""),
                            "updated": str(item.get("updated", "")),
                        })
                    return sessions
            except json.JSONDecodeError:
                pass

            # Fallback: try JSONL (newline-delimited)
            sessions = []
            for raw_line in output.strip().split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    sessions.append({
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "updated": str(item.get("updated", "")),
                    })
                except json.JSONDecodeError:
                    continue

            return sessions  # noqa: TRY300
        except TimeoutError:
            logger.exception("opencode_session_list_timeout")
            return []
        except Exception as e:
            logger.exception("opencode_session_list_error", error=str(e))
            return []

    async def change_session_cwd(self, session_id: int, new_cwd: str) -> Session | None:
        working_dir = Path(new_cwd).expanduser().resolve()

        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            updated = await repo.update(db_session, {"cwd": str(working_dir)})

            logger.info("session_cwd_changed", session_id=session_id, new_cwd=str(working_dir))
            return updated

    async def set_session_model(self, session_id: int, model: str) -> Session | None:
        """Set the model for a session."""
        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            updated = await repo.update(db_session, {"model": model})
            logger.info("session_model_changed", session_id=session_id, model=model)
            return updated

    async def set_session_mode(self, session_id: int, mode: str) -> Session | None:
        """Set the mode (agent) for a session."""
        if mode not in ("plan", "build"):
            raise ValueError("Mode must be 'plan' or 'build'")
        
        async with self._database.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get(session_id)

            if db_session is None:
                return None

            updated = await repo.update(db_session, {"mode": mode})
            logger.info("session_mode_changed", session_id=session_id, mode=mode)
            return updated

    async def get_last_output(self, session_id: int, force_refresh: bool = False) -> str | None:
        """Get the last output from a session with hybrid cache/refresh logic."""
        db_session = await self.get_session(session_id)
        if not db_session:
            return None
        
        # Check if we need to refresh
        should_refresh = force_refresh or db_session.last_output is None
        if not should_refresh and db_session.last_output_fetched_at:
            # Auto-refresh if older than 10 minutes
            age = datetime.utcnow() - db_session.last_output_fetched_at
            if age.total_seconds() > 600:  # 10 minutes
                should_refresh = True
        
        if should_refresh:
            output = await self.fetch_last_output_from_opencode(session_id)
            if output:
                # Cache it
                async with self._database.session() as session:
                    repo = SessionRepository(session)
                    await repo.update(db_session, {
                        "last_output": output,
                        "last_output_fetched_at": datetime.utcnow()
                    })
                    db_session.last_output = output
                    db_session.last_output_fetched_at = datetime.utcnow()
                return output
            elif db_session.last_output:
                # Return cached version even if stale
                return db_session.last_output
        
        return db_session.last_output

    async def fetch_last_output_from_opencode(self, session_id: int) -> str | None:
        """Fetch last assistant message from opencode export."""
        db_session = await self.get_session(session_id)
        if not db_session or not db_session.opencode_session_id:
            return None
        
        try:
            cmd = [self._opencode_executable, "export", db_session.opencode_session_id]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path(db_session.cwd),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if not output:
                return None
            
            # Parse JSON (skip "Exporting session:" prefix)
            lines = output.strip().split("\n")
            json_lines = []
            for line in lines:
                if line.strip().startswith("{") or (json_lines and line.strip()):
                    json_lines.append(line)
            
            if not json_lines:
                return None
            
            data = json.loads("\n".join(json_lines))
            if not isinstance(data, dict) or "messages" not in data:
                return None
            
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                return None
            
            # Find last assistant message
            assistant_messages = [
                msg for msg in messages 
                if isinstance(msg, dict) and msg.get("info", {}).get("role") == "assistant"
            ]
            
            if not assistant_messages:
                return None
            
            # Get last one
            last_msg = assistant_messages[-1]
            parts = last_msg.get("parts", [])
            if not isinstance(parts, list):
                return None
            
            # Extract text from parts
            text_parts = [
                part.get("text", "") 
                for part in parts 
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            
            return "\n".join(text_parts) if text_parts else None
            
        except TimeoutError:
            logger.exception("fetch_last_output_timeout", session_id=session_id)
            return None
        except Exception as e:
            logger.exception("fetch_last_output_failed", session_id=session_id, error=str(e))
            return None

    async def refresh_last_output(self, session_id: int) -> tuple[bool, str | None]:
        """Force refresh the last output from opencode. Returns (success, output)."""
        output = await self.fetch_last_output_from_opencode(session_id)
        if output:
            db_session = await self.get_session(session_id)
            if db_session:
                async with self._database.session() as session:
                    repo = SessionRepository(session)
                    await repo.update(db_session, {
                        "last_output": output,
                        "last_output_fetched_at": datetime.utcnow()
                    })
            return True, output
        return False, None

    def is_user_allowed(self, user_id: int) -> bool:
        allowed_ids = settings.allowed_user_ids
        return user_id in allowed_ids if allowed_ids else True
