# AI 个性化饮食管理平台

V1.2 / P0.1 implementation.

See [CHANGELOG.md](CHANGELOG.md) for version highlights, verification results, and known boundaries.

## Current scope

The current implementation follows the frozen P0.1 loop:

```text
phone or WeChat login
-> onboarding
-> goal proposal and confirmation
-> mock meal recognition draft
-> user confirmation
-> deterministic nutrition calculation
-> meal score and RiskEngine
-> today summary
-> next-meal What-if preview
-> context-aware AI explanation and natural-language meal proposal
-> clarification before low-confidence portions become actions
-> user confirmation before AI-created records or preferences are persisted
-> traceable Agent decisions, controllable memory, and answer feedback
```

The assistant now uses a hybrid single-agent runtime backed by deterministic business tools. `OpenAIAssistantProvider` first produces a strict `IntentInterpretation` with speech act, temporal status, modality, foods, and action intent. The backend then applies a deterministic action guard before `OpenAIAgentProvider` can select tools. Both providers use the official OpenAI Python SDK, Responses API, and native Pydantic Structured Outputs. `RuleBasedAssistantProvider` remains the local, test, and failure fallback. Nutrition, portions, food IDs, consent, safety guards, and write authority remain in the backend rather than the language model.

The Agent follows a controlled loop:

```text
understand the user's final goal
-> parse speech act, time state, modality, and food entities
-> reject write intent for questions, plans, wishes, or hypotheticals
-> select identity-bound deterministic tools
-> inspect tool results and continue or clarify
-> return an explanation, suggestion, or pending action
-> wait for explicit confirmation before formal writes
-> execute through existing backend confirmation services
-> persist an auditable trace and accept user feedback
```

The model can call `get_today_context`, `search_food`, `convert_food_portion`, `calculate_nutrition`, `propose_meal_record`, `preview_meal_plans`, `propose_memory`, and `get_recent_meals`. It cannot access the Store directly. Proposal tools stage `AIAction` objects only; the existing confirm endpoints remain the sole path for creating formal meal records or long-term memories.

## AI provider configuration

The default configuration is rule mode and needs no external credentials. To enable the OpenAI single-agent runtime, create a local `.env` from `.env.example` and set:

```dotenv
DIET_AI_PROVIDER=openai
OPENAI_API_KEY=your-server-side-key
OPENAI_BASE_URL=
OPENAI_MODEL=your-enabled-model
OPENAI_REASONING_EFFORT=low
DIET_AI_TIMEOUT_SECONDS=20
DIET_AI_MAX_RETRIES=1
DIET_AI_MAX_TOOL_TURNS=6
```

`OPENAI_BASE_URL` defaults to the official OpenAI endpoint when empty. For a
Responses API-compatible relay, set it to the provider's documented base URL
and use a model name supported by that relay. Relay latency can be higher than
the official endpoint, so increase `DIET_AI_TIMEOUT_SECONDS` when required. API
credentials remain backend-only.

The relay must support Responses API Structured Outputs and strict function
calling, not only plain text generation. If either capability is unavailable,
the backend keeps the existing rule provider as a safe fallback.

For the official DeepSeek Chat Completions API, use:

```env
DIET_AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-server-side-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/beta
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING=disabled
```

The DeepSeek provider uses function calls for structured intent, deterministic
business tools, and the final response. Tool arguments are always validated by
the backend before execution, and proposal tools still require user confirmation.

`.env` files are ignored by Git. The key is read by FastAPI only and is never returned to the frontend. Missing credentials, timeout, rate limit, authentication failure, malformed output, or a repeated tool loop falls back to the deterministic rule provider without executing a formal write.

## Database persistence

The application now supports PostgreSQL through SQLAlchemy while retaining the
JSON adapter for local fallback and isolated tests. Existing API handlers keep
the same Store contract. User identities, sessions, profiles, foods, goals,
meals, plans, AI actions, memories, traces, feedback, and weights are persisted
in ownership-aware tables. Flexible nutrition and audit snapshots use JSONB on
PostgreSQL; user ownership, status, timestamps, and lookup fields remain indexed
relational columns.

For local JSON mode, no changes are required:

```env
DIET_STORAGE_BACKEND=json
DIET_LOCAL_STORE_PATH=
```

For PostgreSQL, create an empty database and configure:

