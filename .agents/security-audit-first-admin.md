# Security Audit: First-Administrator Provisioning
**Jobyn AI V2 — READ-ONLY audit, no changes made**
**Date:** 2026-09-22
**Auditor:** Kiro AI Security Audit Step
**Scope:** `backend/scripts/provision_first_admin.py`, `backend/database/session.py`, `backend/models/user.py`, `backend/auth/password.py`, `alembic/versions/d7e8f9a0b1c2_add_user_role.py`, `tests/test_first_admin_provisioning.py`, `tests/conftest.py`

---

## Executive Summary

The first-administrator provisioning implementation is **well-designed and secure for its intended scope**. It correctly restricts itself to the repository-local SQLite database, enforces environment guards before any DB access, uses strong password hashing (Argon2id with bcrypt fallback), validates and normalises email input, and protects the production database during tests. Four minor/moderate issues are identified, none of which are critical blockers.

---

## Check Results

### CHECK 1 — Actual Database Target Validation
**PASS**

The script enforces a strict path-equality check that limits provisioning to the repository-local `jobyn.db` file, and explicitly rejects PostgreSQL URLs.

**Evidence:**

- `provision_first_admin.py` lines 30–32: `_EXPECTED_LOCAL_DB_PATH` is computed as `(_REPO_ROOT / "jobyn.db").resolve()`.
- `provision_first_admin.py` lines 62–70 (`validate_local_sqlite_database`): Calls `_resolve_sqlite_database_path`, then enforces `resolved_path != _EXPECTED_LOCAL_DB_PATH` — raises `RuntimeError` if the resolved path is anything other than the repo-local DB.
- `provision_first_admin.py` lines 37–42 (`_resolve_sqlite_database_path`): `parsed.get_backend_name() != "sqlite"` raises `RuntimeError("Only SQLite databases are supported …")`, blocking PostgreSQL, MySQL, etc.
- `provision_first_admin.py` lines 43–44: `:memory:` and empty database names are rejected explicitly.
- Tests (`test_first_admin_provisioning.py` lines 31–46): `test_alternative_sqlite_path_is_rejected`, `test_postgresql_database_is_rejected`, `test_in_memory_sqlite_is_rejected` all confirm this validation.

---

### CHECK 2 — Environment Guard Before Database Access
**PASS**

The environment guard fires at the very top of `provision_first_admin()`, before any session is opened.

**Evidence:**

- `provision_first_admin.py` lines 113–116:
  ```python
  async def provision_first_admin(email: str, password: str) -> User | None:
      _ensure_provisioning_is_allowed_for_runtime()   # <-- fires first
      validate_local_sqlite_database()                # <-- then DB path check
      ...
      async with async_session_maker() as session:    # <-- DB opened only after guards
  ```
- `provision_first_admin.py` lines 75–78 (`_ensure_provisioning_is_allowed_for_runtime`): Checks `os.getenv("ENVIRONMENT", "development").lower() == "test"` and raises if true.
- `conftest.py` lines 10–11: Sets `os.environ["ENVIRONMENT"] = "test"` **before any imports** — meaning any accidental call to the provisioner from tests will be blocked.
- `test_first_admin_provisioning.py` line 56: `test_production_provisioning_rejects_test_environment` verifies this guard.

**Note:** The environment guard reads the raw `ENVIRONMENT` env-var directly (`os.getenv`) rather than through `get_settings()`. This is actually stronger — it avoids the risk of a cached settings object carrying a stale value, and works even if `get_settings()` is not yet called.

---

### CHECK 3 — Prevention of Duplicate Administrators and Existing-User Promotion
**PASS with one moderate weakness (see note)**

The script prevents duplicate admins and silently skips existing-user promotion, both within an atomic transaction.

**Evidence:**

- `provision_first_admin.py` lines 124–131: Inside `session.begin()`, queries for any non-deleted user with `role == "admin"`. If one exists, raises `RuntimeError("An administrator already exists …")` — no second admin can be created.
- `provision_first_admin.py` lines 133–140: Queries for any existing non-deleted user with the same normalised email. If one exists, **returns `None` without modifying it** — existing non-admin accounts are never promoted.
- Both checks occur inside `async with session.begin()`, so they are executed within a single transaction. Inserts are only executed when both checks pass.

