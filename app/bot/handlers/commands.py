import asyncio
import json
from pathlib import Path

from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers.messages import parse_opencode_output, send_formatted_message
from app.logging import StructuredLogger, get_logger
from app.models import SessionStatus
from app.services.session_manager import SessionManager

logger = StructuredLogger(get_logger(__name__))


def format_model_info(info: dict[str, str]) -> str:
    """Format model/mode info into a readable message."""
    mode = info.get("mode", "unknown")
    mode_emoji = "🏗️" if mode == "build" else "📋" if mode == "plan" else "❓"
    mode_text = mode.capitalize()
    
    model = info.get("model", "N/A")
    provider = info.get("provider", "N/A")
    variant = info.get("variant", "default").capitalize()
    
    return (
        f"🤖 <b>Model Info</b>\n\n"
        f"Mode: {mode_emoji} {mode_text}\n"
        f"Model: {model}\n"
        f"Provider: {provider}\n"
        f"Variant: {variant}"
    )


async def check_user_allowed(
    update: Update, session_manager: SessionManager
) -> tuple[int, Message] | None:
    if not update.effective_user or not update.message:
        return None
    if not session_manager.is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return None
    return update.effective_user.id, update.message


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args:
        await message.reply_text(
            "Usage: /new <name> [directory]\nExample: /new myproject ~/projects/myproject"
        )
        return

    name = args[0]
    cwd = args[1] if len(args) > 1 else "~"

    try:
        session = await session_manager.create_session(name, cwd)
        msg = (
            f"✅ Session Created\n\n"
            f"ID: {session.id}\n"
            f"Name: {session.name}\n"
            f"OpenCode Session: {session.opencode_session_id or 'N/A'}\n"
            f"Working Directory: {session.cwd}"
        )
        await message.reply_text(msg)
        
        model_info = await session_manager.get_session_model_info(session.id)
        if model_info:
            await message.reply_text(format_model_info(model_info), parse_mode="HTML")
    except Exception as e:
        await message.reply_text(f"❌ Failed to create session: {e}")


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    sessions = await session_manager.list_sessions()
    current = await session_manager.get_current_session(user_id)
    current_id = current.id if current else None

    # Also fetch OpenCode sessions
    opencode_sessions = await session_manager.list_opencode_sessions()

    if not sessions and not opencode_sessions:
        msg = "No sessions found. Create one with /new <name> [directory]"
        await message.reply_text(msg)
        return

    lines = ["📋 <b>Telebot Sessions</b>", "ID  NAME          STATUS    OPENCODE_SESSION", "─" * 50]
    for session in sessions:
        status_emoji = {
            SessionStatus.RUNNING: "🟢",
            SessionStatus.IDLE: "🟡",
            SessionStatus.CLOSED: "🔴",
            SessionStatus.DEAD: "💀",
        }.get(session.status, "❓")

        marker = "▶ " if session.id == current_id else "  "
        oc_sid = session.opencode_session_id or "N/A"
        line = f"{marker}{session.id:<3} {session.name:<12} {status_emoji} "
        line += f"{session.status.value:<7} {oc_sid}"
        lines.append(line)

    if opencode_sessions:
        lines.append("")
        lines.append("📋 <b>OpenCode Sessions (from opencode session list)</b>")
        for oc in opencode_sessions:
            title = oc["title"][:30] if oc["title"] else "Untitled"
            lines.append(f"  {oc['id']}  {title}")

    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args:
        await message.reply_text("Usage: /switch <session_id_or_name>")
        return

    identifier = args[0]
    session = await session_manager.get_session_by_identifier(identifier)
    if not session:
        await message.reply_text(f"❌ Session not found: {identifier}")
        return

    assert session.id is not None
    await session_manager.switch_session(user_id, session.id)
    await message.reply_text(
        f"✅ Switched to session: {session.name} (ID: {session.id})"
    )
    
    model_info = await session_manager.get_session_model_info(session.id)
    if model_info:
        await message.reply_text(format_model_info(model_info), parse_mode="HTML")


