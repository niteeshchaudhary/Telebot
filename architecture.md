# Telebot Architecture Diagram

## System Overview

Telebot is a Telegram bot that acts as a terminal multiplexer for OpenCode sessions. It allows users to create, manage, and interact with persistent OpenCode sessions entirely from Telegram.

---

## High-Level Architecture

```mermaid
graph TB
    subgraph "Telegram"
        User[Users]
        TG[Telegram Servers]
        BotAPI[Bot API]
    end

    subgraph "Telebot Application"
        Main[main.py - Entry Point]
        App[Application Builder]
        Handlers[Command/Message Handlers]
        SessionMgr[SessionManager]
        Database[(SQLite Database)]
    end

    subgraph "External Systems"
        OpenCode[OpenCode CLI]
        FileSystem[File System]
    end

    User -->|Messages| TG
    TG -->|Exposes| BotAPI
    BotAPI <-->|Long Polling (getUpdates)| Main
    BotAPI -.->|HTTPS Webhook POST| Main
    Main --> App
    App --> Handlers
    Handlers --> SessionMgr
    SessionMgr --> Database
    SessionMgr --> OpenCode
    SessionMgr --> FileSystem
    Handlers --> Database
```

---

## Component Architecture

### 1. **Entry Point Layer** (`app/bot/main.py`)
- **Purpose**: Application bootstrap, webhook/polling setup
- **Key Components**:
  - `create_application()` - Builds Telegram Application with all handlers
  - `post_init()` - Initializes SessionManager, restores sessions
  - `post_shutdown()` - Cleanup on shutdown
  - `run_webhook()` / `run_polling()` - Webhook vs polling modes
  - **Polling Mode**: `application.run_polling()` - Long-polls Telegram's `getUpdates` endpoint
  - **Webhook Mode**: `run_webhook()` - Starts aiohttp server for Telegram to POST updates
  - Health check endpoint (`/health`)

### 2. **Configuration Layer** (`app/config.py`)
- **Settings Class**: Pydantic BaseSettings with environment variable support
- **Key Settings**:
  - `telegram_bot_token` - Bot token from Bot token from BotFather
  - `opencode_executable` - Path to opencode binary
  - `database_url` - SQLite connection string
  - `allowed_user_ids_str` - Comma-separated authorized user IDs
  - Webhook settings (URL, port, path, secret)
  - Session/streaming timeouts and limits

### 3. **Data Layer** (`app/models.py`, `app/database.py`)

#### Models (`app/models.py`)
```mermaid
classDiagram
    class Session {
        +id: int
        +name: str
        +cwd: str
        +status: SessionStatus
        +opencode_session_id: str
        +model: str
        +mode: str
        +last_output: str
        +last_output_fetched_at: datetime
        +created_at: datetime
        +last_used: datetime
    }
    
    class UserState {
        +id: int
        +user_id: int
        +current_session_id: int
        +created_at: datetime
        +updated_at: datetime
    }
    
    class SessionStatus {
        <<enumeration>>
        IDLE
        RUNNING
        CLOSED
        DEAD
    }
    
    Session --> SessionStatus
    UserState --> Session : current_session_id
```

#### Database Layer (`app/database.py`)
- **Database Class**: Async SQLModel wrapper with SQLite
  - Async engine with connection pooling
  - Session factory with `expire_on_commit=False`
  - `init_db()` - Creates tables
  - Context manager for sessions with auto commit/rollback
  
- **SessionRepository**: CRUD operations for sessions
  - `create()`, `get()`, `get_by_name()`, `list()`, `update()`, `delete()`
  
- **UserStateRepository**: Per-user session state
  - `get_current_session(user_id)` / `set_current_session(user_id, session_id)`

### 4. **Service Layer** (`app/services/`)

#### SessionManager (`app/services/session_manager.py`)
**Core service managing OpenCode sessions**

```mermaid
classDiagram
    class SessionManager {
        -_database: Database
        -_opencode_executable: str
        -_user_states: dict
        -_lock: asyncio.Lock
        +create_session(name, cwd) Session
        +get_session(id) Session
        +list_sessions() List[Session]
        +switch_session(user_id, session_id) Session
        +get_current_session(user_id) Session
        +send_message(session_id, message, callback) Tuple[str, str]
        +get_session_model_info(session_id) Dict
        +get_last_output(session_id, force_refresh) str
        +refresh_last_output(session_id) Tuple[bool, str]
        +restart_session(session_id) Session
        +close_session(session_id, force) bool
        +delete_session(session_id) bool
        +adopt_opencode_session(opencode_id) Session
        +get_session_model_info(session_id) Dict
        +list_opencode_sessions() List[Dict]
        +set_session_model(session_id, model) Session
        +set_session_mode(session_id, mode) Session
        +get_last_output(session_id, force_refresh) str
        +fetch_last_output_from_opencode(session_id) str
        +refresh_last_output(session_id) Tuple[bool, str]
    }
```