**⚠ Moderate weakness — TOCTOU / no database-level uniqueness constraint on `role='admin'`:**

The "only one admin" guarantee relies on an **application-level SELECT-then-INSERT** within a SQLite transaction. SQLite's default isolation level (`SERIALIZABLE` with deferred locking) means a second concurrent process running the provisioner could both pass the "no admin exists" check before either commits. In practice this is very low risk for a one-time provisioning CLI, but there is no partial unique index (e.g. `WHERE role = 'admin'`) on the `users` table to enforce this at the database level. See Recommendations.

---

### CHECK 4 — Correct Admin Role, Active and Verified Status
**PASS**

The provisioned user is created with all three required flags set correctly.

**Evidence:**

- `provision_first_admin.py` lines 153–159:
  ```python
  user = User(
      email=normalized_email,
      hashed_password=hash_password(password),
      full_name=None,
      is_active=True,      # ✓
      is_verified=True,    # ✓
      role="admin",        # ✓
  )
  ```
- `backend/models/user.py` lines 44–57 confirm the model columns: `is_active` defaults to `True` server-side (`server_default=text("true")`), `is_verified` defaults to `False` server-side (`server_default=text("false")`), and `role` defaults to `"user"` server-side. The provisioner **explicitly overrides** `is_verified=True` and `role="admin"` — correct.

---

### CHECK 5 — Secure Password Hashing and Handling
**PASS with one minor weakness (see note)**

Password hashing uses Argon2id (preferred) with bcrypt as a secure fallback. No plaintext password is ever logged or printed.

**Evidence:**

- `backend/auth/password.py` lines 22–32: Argon2id is configured with `time_cost=3`, `memory_cost=65536` (64 MiB), `parallelism=4`, `hash_len=32`, `salt_len=16` — these are strong, OWASP-recommended parameters.
- `backend/auth/password.py` lines 38–40 (`_hash_with_bcrypt`): Bcrypt with `gensalt()` (default cost factor 12) is used as the fallback.
- `provision_first_admin.py` line 107: `getpass.getpass()` is used for terminal input — password is not echoed.
- No `print()`, `logging`, or `sys.stderr` call anywhere in the provisioner emits the password or hash.
- `provision_first_admin.py` lines 197–198: The success print emits only `created.email`, not any password data.

**⚠ Minor weakness — plaintext password not cleared from memory:**

After `hash_password(password)` is called on line 154, the plaintext `password` variable remains in memory on the Python heap until garbage-collected. Python does not support explicit memory zeroing of `str` objects. This is a known Python limitation and is very low risk in a local CLI context, but worth documenting. There is no `del password` call after hashing (grep confirmed). See Recommendations.

---

### CHECK 6 — Email Validation and Normalization
**PASS**

Email is validated with Pydantic's `EmailStr` type adapter and normalised to lowercase, stripped of whitespace.

**Evidence:**

- `provision_first_admin.py` lines 34–37 (`_normalize_email`):
  ```python
  def _normalize_email(email: str) -> str:
      normalized = TypeAdapter(EmailStr).validate_python(email)
      return str(normalized).strip().lower()
  ```
- Pydantic's `EmailStr` uses `email-validator` under the hood, which performs RFC 5322-compliant syntax checking including MX-record-optional validation.
- `_normalize_email` is called at two points: in `_read_email_and_password()` (line 109) for CLI input, and again inside `provision_first_admin()` (line 117) for the programmatic path — double validation.
- `test_first_admin_provisioning.py` line 65: `test_normalized_email_is_valid_and_lowercase` asserts `_normalize_email(" First-Admin@Example.com ")` returns `"first-admin@example.com"`.

---

### CHECK 7 — Migration Safety and Existing-User Role Defaults
**PASS**

The migration adds the `role` column with `server_default="user"`, ensuring all existing rows are backfilled safely. The `down_revision` chain is correct.

**Evidence:**