async def cmd_current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    current = await session_manager.get_current_session(user_id)

    if current:
        msg = (
            f"📍 Current Session\n\n"
            f"ID: {current.id}\n"
            f"Name: {current.name}\n"
            f"Status: {current.status.value}\n"
            f"OpenCode Session: {current.opencode_session_id or 'N/A'}\n"
            f"CWD: {current.cwd}"
        )
    else:
        msg = "No current session selected. Use /switch <id> or /new <name>"

    await message.reply_text(msg)


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    _, message = check_result

    args = context.args
    if not args:
        await message.reply_text("Usage: /close <session_id_or_name>")
        return

    identifier = args[0]
    session = await session_manager.get_session_by_identifier(identifier)
    if not session:
        await message.reply_text(f"❌ Session not found: {identifier}")
        return

    assert session.id is not None
    await session_manager.close_session(session.id)
    await message.reply_text(
        f"✅ Session closed: {session.name} (ID: {session.id})"
    )


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    _, message = check_result

    args = context.args
    if not args:
        await message.reply_text("Usage: /restart <session_id_or_name>")
        return

    identifier = args[0]
    session = await session_manager.get_session_by_identifier(identifier)
    if not session:
        await message.reply_text(f"❌ Session not found: {identifier}")
        return

    assert session.id is not None
    result = await session_manager.restart_session(session.id)
    if result:
        await message.reply_text(
            f"✅ Session restarted: {result.name} (ID: {result.id})"
        )
    else:
        await message.reply_text("❌ Failed to restart session")


async def cmd_interrupt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args:
        current = await session_manager.get_current_session(user_id)
        if not current:
            await message.reply_text(
                "No current session. Use /interrupt <session_id_or_name>"
            )
            return
        assert current.id is not None
        session_id = current.id
    else:
        identifier = args[0]
        session = await session_manager.get_session_by_identifier(identifier)
        if not session:
            await message.reply_text(f"❌ Session not found: {identifier}")
            return
        assert session.id is not None
        session_id = session.id

    success = await session_manager.interrupt_session(session_id)

    if success:
        await message.reply_text("✅ Interrupt sent (Ctrl+C)")
    else:
        await message.reply_text("❌ Failed to send interrupt")


async def cmd_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    current = await session_manager.get_current_session(user_id)

    if not current:
        await message.reply_text("No current session. Use /switch or /new")
        return

    await message.reply_text(f"📁 {current.cwd}")


async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args:
        await message.reply_text("Usage: /cd <directory>")
        return

    new_cwd = " ".join(args)
    current = await session_manager.get_current_session(user_id)

    if not current:
        await message.reply_text("No current session. Use /switch or /new")
        return

    assert current.id is not None
    result = await session_manager.change_session_cwd(current.id, new_cwd)

    if result:
        await message.reply_text(f"✅ Changed directory to: {result.cwd}")
    else:
        await message.reply_text("❌ Failed to change directory")


MIN_RENAME_ARGS = 2


async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    _, message = check_result

    args = context.args
    if not args or len(args) < MIN_RENAME_ARGS:
        await message.reply_text("Usage: /rename <session_id_or_name> <new_name>")
        return

    identifier, new_name = args[0], args[1]
    session = await session_manager.get_session_by_identifier(identifier)
    if not session:
        await message.reply_text(f"❌ Session not found: {identifier}")
        return

    assert session.id is not None
    result = await session_manager.rename_session(session.id, new_name)

    if result:
        await message.reply_text(f"✅ Session renamed to: {result.name}")
    else:
        await message.reply_text("❌ Failed to rename session")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    help_text = (
        "🤖 <b>OpenCode Telegram Bot Commands</b>\n\n"
        "<b>Session Management:</b>\n"
        "/new <name> [dir]  - Create new session\n"
        "/sessions             - List all sessions\n"
        "/switch <id|name>  - Switch active session\n"
        "/current              - Show current session\n"
        "/close <id|name>   - Close a session\n"
        "/restart <id|name> - Restart a session\n"
        "/interrupt [id|name]  - Send Ctrl+C to session\n"
        "/session_info [id|name] - Show session model/mode info\n"
        "/model                - List available models\n"
        "/set_model [session] <model> - Set session model\n"
        "/mode <session> <plan|build> - Set session mode\n"
        "/last                 - Show last output from current session\n"
        "/refresh [id|name]    - Force refresh last output from opencode\n\n"
        "<b>Directory:</b>\n"
        "/pwd                  - Show working directory\n"
        "/cd <dir>           - Change working directory\n"
        "/rename <id|name> <new> - Rename session\n\n"
        "<b>File Operations:</b>\n"
        "/upload               - Reply to a file to upload\n"
        "/download <file>    - Download a file\n"
        "/listfiles            - List files in session directory\n\n"
        "<b>Other:</b>\n"
        "/help                 - Show this help\n"
        "/status               - Show bot status\n"
        "/logs <id|name>     - Show session logs\n\n"
        "<i>Send any non-command message to send input to the "
        "active OpenCode session.</i>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await message.reply_text(
            "Usage: Reply to a file with /upload to upload it "
            "to the session directory."
        )
        return

    current = await session_manager.get_current_session(user_id)
    if not current:
        await message.reply_text("No active session. Use /new or /switch first.")
        return

    document = update.message.reply_to_message.document
    file = await context.bot.get_file(document.file_id)

    cwd = current.cwd
    file_name = document.file_name or "unknown_file"
    file_path = Path(cwd) / file_name if cwd else Path(file_name)

    await file.download_to_drive(file_path)
    await message.reply_text(f"✅ File saved: {document.file_name} to {current.cwd}")