**Key Responsibilities**:
- **Session Lifecycle**: Create, switch, restart, close, delete
- **OpenCode Integration**: Run `opencode run --format json` as subprocess
- **Streaming Support**: Callback-based incremental updates
- **Model/Mode Management**: Persist and apply model/mode per session
- **Output Caching**: Hybrid cache with auto-refresh (10 min TTL)
- **External Session Adoption**: Import existing OpenCode sessions
- **OpenCode Session Discovery**: List sessions via `opencode session list`

#### OpenCodeSession (`app/services/opencode_session.py`)
**Legacy persistent process manager** (not currently used - kept for reference)

### 5. **Handler Layer** (`app/bot/handlers/`)

#### Command Handlers (`app/bot/handlers/commands.py`)
```mermaid
graph LR
    subgraph "Session Management"
        CMD_NEW["/new"]
        CMD_SWITCH["/switch"]
        CMD_CURRENT["/current"]
        CMD_SESSIONS["/sessions /ls"]
        CMD_CLOSE["/close"]
        CMD_RESTART["/restart"]
        CMD_RENAME["/rename"]
        CMD_SESSION_INFO["/session_info"]
    end
    
    subgraph "Model/Mode"
        CMD_MODEL["/model"]
        CMD_SET_MODEL["/set_model"]
        CMD_MODE["/mode"]
        CMD_SESSION_INFO["/session_info"]
    end
    
    subgraph "Output"
        CMD_LAST["/last"]
        CMD_REFRESH["/refresh"]
        CMD_LOGS["/logs"]
        CMD_SESSION_INFO["/session_info"]
    end
    
    subgraph "File/Directory"
        CMD_PWD["/pwd"]
        CMD_CD["/cd"]
        CMD_UPLOAD["/upload"]
        CMD_DOWNLOAD["/download"]
        CMD_LISTFILES["/listfiles"]
    end
    
    subgraph "System"
        CMD_HELP["/help"]
        CMD_STATUS["/status"]
        CMD_HEALTH["/health"]
        CMD_SHUTDOWN["/shutdown"]
    end
```

**Key Features**:
- All commands check user authorization
- Model info displayed after `/new` and `/switch`
- Streaming output with incremental updates
- Smart output chunking (4000 char limit)
- HTML formatting with emojis

#### Message Handler (`app/bot/handlers/messages.py`)
```mermaid
graph TD
    MSG[Incoming Message] --> AUTH{Authorized?}
    AUTH -->|No| REJECT[Reject]
    AUTH -->|Yes| SESSION{Has Session?}
    SESSION -->|No| NO_SESS[Prompt /new or /switch]
    SESSION -->|Yes| PROCESS[Processing...]
    PROCESS --> STREAM[Stream Callback]
    STREAM --> TOOLS[Track Tools]
    STREAM --> ERRORS[Track Errors]
    TOOLS --> UPDATE[Edit Processing Msg]
    ERRORS --> UPDATE
    PROCESS --> PARSE[Parse Output]
    PARSE --> FORMAT[Format Messages]
    FORMAT --> SEND[Send to Telegram]
```

**Key Features**:
- Streaming callback for real-time tool updates
- Incremental processing message updates
- JSONL output parsing with state machine
- Smart text chunking (preserves code blocks)
- HTML formatting with fallback
- Tool completion notifications

#### Message Parsing (`parse_opencode_output`)
- Parses JSONL (newline-delimited JSON) from OpenCode
- Extracts: text, tool calls, errors, step events
- Tracks tool state (running/completed/failed)
- Formats for Telegram with emojis and HTML

---

## Data Flow

### Session Creation Flow
```mermaid
sequenceDiagram
    User->>Bot: /new myproject ~/projects
    Bot->>SessionManager: create_session("myproject", "~/projects")
    SessionManager->>Database: INSERT session (status=IDLE)
    SessionManager->>OpenCode: opencode run --format json "Starting session"
    OpenCode-->>SessionManager: {sessionID: "ses_xxx", ...}
    SessionManager->>Database: UPDATE session (opencode_session_id, status=IDLE)
    SessionManager-->>Bot: Session object
    Bot->>User: Session Created + Model Info
```

### Message Sending Flow
```mermaid
sequenceDiagram
    User->>Bot: "explain this code"
    Bot->>SessionManager: get_current_session(user_id)
    SessionManager->>SessionManager: send_message(session_id, "explain this code")
    SessionManager->>OpenCode: opencode run --session ses_xxx "explain..."
    OpenCode-->>SessionManager: JSONL stream
    SessionManager->>Callback: stream_callback(event)
    Callback->>Bot: Edit "Processing..." message
    OpenCode-->>SessionManager: Final output
    SessionManager->>Database: Cache output
    SessionManager-->>Bot: Parsed messages
    Bot->>User: Formatted output
```