```env
DIET_STORAGE_BACKEND=postgresql
DATABASE_URL=postgresql+psycopg://diet_user:strong-password@127.0.0.1:5432/diet_ai
DIET_DB_AUTO_CREATE=0
```

Run the schema migration from the repository root:

```powershell
python -m alembic -c backend/alembic.ini upgrade head
```

To import the existing ignored JSON data after backing it up:

```powershell
$env:PYTHONPATH="backend"
python backend/scripts/migrate_json_to_db.py
```

The importer refuses a non-empty business database unless
`--allow-nonempty` is supplied explicitly. It also remaps historical meal,
draft, and plan references to the new deterministic seed-food IDs. Database
startup failures are not silently redirected to JSON, preventing production
data from splitting across two stores.

`DIET_DB_AUTO_CREATE=1` is convenient for local development and tests. Use
Alembic with `DIET_DB_AUTO_CREATE=0` in deployed environments. The current
compatibility adapter targets a single FastAPI application instance; horizontal
multi-instance deployment should first move remaining in-memory reads to scoped
repository queries and add database-level action claiming.

## Nutrition data sources

Packaged foods now use a traceable source chain:

```text
user-confirmed nutrition label
-> cached barcode result
-> Open Food Facts barcode API
-> local reviewed catalog
-> explicit low-confidence estimate
```

`GET /api/v1/foods/barcode/{barcode}` performs a read-only barcode lookup and caches successful results. `POST /api/v1/foods/label` stores a user-confirmed package label and gives it priority for that account without exposing it to other users. Food records retain barcode, brand, provider, provider version, source URL, observation time, user-verification state, and confidence. Total sugar and added sugar are separate nullable fields; missing label values must remain unknown rather than being saved as zero.

External barcode lookup uses Open Food Facts API v3. The backend sends an identifying user agent and defaults to a four-second timeout. Production deployments should set:

```powershell
$env:DIET_FOOD_SOURCE_USER_AGENT="your-app/version (contact@example.com)"
$env:DIET_FOOD_SOURCE_TIMEOUT="4"
```

## Authentication

The web preview now has a real login gate with Bearer sessions:

- WeChat authorization: the Mini Program client should pass the short-lived code from `wx.login`; the backend exchanges it through WeChat `code2Session` and maps the resulting identity to an internal user ID.
- Phone login: requests a six-digit verification code, then exchanges phone + code for a 30-day session.
- Sessions are stored as SHA-256 token hashes. Phone numbers and WeChat openids are also represented by one-way identity hashes in the local store.

Local development defaults to `DIET_AUTH_DEV_MODE=1`. In this mode the phone-code endpoint returns `dev_code`, and browser WeChat login uses a deterministic development authorization code. Before production, set:

```powershell
$env:DIET_AUTH_DEV_MODE="0"
$env:WECHAT_APP_ID="your-mini-program-appid"
$env:WECHAT_APP_SECRET="your-mini-program-secret"
$env:DIET_IDENTITY_SECRET="a-long-random-production-secret"
```

Production phone login still requires an SMS provider adapter. The current endpoint intentionally does not pretend to send a real SMS when development mode is disabled.

This local persistence adapter keeps the repository boundary separate. PostgreSQL remains the production target and can replace the adapter without changing the API, nutrition, scoring, or risk modules.

## WeChat Mini Program status

The current frontend is a React + Vite web preview with responsive mobile layout. It is not yet a WeChat Mini Program build.

Before shipping to WeChat, the frontend still needs a Taro migration pass:

- replace browser-only assumptions with Taro page/components;
- wire WeChat login and upload APIs through providers;
- validate safe-area, bottom tab, and small-screen layout on real Mini Program devices;
- keep nutrition, scoring, and risk calculation on the FastAPI backend.

## Run backend

```powershell
$env:PYTHONPATH="backend"
python -m uvicorn app.main:app --reload --port 8000
```

## Run frontend

```powershell
cd frontend
npm.cmd run dev
```

Open `http://127.0.0.1:5174`.

If Vite cannot start because of local `esbuild` permission restrictions, build once and serve the static output:

```powershell
cd frontend
npm.cmd run build
cd dist
python -m http.server 5174 --bind 127.0.0.1
```

## Test backend

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests -q
```

Read the product and implementation contracts in `docs/` before extending P0.