async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args:
        await message.reply_text("Usage: /download <filename>")
        return

    file_name = " ".join(args)
    current = await session_manager.get_current_session(user_id)
    if not current:
        await message.reply_text("No active session. Use /new or /switch first.")
        return

    cwd = current.cwd
    file_path = Path(cwd) / file_name if cwd else Path(file_name)

    if not file_path.exists():
        await message.reply_text(f"❌ File not found: {file_name}")
        return

    try:
        with open(file_path, "rb") as f:
            await message.reply_document(document=f, filename=file_name)
    except Exception as e:
        await message.reply_text(f"❌ Failed to send file: {e}")


async def cmd_listfiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    current = await session_manager.get_current_session(user_id)
    if not current:
        await message.reply_text("No active session. Use /new or /switch first.")
        return

    cwd = current.cwd
    if not cwd or not Path(cwd).exists():
        await message.reply_text(f"Directory not found: {cwd}")
        return

    try:
        files = list(Path(cwd).iterdir())
        if not files:
            await message.reply_text(f"📁 {cwd}\n\n(Empty directory)")
            return

        lines = [f"📁 {cwd}\n"]
        for f in sorted(files):
            if f.is_dir():
                lines.append(f"📂 {f.name}/")
            else:
                size = f.stat().st_size
                lines.append(f"📄 {f.name} ({size} bytes)")

        await message.reply_text("\n".join(lines))
    except Exception as e:
        await message.reply_text(f"❌ Failed to list files: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot and session status."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    sessions = await session_manager.list_sessions()
    current = await session_manager.get_current_session(user_id)

    running = sum(1 for s in sessions if s.status == SessionStatus.RUNNING)
    idle = sum(1 for s in sessions if s.status == SessionStatus.IDLE)
    closed = sum(1 for s in sessions if s.status == SessionStatus.CLOSED)
    dead = sum(1 for s in sessions if s.status == SessionStatus.DEAD)

    lines = [
        "🤖 <b>Bot Status</b>",
        f"Total sessions: {len(sessions)}",
        f"  🟢 Running: {running}",
        f"  🟡 Idle: {idle}",
        f"  🔴 Closed: {closed}",
        f"  💀 Dead: {dead}",
        "",
    ]

    if current:
        lines.append(f"📍 Current: {current.name} (ID: {current.id})")
        lines.append(f"   Status: {current.status.value}")
        lines.append(f"   OpenCode Session: {current.opencode_session_id or 'N/A'}")
        lines.append(f"   CWD: {current.cwd}")
    else:
        lines.append("📍 No active session")

    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def _get_session_id_for_logs(
    user_id: int,
    session_manager: SessionManager,
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> int | None:
    """Get session ID from args or current session."""
    args = context.args
    if not args:
        current = await session_manager.get_current_session(user_id)
        if current is None:
            await message.reply_text("No session specified. Use /logs <session_id_or_name>")
            return None
        return current.id

    identifier = args[0]
    session = await session_manager.get_session_by_identifier(identifier)
    if not session:
        await message.reply_text(f"❌ Session not found: {identifier}")
        return None
    return session.id


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent logs for a session."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    session_id = await _get_session_id_for_logs(user_id, session_manager, message, context)
    if session_id is None:
        return

    db_session = await session_manager.get_session(session_id)
    if not db_session or not db_session.opencode_session_id:
        await message.reply_text("❌ Session not found or not initialized")
        return

    # Use opencode export to get session logs
    cmd = [
        session_manager._opencode_executable,
        "export",
        db_session.opencode_session_id,
        "--format",
        "json",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=Path(db_session.cwd),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        error = stderr.decode("utf-8", errors="replace") if stderr else ""

        if error and not output:
            await message.reply_text(f"❌ Failed to export session: {error}")
            return

        if not output:
            await message.reply_text("No logs available.")
            return

        # Send output in chunks
        max_len = 4000
        if len(output) <= max_len:
            await message.reply_text(output)
        else:
            for i in range(0, len(output), max_len):
                await message.reply_text(output[i : i + max_len])

    except TimeoutError:
        await message.reply_text("❌ Timeout exporting session")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


async def cmd_session_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed session info including model/mode."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if args:
        identifier = args[0]
        session = await session_manager.get_session_by_identifier(identifier)
        if not session:
            await message.reply_text(f"❌ Session not found: {identifier}")
            return
    else:
        session = await session_manager.get_current_session(user_id)
        if not session:
            await message.reply_text("No current session. Use /switch or /new")
            return

    assert session.id is not None
    
    model_info = await session_manager.get_session_model_info(session.id)
    
    lines = [
        "📊 <b>Session Info</b>\n\n"
        f"ID: {session.id}\n"
        f"Name: {session.name}\n"
        f"Status: {session.status.value}\n"
        f"CWD: {session.cwd}\n"
        f"OpenCode Session: {session.opencode_session_id or 'N/A'}\n"
        f"Model: {session.model or 'Default'}\n"
        f"Mode: {session.mode or 'Default'}"
    ]
    
    if model_info:
        lines.append("\n" + format_model_info(model_info))
    
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all available models."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    _, message = check_result

    try:
        cmd = [session_manager._opencode_executable, "models"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)

        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        error = stderr.decode("utf-8", errors="replace") if stderr else ""

        if error and not output:
            await message.reply_text(f"❌ Failed to list models: {error}")
            return

        if not output:
            await message.reply_text("No models available.")
            return

        # Parse plain text output - one model per line
        lines = output.strip().split("\n")
        if lines:
            msg_lines = ["🤖 Available Models\n"]
            for model in lines:
                model = model.strip()
                if model:
                    # Format: provider/model or just model
                    if "/" in model:
                        parts = model.split("/", 1)
                        provider = parts[0]
                        model_name = parts[1] if len(parts) > 1 else model
                        # Escape HTML special characters
                        model_name = model_name.replace("&", "&").replace("<", "<").replace(">", ">")
                        provider = provider.replace("&", "&").replace("<", "<").replace(">", ">")
                        msg_lines.append(f"• {model_name} ({provider})")
                    else:
                        model = model.replace("&", "&").replace("<", "<").replace(">", ">")
                        msg_lines.append(f"• {model}")
            
            # Send in chunks if too long
            full_msg = "\n".join(msg_lines)
            max_len = 4000
            if len(full_msg) <= max_len:
                await message.reply_text(full_msg)
            else:
                for i in range(0, len(full_msg), max_len):
                    await message.reply_text(full_msg[i:i + max_len])
        else:
            await message.reply_text(output[:4000])

    except TimeoutError:
        await message.reply_text("❌ Timeout listing models")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


async def cmd_set_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the model for a session."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args or len(args) < 2:
        current = await session_manager.get_current_session(user_id)
        if not current:
            await message.reply_text("Usage: /set_model <session_id_or_name> <model>\nExample: /set_model 1 nvidia/qwen3.5-397b-a17b")
            return
        session_id = current.id
        model = args[0]
    else:
        identifier, model = args[0], args[1]
        session = await session_manager.get_session_by_identifier(identifier)
        if not session:
            await message.reply_text(f"❌ Session not found: {identifier}")
            return
        assert session.id is not None
        session_id = session.id

    assert session_id is not None
    
    # Validate model exists in OpenCode
    available_models = await session_manager.get_available_models()
    if available_models and model not in available_models:
        await message.reply_text(
            f"❌ Model '{model}' not found in available models.\n"
            f"Use /model to see available models."
        )
        return

    result = await session_manager.set_session_model(session_id, model)
    
    if result:
        await message.reply_text(f"✅ Model set to: {model}")
    else:
        await message.reply_text("❌ Failed to set model")


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last output from current session."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    current = await session_manager.get_current_session(user_id)
    if not current:
        await message.reply_text("No current session. Use /switch or /new")
        return

    assert current.id is not None
    
    # Send processing message
    processing_msg = await message.reply_text("⏳ Fetching last output...")
    
    # Get last output (with auto-refresh if needed)
    last_output = await session_manager.get_last_output(current.id)
    
    # Delete processing message
    try:
        await processing_msg.delete()
    except Exception:
        pass
    
    if not last_output:
        await message.reply_text("No output yet. Send a message to the session first.")
        return

    # Parse and format the output
    formatted_messages = parse_opencode_output(last_output)
    if not formatted_messages:
        await message.reply_text("No formatted output available.")
        return

    # Send output to Telegram
    for msg in formatted_messages:
        await send_formatted_message(update, msg, context.bot)


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force refresh the last output from current session."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if args:
        # Refresh specific session
        identifier = args[0]
        session = await session_manager.get_session_by_identifier(identifier)
        if not session:
            await message.reply_text(f"❌ Session not found: {identifier}")
            return
        assert session.id is not None
        session_id = session.id
    else:
        # Refresh current session
        current = await session_manager.get_current_session(user_id)
        if not current:
            await message.reply_text("No current session. Use /switch or /new")
            return
        assert current.id is not None
        session_id = current.id

    # Send processing message
    processing_msg = await message.reply_text("⏳ Refreshing output from opencode...")
    
    # Force refresh
    success, output = await session_manager.refresh_last_output(session_id)
    
    # Delete processing message
    try:
        await processing_msg.delete()
    except Exception:
        pass
    
    if not success:
        await message.reply_text("❌ Failed to fetch output from opencode")
        return

    # Parse and format the output
    formatted_messages = parse_opencode_output(output)
    if not formatted_messages:
        await message.reply_text("✅ Output refreshed (no formatted content)")
        return

    await message.reply_text("✅ Output refreshed")
    
    # Send output to Telegram
    for msg in formatted_messages:
        await send_formatted_message(update, msg, context.bot)


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the mode (agent) for a session."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]
    check_result = await check_user_allowed(update, session_manager)
    if check_result is None:
        return
    user_id, message = check_result

    args = context.args
    if not args or len(args) < 2:
        await message.reply_text("Usage: /mode <session_id_or_name> <plan|build>")
        return

    identifier, mode = args[0], args[1].lower()
    if mode not in ("plan", "build"):
        await message.reply_text("❌ Mode must be 'plan' or 'build'")
        return

    session = await session_manager.get_session_by_identifier(identifier)
    if not session:
        await message.reply_text(f"❌ Session not found: {identifier}")
        return

    assert session.id is not None
    try:
        result = await session_manager.set_session_mode(session.id, mode)
        
        if result:
            mode_emoji = "📋" if mode == "plan" else "🏗️"
            await message.reply_text(f"✅ Mode set to: {mode_emoji} {mode.capitalize()}")
        else:
            await message.reply_text("❌ Failed to set mode")
    except ValueError as e:
        await message.reply_text(f"❌ {e}")


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Health check endpoint."""
    if not update.message:
        return
    await update.message.reply_text("✅ Bot is healthy")


async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shutdown the bot (admin only)."""
    if not update.effective_user or not update.message:
        return
    session_manager: SessionManager = context.bot_data["session_manager"]

    # Check if user is allowed (admin check)
    if not session_manager.is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ Not authorized")
        return

    await update.message.reply_text("🛑 Shutting down...")
    # Stop the application
    await context.application.stop()