- `alembic/versions/d7e8f9a0b1c2_add_user_role.py` lines 23–30 (`upgrade`):
  ```python
  batch_op.add_column(
      sa.Column(
          "role",
          sa.String(length=32),
          nullable=False,
          server_default="user",   # ✓ safe default for all existing rows
      )
  )
  ```
- `server_default="user"` means SQLite/PostgreSQL will set `'user'` for every existing row at migration time, and for new rows that don't specify a role — no existing user gains elevated privileges.
- `down_revision = "c1a2b3d4e5f6"` (line 16): Verified by grep that `c1a2b3d4e5f6` is a real migration (`alembic/versions/c1a2b3d4e5f6_add_job_ingestion_engine.py`) with `revision = "c1a2b3d4e5f6"`. The chain is valid.
- `batch_alter_table` is used, which is the correct Alembic pattern for SQLite (SQLite does not support `ALTER COLUMN` natively; batch mode recreates the table).
- An index `ix_users_role` is created on the `role` column (line 31), consistent with `backend/models/user.py` line 57 (`index=True`).
- The `downgrade` function correctly drops the index before dropping the column (line 34–36), avoiding an orphaned index.

---

### CHECK 8 — Protection of Existing jobyn.db During Tests
**PASS**

Tests use a completely isolated temporary database, and the provisioning script's environment guard blocks any accidental test-environment call from touching `jobyn.db`.

**Evidence:**

- `conftest.py` lines 7–13:
  ```python
  _TEST_DIR = tempfile.mkdtemp(prefix="jobyn_test_")
  os.environ["ENVIRONMENT"] = "test"
  os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DIR}/test.db"
  ```
  This sets `DATABASE_URL` to a temp directory **before any backend imports occur**, so the SQLAlchemy engine created in `session.py` binds to the temp DB, not `jobyn.db`.
- `conftest.py` lines 46–51 (`db_session` fixture): Drops and recreates the schema on every test, ensuring full isolation.
- `provision_first_admin.py` line 76 (`_ensure_provisioning_is_allowed_for_runtime`): Even if a test somehow called `provision_first_admin()`, the `ENVIRONMENT=test` guard blocks execution before any DB access.
- `test_first_admin_provisioning.py` line 18 (`reset_test_environment` autouse fixture): Explicitly re-pins `ENVIRONMENT=test` for every test in the module via `monkeypatch.setenv`, providing an additional safety layer.

---

### CHECK 9 — Race Conditions and Other Security Weaknesses
**PARTIAL PASS — one moderate TOCTOU weakness, no critical issues**

#### 9a. TOCTOU on admin-existence check — MODERATE WEAKNESS
**Evidence:** `provision_first_admin.py` lines 124–142 perform a SELECT for existing admin, then INSERT. Both are inside `session.begin()` (a single transaction), but SQLite's default transaction isolation uses **deferred write locks**: two concurrent processes could both complete the SELECT before either acquires the write lock. If two provisioner instances ran simultaneously (extremely unlikely in practice for a one-time CLI), both might observe "no admin" and attempt insertion. The email's unique constraint (`ix_users_email`, unique index from migration `e77d77a9d923`) would cause one to fail with an `IntegrityError` only if they used the same email. If different emails were used, both could succeed, creating two admin accounts.

A partial unique index `WHERE role = 'admin'` at the database level would close this gap completely.

#### 9b. No unique constraint on `role='admin'` — MODERATE WEAKNESS
There is no database-level constraint preventing more than one row with `role='admin'`. The guard is application-level only (check 3). While this is adequate for the primary provisioning script, it does not protect against direct SQL manipulation or future code paths that bypass the guard.

#### 9c. Email uniqueness — PASS
The `users.email` column carries a unique index (`ix_users_email`, `unique=True`, confirmed in `alembic/versions/e77d77a9d923_create_users_table.py`). This prevents duplicate accounts at the database level regardless of application logic.

#### 9d. No password in error messages or audit trail — PASS
The exception handler at `provision_first_admin.py` line 190 only prints `exc` (a `RuntimeError` or `ValueError` message), never the password.

#### 9e. No SQL injection risk — PASS
All queries use SQLAlchemy ORM with parameterised binds (`select(User).where(User.role == "admin")`). No raw string interpolation into SQL.

