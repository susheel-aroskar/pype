# CLAUDE.md — FastAPI Project Guide

This file provides Claude with context, conventions, and instructions for working on this project. Read it fully before making any changes.

---

## Project Overview

- **Purpose**: “pype” is a push-pull based load balancing solution to replace the traditional reverse proxy based load-balancers. It acts as a central rendezvous point + message broker. Instead of clients sending requests to a reverse proxy/load balancer that then routes these requests to backend services both the clients and the backend services connect to pype instance and,
	- Clients push their API requests to their in memory queues for designated  for individual services
    - Services pull their requests from these queues and process them
	- Responses to the client flow back similarly through per client transient queues. Clients can either poll or block on their response queue when they are ready to consume their responses
- **Primary consumers**: API clients and backend micro-services
- **Auth model**: JWT in Authorization header
- **Architecture**: FastAPI based async REST server (pype) and requests based single threaded blocking client

---

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI |
| Python | 3.12+ |
| Package manager | `pip` |
| Validation | Pydantic v2 |
| Testing | pytest + request |
| Linting | Ruff |
| Type checking | mypy (strict) |

---

## Project Structure

The repo is a workspace with two independent Python distributions: `pype-server` and `pype-client`. Each lives in its own subdirectory with its own `pyproject.toml`. The top-level `pyproject.toml` only houses shared linter/type-checker/pytest config.

```
project_root/
├── CLAUDE.md
├── pyproject.toml             # workspace-only: shared ruff/mypy/pytest config
├── .env                       # PYPE_SERVER_* and PYPE_CLIENT_* env vars
├── server/
│   ├── pyproject.toml         # pype-server distribution
│   ├── src/
│   │   └── pype_server/
│   │       ├── main.py        # FastAPI app factory
│   │       ├── config.py      # Settings via pydantic-settings (PYPE_SERVER_ prefix)
│   │       ├── schemas/       # Pydantic request/response schemas
│   │       ├── routers/       # APIRouter modules, one per domain
│   │       └── exceptions.py  # Custom exception classes + handlers
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       └── integration/
└── client/
    ├── pyproject.toml         # pype-client distribution
    ├── .env.example
    ├── src/
    │   └── pype_client/       # ServiceClient, PypeClient, connections, schemas
    └── tests/
        ├── unit/              # mocked HTTP via `responses`
        └── integration/       # spawns real server via uvicorn subprocess
```

Install both packages editable in one venv:
```bash
pip install -e "./server[dev]" -e "./client[dev]" ruff mypy
```

---

## Core Conventions

### General Python

- Python **3.12+** features are available and encouraged (e.g., `type X = ...`, `match` statements).
- Use **type annotations everywhere** — parameters, return types, class attributes. No `Any` unless truly necessary; if you must, add a `# type: ignore` comment explaining why.
- Prefer **composition over inheritance**.
- Keep functions **small and single-purpose**. If a function needs a large docstring to explain what it does, consider splitting it.
- Use `pathlib.Path` instead of `os.path`.
- Never use mutable default arguments. Use `None` and set inside the function body.

### Async

- All I/O (DB, HTTP, file) must be **async** inside server. Use `async def` and `await` throughout.
- Clients will use requests module based sync, blocking I/O
- Do not use `time.sleep` — use `asyncio.sleep`.
- Use `anyio` for concurrency primitives (task groups, locks) rather than raw `asyncio`.
- CPU-bound work must be offloaded with `asyncio.to_thread` or a task queue.

### FastAPI Specifics

- Define one `APIRouter` per domain/resource in `routers/`. Mount them in `main.py`.
- Use **dependency injection** for DB sessions, auth, pagination, and other cross-cutting concerns — never import global state inside a route.
- Always declare response models explicitly: `@router.get("/", response_model=UserOut)`.
- Use `status` constants from `fastapi` (e.g., `status.HTTP_201_CREATED`), not raw integers.
- Tag all routers with descriptive tags for auto-generated OpenAPI docs.
- Raise `HTTPException` from route handlers only. Let services raise domain exceptions; catch and convert them in routes or via exception handlers registered in `main.py`.

```python
# Good
@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> UserOut:
    return await service.create(body)
```

### Pydantic Schemas

- Separate schemas for **Create**, **Update**, **Out** (response), and **DB** (internal) shapes. Do not reuse the same model for input and output.
- Use `model_config = ConfigDict(from_attributes=True)` on response schemas that are built from ORM objects.
- Use `Field(...)` with `description=` for every field on public-facing schemas — it populates the OpenAPI docs.
- Validate at the boundary (router). Never pass raw dicts deep into services.

