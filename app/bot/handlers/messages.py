import json
from pathlib import Path

from telegram import Bot, Update
from telegram.ext import ContextTypes

from app.logging import StructuredLogger, get_logger
from app.services.session_manager import SessionManager

logger = StructuredLogger(get_logger(__name__))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: PLR0911
    if not update.effective_user or not update.message:
        return

    session_manager: SessionManager = context.bot_data["session_manager"]

    if not session_manager.is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    if update.message.text and update.message.text.startswith("/"):
        return

    current_session = await session_manager.get_current_session(update.effective_user.id)
    if not current_session:
        await update.message.reply_text(
            "No active session. Use /new to create one or /switch to select one."
        )
        return

    assert current_session.id is not None

    text = update.message.text or ""
    if not text.strip():
        return

    # Send immediate processing response
    processing_msg = await update.message.reply_text("⏳ Processing...")

    # Track significant events for incremental updates
    tool_events = []
    error_events = []
    
    async def stream_callback(data: dict) -> None:
        """Callback to receive incremental updates from opencode."""
        nonlocal tool_events, error_events
        
        msg_type = data.get("type")
        
        if msg_type == "tool" or msg_type == "tool_use":
            # Track tool completions
            part = data.get("part", {})
            if isinstance(part, dict):
                tool_data = part.get("tool", {})
                state_info = part.get("state", {})
            else:
                tool_data = data.get("tool", {})
                state_info = data.get("state", {})
            
            if isinstance(tool_data, dict):
                tool_name = tool_data.get("name", "unknown")
                state = state_info.get("status", "running") if isinstance(state_info, dict) else "running"
                
                # Only send updates for completed tools
                if state != "running":
                    tool_emoji = {
                        "read": "📖",
                        "write": "✍️",
                        "edit": "✏️",
                        "bash": "🖥",
                        "glob": "🔍",
                        "grep": "🔎",
                        "task": "🤖",
                        "list": "📁",
                    }.get(tool_name, "🔧")
                    
                    tool_events.append(f"{tool_emoji} {tool_name}")
                    
                    # Send incremental update (limit to avoid spam)
                    if len(tool_events) <= 5:
                        status = "✅" if state == "success" else "⚠️"
                        tools_str = " → ".join(tool_events[-3:])  # Show last 3
                        await processing_msg.edit_text(f"⏳ Working... {status} {tools_str}")
        
        elif msg_type == "error":
            error_msg = data.get("error", "Unknown error")
            error_events.append(error_msg)
            if len(error_events) <= 3:  # Limit error notifications
                await processing_msg.edit_text(f"⏳ Processing... ⚠️ Error occurred")

    try:
        # Send message to OpenCode via session manager with streaming callback
        opencode_session_id, output = await session_manager.send_message(
            current_session.id, text, callback=stream_callback
        )

        # Delete processing message
        try:
            await processing_msg.delete()
        except Exception:
            pass

        if output is None:
            await update.message.reply_text("❌ Failed to send message to session")
            return

        # Parse JSON output and extract structured messages
        formatted_messages = parse_opencode_output(output)
        if not formatted_messages:
            # If no formatted messages but we have tool events, show summary
            if tool_events:
                summary = "✅ Task completed\n\nTools used:\n" + "\n".join(f"• {evt}" for evt in tool_events)
                await update.message.reply_text(summary)
            return

        # Send output to Telegram
        for msg in formatted_messages:
            await send_formatted_message(update, msg, context.bot)
            
    except Exception as e:
        # Handle any errors during processing
        try:
            await processing_msg.delete()
        except Exception:
            pass
        logger.exception("handle_message_error", error=str(e))
        await update.message.reply_text(f"❌ Error during processing: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    session_manager: SessionManager = context.bot_data["session_manager"]

    if not session_manager.is_user_allowed(update.effective_user.id):
        return

    current = await session_manager.get_current_session(update.effective_user.id)
    if not current:
        await update.message.reply_text("No active session")
        return

    document = update.message.document
    if not document:
        return

    file = await context.bot.get_file(document.file_id)

    cwd = current.cwd
    file_name = document.file_name or "unknown_file"
    file_path = Path(cwd) / file_name if cwd else Path(file_name)

    await file.download_to_drive(file_path)
    await update.message.reply_text(f"✅ File saved: {document.file_name}")


def parse_opencode_output(output: str) -> list[dict[str, str]]:  # noqa: PLR0912,PLR0915
    """Parse OpenCode JSON output and extract structured messages for Telegram."""
    if not output:
        return []

    lines = output.strip().split("\n")
    messages = []
    current_text = ""
    current_tool = None

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue
        try:
            data = json.loads(stripped_line)
            msg_type = data.get("type")

            if msg_type == "text":
                # Text content is nested in part.text
                part = data.get("part", {})
                if isinstance(part, dict):
                    text_content = part.get("text", "")
                else:
                    text_content = data.get("text", "")
                if text_content:
                    # Flush any pending tool message
                    if current_tool:
                        messages.append(current_tool)
                        current_tool = None
                    current_text += text_content + "\n"

            elif msg_type in ("tool", "tool_use"):
                # Flush accumulated text
                if current_text.strip():
                    messages.append({"type": "text", "content": current_text.strip()})
                    current_text = ""

                # Tool data is in part for tool_use, or at top level for tool
                part = data.get("part", {})
                if isinstance(part, dict):
                    tool_data = part.get("tool", {})
                    state_info = part.get("state", {})
                else:
                    tool_data = data.get("tool", {})
                    state_info = data.get("state", {})

                if isinstance(tool_data, dict):
                    tool_name = tool_data.get("name", "unknown")
                    tool_input = tool_data.get("input", {})
                else:
                    tool_name = "unknown"
                    tool_input = {}

                if isinstance(state_info, dict):
                    tool_output = state_info.get("output", "")
                    tool_state = state_info.get("status", "running")
                else:
                    tool_output = ""
                    tool_state = "running"

                current_tool = {
                    "type": "tool",
                    "name": tool_name,
                    "input": tool_input,
                    "output": tool_output,
                    "state": tool_state,
                }

            elif msg_type == "step_start":
                # Could show thinking indicator
                pass

            elif msg_type == "step_finish":
                # Flush any pending content
                if current_text.strip():
                    messages.append({"type": "text", "content": current_text.strip()})
                    current_text = ""
                if current_tool:
                    messages.append(current_tool)
                    current_tool = None

            elif msg_type == "error":
                if current_text.strip():
                    messages.append({"type": "text", "content": current_text.strip()})
                    current_text = ""
                messages.append({
                    "type": "error",
                    "content": data.get("error", "Unknown error"),
                })

        except json.JSONDecodeError:
            # If not JSON, include as text
            current_text += stripped_line + "\n"

    # Flush remaining
    if current_text.strip():
        messages.append({"type": "text", "content": current_text.strip()})
    if current_tool:
        messages.append(current_tool)

    return messages


async def send_formatted_message(update: Update, msg: dict[str, str], bot: Bot) -> None:
    """Send a formatted message to Telegram based on message type."""
    if not update.message:
        return

    msg_type = msg.get("type")

    if msg_type == "text":
        content = msg.get("content", "")
        if content:
            await send_output_chunks(update, content, bot)

    elif msg_type == "tool":
        name = msg.get("name", "unknown")
        input_data: dict[str, object] = msg.get("input", {})  # type: ignore[assignment]
        output_data = msg.get("output", "")
        state = msg.get("state", "running")

        # Format tool message
        tool_emoji = {
            "read": "📖",
            "write": "✍️",
            "edit": "✏️",
            "bash": "🖥",
            "glob": "🔍",
            "grep": "🔎",
            "task": "🤖",
            "list": "📁",
        }.get(name, "🔧")

        status_emoji = "⏳" if state == "running" else "✅"

        # Format input for display
        input_str = format_tool_input(input_data)

        text = f"{status_emoji} <b>{tool_emoji} {name}</b>"
        if input_str:
            text += f"\n<code>{escape_html(input_str)}</code>"

        if output_data and state != "running":
            output_str = str(output_data)[:1000]
            text += f"\n\n📤 <b>Output:</b>\n<pre>{escape_html(output_str)}</pre>"

        try:
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception:
            # Fallback without HTML
            plain = f"{status_emoji} {name}"
            if input_str:
                plain += f"\n{input_str}"
            if output_data and state != "running":
                plain += f"\nOutput: {str(output_data)[:1000]}"
            await update.message.reply_text(plain)

    elif msg_type == "error":
        content = msg.get("content", "Unknown error")
        try:
            await update.message.reply_text(
                f"❌ <b>Error:</b>\n<pre>{escape_html(content)}</pre>",
                parse_mode="HTML"
            )
        except Exception:
            await update.message.reply_text(f"❌ Error: {content}")


MAX_FIELD_LENGTH = 200
MAX_INPUT_PREVIEW = 300


def format_tool_input(input_data: dict[str, object]) -> str:
    """Format tool input for display."""
    if not input_data:
        return ""

    # Show relevant fields based on tool type
    relevant_fields = ["path", "pattern", "command", "query", "prompt", "old_string", "new_string"]
    parts = []

    for field in relevant_fields:
        if field in input_data:
            val = str(input_data[field])
            if len(val) > MAX_FIELD_LENGTH:
                val = val[:MAX_FIELD_LENGTH] + "..."
            parts.append(f"{field}: {val}")

    # Include other fields if no relevant ones found
    if not parts:
        for key, raw_val in input_data.items():
            if key not in ["id", "session_id"]:
                val = str(raw_val)
                if len(val) > MAX_FIELD_LENGTH:
                    val = val[:MAX_FIELD_LENGTH] + "..."
                parts.append(f"{key}: {val}")

    return "; ".join(parts) if parts else str(input_data)[:MAX_INPUT_PREVIEW]


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&").replace("<", "<").replace(">", ">")


async def send_output_chunks(
    update: Update, text: str, bot: Bot, max_chunk_size: int = 4000
) -> None:
    if not update.message:
        return

    if not text.strip():
        return

    # Try to send with markdown formatting for code blocks
    chunks = split_text_smart(text, max_chunk_size)

    for _i, chunk in enumerate(chunks):
        try:
            # Use HTML for better formatting
            await update.message.reply_text(chunk, parse_mode="HTML")
        except Exception:
            # Fallback to plain text
            try:
                await update.message.reply_text(chunk)
            except Exception as e:
                logger.exception("send_output_failed", error=str(e))


def split_text_smart(text: str, max_size: int) -> list[str]:  # noqa: PLR0912
    """Split text into chunks trying to preserve code blocks and paragraphs."""
    if len(text) <= max_size:
        return [text]

    chunks = []
    current = ""

    # Split by paragraphs first
    paragraphs = text.split("\n\n")

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_size:
            if current:
                chunks.append(current.strip())
            current = para
        elif current:
            current += "\n\n" + para
        else:
            current = para

    if current:
        chunks.append(current.strip())

    # If still too large, split by lines
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= max_size:
            final_chunks.append(chunk)
        else:
            lines = chunk.split("\n")
            current = ""
            for line in lines:
                if len(current) + len(line) + 1 > max_size:
                    if current:
                        final_chunks.append(current)
                    current = line
                elif current:
                    current += "\n" + line
                else:
                    current = line
            if current:
                final_chunks.append(current)

    return final_chunks
