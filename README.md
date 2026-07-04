# Telebot - Telegram Remote Controller for OpenCode

Control your local OpenCode instances from Telegram. Like tmux but for AI coding assistants.

## Features

- **Persistent Sessions**: Multiple OpenCode processes that survive bot restarts
- **Multi-user**: Each Telegram user has their own active session
- **Full Terminal Control**: Send input, receive streaming output, interrupt with Ctrl+C
- **Session Management**: Create, switch, list, close, restart, rename sessions
- **File Operations**: Upload/download files to session working directories
- **Directory Navigation**: Change working directory per session
- **Process Persistence**: Sessions restored on bot restart with dead process detection

## Requirements

- Python 3.12+
- OpenCode CLI installed and in PATH (or set `OPENCODE_EXECUTABLE`)
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

## Quick Start

### 1. Clone and Setup

```bash
git clone <repo-url>
cd telebot
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS
```

### 2. Install Dependencies

```bash
# Using uv (recommended)
uv sync

# Or with pip
pip install -e ".[dev]"
```

### 3. Run the Bot

```bash
# Development
uv run python -m app.bot.main

# Or with Make
make run
```

### 4. Docker

```bash
docker-compose up -d
```

## Configuration

Environment variables (see `.env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from @BotFather |
| `OPENCODE_EXECUTABLE` | No | `opencode` | Path to OpenCode binary |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./telebot.db` | SQLAlchemy database URL |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `ALLOWED_USER_IDS` | No | (empty = all) | Comma-separated Telegram user IDs |
| `DEFAULT_CWD` | No | `~` | Default working directory for new sessions |
| `WEBHOOK_MODE` | No | `false` | Enable webhook mode (production) |
| `WEBHOOK_URL` | No* | - | Public HTTPS webhook URL |
| `WEBHOOK_PORT` | No | `8443` | Local port to listen on |
| `WEBHOOK_PATH` | No | `/webhook` | Webhook path |
| `WEBHOOK_SECRET` | No | - | Secret token for webhook validation |

*Required when `WEBHOOK_MODE=true`

## Webhook Mode (Production)

For production, use webhooks instead of polling. This requires a public HTTPS URL.

### Quick Start with Cloudflare Tunnel (Free HTTPS)

[Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/) provides free HTTPS tunnels to localhost - no domain, certs, or port forwarding needed.

#### 1. Install cloudflared

```bash
# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared

# macOS
brew install cloudflare/cloudflare/cloudflared

# Windows
# Download from https://github.com/cloudflare/cloudflared/releases
```

#### 2. Run the tunnel

```bash
# Creates a temporary *.trycloudflare.com URL
cloudflared tunnel --url http://localhost:8443
```

Output example:
```2024-01-15T10:30:00Z INF Your quick Tunnel has been created! Visit it at (https://abc-123.trycloudflare.com)
```

#### 3. Configure webhook

```bash
# In .env
WEBHOOK_MODE=true
WEBHOOK_URL=https://abc-123.trycloudflare.com/webhook
WEBHOOK_PORT=8443
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET=your-secret-token
```

#### 4. Run

```bash
docker-compose up -d
# or locally
uv run python -m app.bot.main
```

#### 5. Set webhook in Telegram (auto-done on startup)

The bot automatically calls `setWebhook` on startup. You can verify:
```bash
curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

### Permanent Tunnel (Custom Domain)

For a permanent URL with your own domain:

```bash
# 1. Add domain to Cloudflare
# 2. Create tunnel
cloudflared tunnel create telebot

# 3. Route to local service
cloudflared tunnel route dns telebot bot.yourdomain.com

# 4. Run tunnel
cloudflared tunnel run --config ~/.cloudflared/config.yml telebot
```

Config (`~/.cloudflared/config.yml`):
```yaml
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: bot.yourdomain.com
    service: http://localhost:8443
  - service: http_status:404
```

### Production Notes

- **Reverse proxy recommended**: Use nginx/Traefik/Caddy to terminate SSL and forward HTTP to container port
- **Webhook secret**: Set `WEBHOOK_SECRET` for request validation
- **Health check**: Available at `http://localhost:8443/health`
- **Telegram allowed ports**: 443, 80, 88, 8443 (use 443 or 8443 for direct)

## Commands

| Command | Description |
|---------|-------------|
| `/new <name> [dir]` | Create new OpenCode session |
| `/sessions` | List all sessions |
| `/switch <id\|name>` | Switch active session |
| `/current` | Show current session |
| `/close <id\|name>` | Close a session |
| `/restart <id\|name>` | Restart a session |
| `/interrupt [id\|name]` | Send Ctrl+C to session |
| `/pwd` | Show working directory |
| `/cd <dir>` | Change working directory |
| `/rename <id\|name> <new>` | Rename session |
| `/upload` | Reply to file to upload |
| `/download <file>` | Download file from session dir |
| `/listfiles` | List files in session directory |
| `/help` | Show help |
| `/status` | Show bot status |

## Usage

1. **Create a session**: `/new myproject ~/projects/myproject`
2. **Switch to it**: `/switch myproject` or `/switch 1`
3. **Send messages**: Just type and send - they go to OpenCode stdin
4. **Get responses**: OpenCode output streams back as Telegram messages
5. **Interrupt**: `/interrupt` sends Ctrl+C to running process

## Architecture

```
Telegram → Bot → Command Router → Session Manager → OpenCode Processes
                                        ↓
                                   SQLite Database
```

- **SessionManager**: Manages session lifecycle, user session mapping
- **OpenCodeSession**: Wraps asyncio subprocess with streaming I/O
- **Database**: SQLModel/SQLAlchemy for persistence
- **Handlers**: Telegram command and message handlers

## Development

```bash
# Install dev dependencies
make dev

# Run tests
make test

# Lint
make lint

# Format
make format

# Type check
make typecheck
```

## Project Structure

```
app/
├── bot/
│   ├── handlers/       # Telegram command/message handlers
│   └── main.py         # Bot application setup
├── services/
│   ├── opencode_session.py  # OpenCode process management
│   └── session_manager.py   # Session lifecycle management
├── config.py           # Configuration (Pydantic Settings)
├── database.py         # Database layer (SQLModel)
├── logging.py          # Structured logging (Rich)
└── models.py           # Data models (Pydantic + SQLModel)
```

## Security

- Only allowed user IDs can use the bot (set `ALLOWED_USER_IDS`)
- No shell access - only OpenCode subprocess communication
- Sessions isolated by working directory

## License

MIT