### Session Switch Flow
```mermaid
sequenceDiagram
    User->>Bot: /switch ses_xxx
    Bot->>SessionManager: get_session_by_identifier("ses_xxx")
    alt Not in DB
        SessionManager->>SessionManager: adopt_opencode_session("ses_xxx")
        SessionManager->>OpenCode: opencode export ses_xxx
        OpenCode-->>SessionManager: {info: {model, agent, ...}}
        SessionManager->>Database: INSERT session
    end
    SessionManager->>Database: UPDATE user_state.current_session_id
    SessionManager-->>Bot: Switched + Model Info
    Bot->>User: Switched + Model Info
```

---

## External Integration: OpenCode

### Commands Used
| Command | Purpose | Format |
|---------|---------|--------|
| `opencode run --format json` | Run message in session | JSONL streaming |
| `opencode export <id>` | Get session info/history | JSON |
| `opencode session list --format json` | List all sessions | JSON array |
| `opencode models` | List available models | Plain text |

### OpenCode JSONL Event Types
| Type | Description |
|------|-------------|
| `text` | Assistant text response (in `part.text`) |
| `tool` / `tool_use` | Tool invocation with input/state |
| `step_start` | Step beginning (thinking) |
| `step_finish` | Step completion |
| `error` | Error message |

---

## Security

- **Authorization**: Per-user allowed list via `allowed_user_ids`
- **Input Validation**: Pydantic models for all inputs
- **SQL Injection Prevention**: SQLModel ORM (parameterized queries)
- **Path Traversal**: `Path.resolve()` for directory operations
- **Command Injection**: `asyncio.create_subprocess_exec` (no shell)

---

## Deployment

### Modes
1. **Polling** (default): `application.run_polling()`
   - Long-polling via Telegram `getUpdates` API
   - Bot initiates HTTPS requests to Telegram `getUpdates` endpoint
   - Telegram responds with pending updates
   - No public HTTPS endpoint required
   - Simpler deployment (no reverse proxy/certs needed)
   - Default mode when `WEBHOOK_MODE=false`

2. **Webhook**: `run_webhook()` with aiohttp server
   - Telegram pushes updates via HTTPS POST to your endpoint
   - Requires public HTTPS URL with valid certificate
   - Lower latency (push vs pull)
   - Requires reverse proxy (nginx) + SSL certs
   - Enabled when `WEBHOOK_MODE=true` + `WEBHOOK_URL` set

### Docker
- `Dockerfile` - Multi-stage build
- `docker-compose.yml` - Service orchestration
- SQLite persistence via volume

---

## Key Design Patterns

1. **Repository Pattern**: Database abstraction
2. **Callback Pattern**: Streaming updates from OpenCode
3. **Hybrid Caching**: Memory + DB with TTL
4. **Graceful Degradation**: HTML fallback for formatting
5. **Graceful Session Adoption**: Import external OpenCode sessions
6. **Auto-refresh**: Stale cache auto-refresh (10 min)

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12+ |
| Async Framework | asyncio |
| Telegram Library | python-telegram-bot v21+ |
| Database | SQLite + SQLModel (SQLAlchemy) |
| Config | Pydantic Settings |
| Logging | Rich + Structured Logging |
| Testing | pytest + pytest-asyncio |
| Linting | Ruff |
| Type Checking | mypy |
| Formatting | Black |

---

## File Structure Summary

```
telebot/
├── app/
│   ├── __init__.py
│   ├── config.py              # Configuration
│   ├── models.py              # Data models
│   ├── database.py            # Database layer
│   ├── logging.py             # Logging setup
│   ├── bot/
│   │   ├── main.py            # Entry point
│   │   └── handlers/
│   │       ├── commands.py    # 30+ command handlers
│   │       └── messages.py    # Message/document handling
│   └── services/
│       ├── session_manager.py # Core business logic
│       └── opencode_session.py
├── tests/
├── Makefile
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env
└── telebot.db
```

---

## Critical Flows Summary

| Operation | Components Involved |
|-----------|---------------------|
| Create Session | Bot → SessionManager → Database + OpenCode |
| Send Message | Bot → SessionManager → OpenCode (streaming) → Callback → Bot |
| Switch Session | Bot → SessionManager → Database + (OpenCode adopt) |
| Get Model Info | Bot → SessionManager → OpenCode export |
| Refresh Output | Bot → SessionManager → OpenCode export → Cache |
| List Sessions | Bot → SessionManager → Database + OpenCode CLI |
| File Upload | Bot → SessionManager → FileSystem |
| Set Model/Mode | Bot → SessionManager → Database (persisted for future runs) |

---

## Extensibility Points

1. **New Commands**: Add handler in `commands.py`, register in `main.py`
2. **New Output Formats**: Extend `parse_opencode_output()` and `send_formatted_message()`
3. **New OpenCode Features**: Add methods to `SessionManager`
4. **Different Databases**: Swap `Database` class (SQLModel supports PostgreSQL, MySQL)
5. **Auth Providers**: Extend `is_user_allowed()` in `SessionManager`