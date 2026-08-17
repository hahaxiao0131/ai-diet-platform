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

The V1.2 assistant currently uses `MockAssistantProvider` for deterministic intent and food parsing. It supports food calorie and macro lookup, portion conversion, food comparison, common diet knowledge, data explanation, natural-language meal proposals, next-meal planning, food replacement, explicit preference memory, confidence-aware clarification, safety boundaries, and per-answer feedback. Confirmed avoidance memories are applied to generated meal plans. A real model can replace the provider later without moving nutrition, scoring, risk, memory consent, or write authority out of the backend.

The Agent follows a controlled loop:

```text
observe current records and confirmed preferences
-> assess intent and data confidence
-> clarify missing portions when needed
-> propose an action with assumptions and evidence
-> wait for explicit confirmation
-> execute through deterministic backend services
-> persist a trace and accept user feedback
```

The local V1 backend persists user data to `backend/data/local_store.json` using atomic replacement and a previous-version backup. The file is ignored by Git. Set `DIET_LOCAL_STORE_PATH` to override the location or `DIET_DISABLE_PERSISTENCE=1` for isolated tests.

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
