import asyncio
import os
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.logging import StructuredLogger, get_logger
from app.models import SessionStatus

logger = StructuredLogger(get_logger(__name__))


@dataclass
class StreamOutput:
    text: str
    is_error: bool = False


@dataclass
class OpenCodeSession:
    session_id: int
    name: str
    cwd: Path
    process: asyncio.subprocess.Process | None = None
    pid: int | None = None
    status: SessionStatus = SessionStatus.IDLE
    stdout_buffer: list[str] = field(default_factory=list)
    stderr_buffer: list[str] = field(default_factory=list)
    _stdout_task: asyncio.Task[None] | None = None
    _stderr_task: asyncio.Task[None] | None = None
    _stdin_writer: asyncio.StreamWriter | None = None
    _stream_queue: asyncio.Queue[StreamOutput] = field(default_factory=asyncio.Queue)
    _closed: bool = False

    @property
    def id(self) -> int:
        return self.session_id

    async def start(self, opencode_executable: str = "opencode") -> bool:
        if self.process is not None:
            return False

        self.cwd.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update({
            "TERM": "dumb",
            "COLUMNS": "120",
            "LINES": "40",
        })

        try:
            self.process = await asyncio.create_subprocess_exec(
                opencode_executable,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
            self.pid = self.process.pid
            self.status = SessionStatus.RUNNING
            self._stdin_writer = self.process.stdin

            assert self.process.stdout is not None
            assert self.process.stderr is not None

            self._stdout_task = asyncio.create_task(
                self._read_stream(self.process.stdout, False)
            )
            self._stderr_task = asyncio.create_task(
                self._read_stream(self.process.stderr, True)
            )

        except Exception:
            logger.exception("opencode_session_start_failed", session_id=self.session_id)
            self.status = SessionStatus.DEAD
            return False
        else:
            logger.info(
                "opencode_session_started",
                session_id=self.session_id,
                pid=self.pid,
                name=self.name,
            )
            return True

    async def _read_stream(self, stream: asyncio.StreamReader, is_error: bool) -> None:
        buffer = ""
        try:
            while not stream.at_eof():
                chunk = await stream.read(settings.stream_buffer_size)
                if not chunk:
                    break

                text = chunk.decode("utf-8", errors="replace")
                buffer += text

                lines = buffer.split("\n")
                buffer = lines.pop()

                for line in lines:
                    await self._stream_queue.put(StreamOutput(text=line + "\n", is_error=is_error))

            if buffer:
                await self._stream_queue.put(StreamOutput(text=buffer, is_error=is_error))

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("stream_read_error", session_id=self.session_id, is_error=is_error)

    async def write_stdin(self, data: str) -> bool:
        if self._stdin_writer is None or self._stdin_writer.is_closing():
            return False

        try:
            self._stdin_writer.write(data.encode("utf-8"))
            await self._stdin_writer.drain()
        except Exception:
            logger.exception("stdin_write_failed", session_id=self.session_id)
            return False
        else:
            return True

    async def read_output(self, timeout: float | None = None) -> list[StreamOutput]:
        if timeout is None:
            timeout = settings.stream_update_interval
        outputs = []
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break

            try:
                output = await asyncio.wait_for(self._stream_queue.get(), timeout=remaining)
                outputs.append(output)
            except TimeoutError:
                break

        return outputs

    async def wait_for_output(
        self,
        timeout: float = 30.0,
        prompt_patterns: tuple[str, ...] = (">", "$", "#", "%", "❯", "➜", "::"),
        min_wait: float = 0.5,
    ) -> list[StreamOutput]:
        """
        Wait for output until a prompt is detected or timeout.

        Args:
            timeout: Maximum time to wait in seconds
            prompt_patterns: Tuple of strings that indicate a prompt
                (checked at end of stripped lines)
            min_wait: Minimum time to wait before checking for prompt
                (allows output to accumulate)
        """
        outputs = []
        deadline = asyncio.get_event_loop().time() + timeout
        start_time = asyncio.get_event_loop().time()

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break

            try:
                output = await asyncio.wait_for(self._stream_queue.get(), timeout=remaining)
                outputs.append(output)

                # Only check for prompt after minimum wait time (allows output to accumulate)
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= min_wait:
                    text = output.text.strip()
                    if any(text.endswith(p) for p in prompt_patterns):
                        break

            except TimeoutError:
                break

        return outputs

    async def send_interrupt(self) -> bool:
        if self.process and self.process.returncode is None:
            try:
                self.process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                return False
            else:
                return True
        return False

    async def capture_tui_frame(self, timeout: float = 5.0) -> dict[str, str] | None:
        """
        Capture and parse a TUI frame to extract model/mode info.

        Sends Ctrl+L to refresh the TUI, captures the rendered frame,
        and parses ANSI escape sequences to extract model/mode info.

        Returns:
            Dict with keys: model, provider, variant, mode (plan/build)
            or None if capture/parse fails.
        """
        if not self.process or self.process.returncode is not None:
            return None

        if not self._stdin_writer or self._stdin_writer.is_closing():
            return None

        try:
            # Send Ctrl+L to refresh the TUI
            self._stdin_writer.write(b"\x0c")  # Ctrl+L
            await self._stdin_writer.drain()
        except Exception:
            logger.exception("tui_refresh_failed", session_id=self.session_id)
            return None

        # Wait for TUI to redraw and capture output
        outputs = await self.wait_for_output(
            timeout=timeout,
            prompt_patterns=(">", "$", "#", "%", "❯", "➜", "::", "│", "└─", "└──"),
            min_wait=0.3,
        )

        if not outputs:
            return None

        # Combine all output text
        full_text = "".join(o.text for o in outputs)

        # Parse ANSI output for model/mode info
        return self._parse_tui_frame(full_text)

    def _parse_tui_frame(self, text: str) -> dict[str, str] | None:
        """
        Parse TUI frame text for model/mode information.

        Looks for patterns like:
        - Model name (e.g., "Claude 3.5 Sonnet", "GPT-4o")
        - Provider (e.g., "Anthropic", "OpenAI")
        - Variant (High, Medium, Low)
        - Mode (Plan, Build)
        """
        # Strip ANSI escape sequences
        ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
        clean_text = ansi_escape.sub("", text)

        result = {}

        # Try to find mode (Plan/Build) - often shown in status bar or header
        mode_match = re.search(r"\b(Plan|Build)\b", clean_text, re.IGNORECASE)
        if mode_match:
            result["mode"] = mode_match.group(1).capitalize()

        # Try to find model info - patterns like "Claude 3.5 Sonnet", "GPT-4o", etc.
        # Model names often appear with provider
        model_patterns = [
            r"(Claude\s+[\d.]\s*\w+)",
            r"(GPT-\d\w*)",
            r"(Gemini\s+\d\w*)",
        ]
        for pattern in model_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                result["model"] = match.group(1)
                break

        # Try to find provider
        provider_match = re.search(
            r"\b(Anthropic|OpenAI|Google|Azure|AWS)\b",
            clean_text,
            re.IGNORECASE,
        )
        if provider_match:
            result["provider"] = provider_match.group(1)

        # Try to find variant (High, Medium, Low)
        variant_match = re.search(r"\b(High|Medium|Low)\b", clean_text, re.IGNORECASE)
        if variant_match:
            result["variant"] = variant_match.group(1).capitalize()

        return result if result else None

    async def close(self, force: bool = False) -> None:
        if self._closed:
            return

        self._closed = True

        if self._stdout_task:
            self._stdout_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()

        if self._stdin_writer and not self._stdin_writer.is_closing():
            self._stdin_writer.close()
            await self._stdin_writer.wait_closed()

        if self.process and self.process.returncode is None:
            if force:
                self.process.kill()
            else:
                self.process.terminate()

            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

        self.status = SessionStatus.CLOSED
        logger.info("opencode_session_closed", session_id=self.session_id, name=self.name)

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def __del__(self) -> None:
        if not self._closed and self.process and self.process.returncode is None:
            self.process.kill()
