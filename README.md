# AI QQ Email Support Agent

An AI-powered email handling system for automating customer support over QQ email. The system automatically monitors new QQ emails, classifies them with an LLM (by intent and urgency), routes them to the appropriate handler, drafts a professional response, and sends the reply via SMTP. Complex or high-priority emails are escalated to a human and optionally pushed via the official QQ Bot.

## Key Features

- Asynchronous monitoring of new QQ emails (IMAP polling)
- AI-powered classification by intent, urgency, and target terminal platform
- Prompt-embedded JSON Schema for classification with tolerant output parsing (bare JSON or fenced code block) — no API-level structured output required
- Automatic drafting of professional, accurate responses
- Automatic reply via SMTP
- Human escalation for complex or high-priority emails
- Optional QQ Bot push notifications (c2c / group) for escalations

## Technical Architecture

The core workflow is implemented as a custom asynchronous state machine
(`EmailWorkflow`) — it does **not** depend on the LangGraph library. It is built on:

- [openai](https://pypi.org/project/openai/) (AsyncOpenAI) — LLM classification & response drafting; compatible with OpenAI / DeepSeek / other OpenAI-compatible APIs
- [aioimaplib](https://pypi.org/project/aioimaplib/) — asynchronous IMAP email fetching
- [aiosmtplib](https://pypi.org/project/aiosmtplib/) — asynchronous SMTP reply sending
- [httpx](https://pypi.org/project/httpx/) — QQ Bot HTTP API client
- [beautifulsoup4](https://pypi.org/project/beautifulsoup4/) — HTML email body cleaning
- [tenacity](https://pypi.org/project/tenacity/) — classification retry on failure
- [python-dotenv](https://pypi.org/project/python-dotenv/) — `.env` configuration loading
- Python 3.10+ (uses PEP 604 `X | None` union syntax)

> Note: Push notifications use the official QQ Bot (c2c / group). The earlier
> Feishu (Lark) integration — both push and Bitable record persistence — has
> been removed; classification and handling results are kept in memory only.
> If you need audit logging, see "Extending Functionality" below.

## Requirements

- Python 3.10+
- QQ email account with IMAP enabled and its **authorization code** (not the login password)
- API key for an OpenAI-compatible chat model (OpenAI, DeepSeek, etc.)
- (Optional) A QQ official Bot for push notifications

## Installation

### 1. Clone the project

```bash
git clone <repository-url>
cd AIHandleQQEmail
```

### 2. Install with uv (recommended)

This project is managed with [uv](https://docs.astral.sh/uv/) — install it first if you
don't have it yet (see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)).

```bash
uv sync                 # create .venv and install exactly what uv.lock pins (incl. dev group)
```

This installs the `ai-email` console entry point.
Run it through `uv run`:

```bash
uv run ai-email --help
```

> pip fallback: `pip install .` still installs the runtime dependencies (the dev toolchain
> — pytest / pyinstaller — is managed via uv dependency groups).

## Configuration

The configuration file is stored at `~/.ai-email/.env` (outside the project directory, so
secrets are not committed). The recommended way to create it is the interactive setup wizard,
which validates your model connection and QQ Bot credentials:

```bash
ai-email setup
```

For the QQ Bot notification section, the wizard uses **scan-to-configure**: it renders a QR code
in the terminal, and after you scan it with QQ, the App ID, Client Secret, and your real `openid`
(as the notification target) are filled in automatically. Manual entry is intentionally not
offered — there is no API to look up an openid; it only surfaces via scan-binding or bot
message events.

You may also copy `.env.example` to `~/.ai-email/.env` and fill it in manually.

Required environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `MODEL` | Yes | Model name (e.g. `gpt-4o`, `deepseek-chat`) |
| `BASE_URL` | Yes | API base URL |
| `API_KEY` | Yes | API key |
| `QQEMAIL` | Yes | QQ email address |
| `EMAIL_PASSWORD` | Yes | QQ email **authorization code** (not login password) |
| `QQ_APP_ID` | No | QQ Bot App ID (enables notifications) |
| `QQ_CLIENT_SECRET` | No | QQ Bot Client Secret |
| `QQ_NOTIFY_TARGET` | No | Notification target (`c2c:openid` or `group:groupid`; real values auto-filled by scan-to-configure) |
| `WORKER_CONCURRENCY` | No | Max emails processed in parallel (default `4`). Not prompted by `setup`; set manually in `~/.ai-email/.env` only if you need to tune it. |
| `LLM_TIMEOUT_SECONDS` | No | Timeout per LLM request (default `60`). Raise it for slow models; the openai SDK default of 600s would let a hung request block a worker for up to 30 minutes. Not prompted by `setup`. |
| `RETRY_BACKOFF_SECONDS` | No | Backoff before a failed email is retried (default `30`). Prevents retry storms while SMTP is down. |
| `LOG_LEVEL` | No | Logging level (default `INFO`; set `DEBUG` for troubleshooting without code changes). |

Notes:
- QQ email requires an **authorization code** instead of the login password; generate it in QQ email settings.
- `MODEL`, `BASE_URL`, `API_KEY` are configured according to your model provider.
- QQ Bot notifications are enabled only when all three `QQ_*` variables are set; placeholder values (e.g. the literal `c2c:openid`) are detected and treated as unset.

## Usage

After `uv run ai-email setup`, use the CLI subcommands (prefix each with `uv run`, or activate
`.venv` first):

```bash
uv run ai-email setup      # Interactive configuration wizard
uv run ai-email            # Run in the foreground
uv run ai-email daemon     # Run as a background daemon
uv run ai-email status     # Check daemon status
uv run ai-email stop       # Stop the daemon
```

Running the service starts the listener, which polls for new emails and processes each one
through the workflow automatically.

## Workflow

1. **Email Monitoring**: Asynchronously polls the QQ mailbox for new emails via IMAP UID-incremental fetch (only UIDs beyond the persisted `last_uid`), with a UIDVALIDITY guard that re-baselines safely if the mailbox's UID space changes.
2. **Email Classification**: The LLM analyzes the email and classifies it by intent, urgency, and terminal platform.
3. **Routing**: Emails are routed based on intent and urgency:
   - `complex_request`, or `high`/`critical` urgency → escalated to a human (with optional QQ Bot push)
   - `question` / `feature` → knowledge base search step
   - `bug` → ticket creation step (priority P0/P1/P2 assigned in memory)
4. **Response Drafting**: The LLM drafts a response based on the email content and handling results.
5. **Email Sending**: The reply is sent automatically via SMTP.

## Email Classification Rules

The system classifies emails on the following dimensions:

- **Intent** (`intent`):
  - `question`: General inquiries
  - `bug`: Bug reports
  - `building`: Deployment-related issues
  - `feature`: Feature requests
  - `complex_request`: Complex requests requiring human handling

- **Urgency** (`urgency`):
  - `low`: Low priority
  - `medium`: Medium priority
  - `high`: High priority
  - `critical`: Critical

- **Terminal** (`terminal`): `Web`, `Windows`, `Android`, `Mac`, `iOS`, or `Not provided`

## Reliability & State

The daemon keeps all state in SQLite at `~/.ai-email/seen.db` (stdlib `sqlite3`, no extra
dependencies), so a restart or crash never causes lost or duplicate processing:

- **UID-incremental fetch** — each poll pulls only UIDs greater than the persisted `last_uid`
  (`UID SEARCH last_uid+1:*`); `last_uid` is advanced as emails are fetched.
- **UIDVALIDITY guard** — the IMAP UIDVALIDITY value is persisted; if it changes (the mailbox's
  UID space rolled over), the dedup table and `last_uid` baseline are reset so reprocessing stays safe.
- **Atomic dedup** — each email is claimed with a single `INSERT OR IGNORE` (`status='processing'`),
  collapsing the check-then-insert race across concurrent workers; only the winning worker processes it.
- **Crash reconciliation** — on startup, `reconcile_orphans` rolls any email still in `processing`
  (interrupted mid-handling) back into the retry queue; emails already marked `done` are preserved.
- **Retry with backoff** — failed sends or exceptions go to a `retry_queue` and are re-fed into the
  pipeline after a backoff window; after more than 5 attempts the email is dropped as a dead letter.
- **Bounded concurrency** — up to `WORKER_CONCURRENCY` (default `4`) emails are handled in parallel.

Runtime files under `~/.ai-email/`:

| File | Purpose |
|------|---------|
| `.env` | Configuration (created by `ai-email setup`) |
| `seen.db` | Dedup ledger, `last_uid` / UIDVALIDITY, retry queue |
| `ai-email.pid` | Worker PID (informational; used by `stop` to target the process) |
| `ai-email.lock` | Exclusive liveness lock — the source of truth for "is the daemon running" |
| `ai-email.log` | Daemon stdout/stderr log (read by `ai-email status`) |

## Continuous Integration

The project includes a GitHub Actions workflow (`.github/workflows/build-and-test.yml`)
that runs on push/PR to `main` and on releases, across a Python `3.10` / `3.13` matrix:

1. `ruff check` (lint) + `ruff format --check` (formatting) + `mypy` (type check)
2. `pytest` (test suite — no secrets required; `tests/conftest.py` supplies placeholder env)
3. `uv build`, artifact upload, and a wheel install + smoke test (`ai-email --help`)

No repository secrets are needed: the test suite never touches the network.

## Project Structure

```
.
├── ai_email/
│   ├── __init__.py                # Package entry, re-exports main
│   ├── __main__.py                # Enables `python -m ai_email`
│   ├── cli.py                     # CLI: setup wizard, daemon/worker, stop/status, PID+lock liveness
│   ├── workflow.py                # Core engine: EmailWorkflow, LLM calls, routing, SMTP, pipeline
│   ├── qq_email_listener.py       # Async IMAP listener (UID-incremental fetch, UIDVALIDITY guard)
│   ├── persistence.py             # SQLite state: dedup ledger, last_uid/UIDVALIDITY, retry queue
│   ├── qq_bot.py                  # QQ official Bot notification client (OAuth + token cache)
│   ├── qq_onboard.py              # QQ Bot scan-to-configure onboarding (QR bind task, AES-GCM secret decrypt)
│   └── log_setup.py               # JSON single-line logging setup
├── tests/                         # pytest suite (workflow, listener, persistence, concurrency, ...)
├── pyproject.toml                 # Project metadata and dependencies
├── build.sh                       # Build helper (uv + PyInstaller packaging)
├── Dockerfile                     # Container image definition
├── .env.example                   # Template for ~/.ai-email/.env
└── .github/workflows/
    └── build-and-test.yml         # GitHub Actions build, lint & test configuration
```

## Development Guide

### Main Components

1. **`qq_email_listener.QQEmailListener`** — async generator that fetches new emails via IMAP UID-incremental search (with a UIDVALIDITY guard) and transparently reconnects on failure.
2. **`EmailWorkflow`** — the custom async workflow carrying `WorkflowState` through nodes:
   - `classify_intent` — LLM classification (JSON Schema embedded in prompt, tolerant JSON extraction, with retry)
   - `search_knowledge_base` — knowledge base lookup for `question`/`feature` (placeholder; swap the method body to wire up a RAG/vector-retrieval backend — contract: hits go into `state.handle_results`)
   - `create_ticket` — ticket creation for `bug` (placeholder; swap the method body to wire up a ticketing platform — contract: ticket number/link goes into `state.handle_results`)
   - `to_human` — human escalation (+ QQ Bot push) for complex/high-priority emails
   - `draft_response` — LLM response drafting
   - `send_reply` — SMTP reply
3. **`QQBotNotifier`** — QQ official Bot client with OAuth token caching and auto-refresh.
4. **CLI** — `setup` wizard (with online validation), plus daemon `run`/`stop`/`status`. Liveness is tracked by an exclusive lock on `~/.ai-email/ai-email.lock` (held while the worker runs, so a recycled PID can't be mistaken for the daemon); the worker PID is also recorded in `~/.ai-email/ai-email.pid`.

### Extending Functionality

You can extend the following based on your requirements:
- Add more email classification types
- Integrate a RAG / vector-retrieval knowledge base behind `search_knowledge_base`
- Wire `create_ticket` to a ticket management platform
- Add multi-language support
- Enhance the human-review interface

## Notes

1. IMAP service must be enabled for QQ email.
2. The QQ email **authorization code** must be used instead of the login password.
3. Ensure a stable network connection to access the AI API.
4. In production, consider adding additional error handling and logging.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