```python
class UserCreate(BaseModel):
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., min_length=8, description="Plain-text password (hashed on write)")

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime
```

### Configuration

- All config lives in `app/config.py` using `pydantic-settings` `BaseSettings`.
- Never hardcode secrets, URLs, or environment-specific values in source files.
- Load settings as a **cached singleton** via a `lru_cache`-wrapped factory injected as a dependency.

```python
# config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: PostgresDsn
    secret_key: SecretStr
    debug: bool = False

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Error Handling

- Define custom exception classes in `exceptions.py` (e.g., `NotFoundError`, `ConflictError`).
- Register global exception handlers in `main.py` via `app.add_exception_handler(...)`.
- Return structured error bodies: `{"detail": "...", "code": "USER_NOT_FOUND"}`.
- Log the full traceback for 5xx errors; do not expose internal details to the client.

---

## Testing

- Use `pytest` with `pytest-asyncio` in **auto mode**.
- Use `httpx.AsyncClient` with `ASGITransport` for integration tests — never spin up a real server.
- Use a separate test database (or an in-memory SQLite) and run Alembic migrations in the test session fixture.
- Aim for **high coverage of the service layer**; route tests should cover happy path + key error cases.
- Name tests descriptively: `test_create_user_returns_201_with_valid_payload`.
- Use `pytest.mark.parametrize` for data-driven cases rather than copy-pasting test bodies.
- Mock only at the boundary (e.g., external HTTP calls). Prefer real DB queries in integration tests.

```python
# conftest.py pattern
@pytest_asyncio.fixture(scope="session")
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

---

## Code Quality

### Linting & Formatting

Run before every commit:

```bash
ruff check . --fix
ruff format .
mypy src/
```

Key Ruff rules enabled: `E`, `F`, `I`, `UP`, `B`, `ANN`, `RUF`.

### Commits

- Use **Conventional Commits**: `feat:`, `fix:`, `refactor:`, `test:`, `chore:`, `docs:`.
- Keep commits atomic — one logical change per commit.
- Never commit: `.env`, secrets, compiled files, or `__pycache__`.

---

## Instructions for Claude

### When Adding a New Endpoint

1. Create/update the Pydantic schemas in `schemas/`.
2. Add business logic to the relevant service in `services/`.
3. Add any DB access to the repository in `repositories/`.
4. Register the route in the appropriate router in `routers/`.
5. Write integration tests covering the happy path and at least one error case.
6. Update `README.md` if the public API surface changes.

### When Modifying the Database Schema

1. Update the SQLAlchemy model in `models/`.
2. Generate an Alembic migration: `alembic revision --autogenerate -m "describe change"`.
3. Review the generated migration before applying — autogenerate is not always correct.
4. Update affected schemas, services, and tests.

### When Asked to Refactor

- Preserve existing behaviour unless explicitly told otherwise.
- Run the full test suite mentally before proposing changes; flag any tests that will break.
- Prefer small, incremental changes over large rewrites.

### When Something Is Ambiguous

- Ask one clarifying question before proceeding, not a list of five.
- State your assumption explicitly and proceed — don't stall.

### What NOT to Do

- Do not install new dependencies without asking first.
- Do not change `pyproject.toml`, CI config, or Alembic env without flagging it.
- Do not bypass the service/repository layering (e.g., raw DB queries in route handlers).
- Do not write `print()` for debugging — use `logging` with the appropriate level.
- Do not swallow exceptions silently (`except Exception: pass` is never acceptable).
- Do not use `Optional[X]` — use `X | None` (Python 3.10+ union syntax).

---

## Common Commands

```bash
# Run dev server
.venv/bin/uvicorn pype_server.main:app --reload

# Run tests (both packages)
pytest -v

# Run only server tests
pytest server/tests -v

# Run only client tests
pytest client/tests -v

# Lint + format
ruff check . --fix && ruff format .

# Type check
mypy src/

# Create a migration
alembic revision --autogenerate -m "add users table"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

---

## Notes & Decisions Log

<!-- Use this section to record architectural decisions, trade-offs made, and context that isn't obvious from the code. -->

| Date | Decision | Reason |
|---|---|---|
| YYYY-MM-DD | Example: chose async SQLAlchemy over SQLModel | Needed fine-grained control over session lifecycle |