#### 9f. `getpass` used for terminal input — PASS
`getpass.getpass()` at line 107 suppresses echo on all supported platforms.

#### 9g. `lru_cache` on `get_settings()` — LOW RISK NOTE
`backend/core/config.py` line 183 caches `Settings` with `@lru_cache`. In tests, `conftest.py` sets env vars before imports, so the cached settings object picks up `DATABASE_URL = test.db` on first call. However, if any test re-patches env vars after `get_settings()` has been called, the cache may return stale values. This is a general testing concern, not specific to provisioning, and is mitigated by `conftest.py`'s import-order discipline.

---

## Critical Issues Requiring Correction

**None that are blockers.** Two moderate weaknesses are worth fixing:

### Moderate Issue 1: No database-level uniqueness constraint on `role='admin'`

**File:** `alembic/versions/d7e8f9a0b1c2_add_user_role.py`, `backend/models/user.py`
**Risk:** Application-level TOCTOU means two simultaneous provisioner invocations could theoretically create two admin accounts (only if using different emails, given the email unique constraint). Direct SQL access bypasses the guard entirely.
**Recommended fix:** Add a partial unique index in a follow-up migration:
```sql
CREATE UNIQUE INDEX ix_users_one_admin ON users (role) WHERE role = 'admin' AND deleted_at IS NULL;
```
In Alembic:
```python
op.create_index(
    "ix_users_one_admin",
    "users",
    ["role"],
    unique=True,
    postgresql_where=text("role = 'admin' AND deleted_at IS NULL"),
)
```
Note: SQLite does not support partial indexes natively in all versions; this constraint is most valuable on the PostgreSQL production target.

### Moderate Issue 2: Plaintext password not explicitly deleted after hashing

**File:** `backend/scripts/provision_first_admin.py` lines 117, 154
**Risk:** The plaintext password string remains on the Python heap after `hash_password(password)` is called. This is a Python language limitation (immutable `str` objects cannot be zeroed). The risk is low in a local CLI context with no persistent process state.
**Recommended fix:** Reassign the variable immediately after hashing to drop the reference:
```python
hashed = hash_password(password)
del password  # drops the reference; GC will collect on next sweep
user = User(..., hashed_password=hashed, ...)
```
This does not guarantee immediate memory erasure (CPython GC is non-deterministic) but is best practice and signals intent.

---

## Conclusions

| Check | Result |
|---|---|
| 1. Actual database target validation | ✅ PASS |
| 2. Environment guard before database access | ✅ PASS |
| 3. Prevention of duplicate admins / existing-user promotion | ✅ PASS (moderate TOCTOU note) |
| 4. Correct admin role, active and verified status | ✅ PASS |
| 5. Secure password hashing and handling | ✅ PASS (minor memory note) |
| 6. Email validation and normalization | ✅ PASS |
| 7. Migration safety and existing-user role defaults | ✅ PASS |
| 8. Protection of jobyn.db during tests | ✅ PASS |
| 9. Race conditions and other security weaknesses | ⚠ PARTIAL PASS (2 moderate weaknesses) |

---

**Safe to migrate:** YES
The migration `d7e8f9a0b1c2` is safe. It uses `batch_alter_table` (correct for SQLite), applies `server_default="user"` to all existing rows, has a valid `down_revision`, and includes a correct `downgrade()` path.

**Safe to provision first administrator:** YES
All primary guards are in place: environment check, SQLite-only check, path restriction to `jobyn.db`, duplicate-admin check, existing-user non-promotion, strong password hashing, and email validation. The two moderate weaknesses do not block safe operation of the one-time CLI provisioning flow.

**Recommended next action:**
1. Run `python -m alembic upgrade head` to apply migration `d7e8f9a0b1c2`.
2. Run `python -m backend.scripts.provision_first_admin` to provision the first administrator.
3. In a follow-up PR, add a partial unique index on `role='admin'` (PostgreSQL) to close the TOCTOU gap at the database level.
4. Optionally add `del password` after hashing in `provision_first_admin.py` as a defence-in-depth measure.
