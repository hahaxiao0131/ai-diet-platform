# AI 个性化饮食管理平台

V1.1 / P0.1 implementation.

## Current scope

The current implementation follows the frozen P0.1 loop:

```text
mock login
-> onboarding
-> goal proposal and confirmation
-> mock meal recognition draft
-> user confirmation
-> deterministic nutrition calculation
-> meal score and RiskEngine
-> today summary
-> next-meal What-if preview
-> controlled AI explanation and natural-language meal proposal
-> user confirmation before AI-created records are persisted
```

The V1 assistant currently uses `MockAssistantProvider` for deterministic intent and food parsing. It supports data explanation, natural-language meal proposals, next-meal planning, and food replacement. A real model can replace the provider later without moving nutrition, scoring, risk, or write authority out of the backend.

The local V1 backend persists user data to `backend/data/local_store.json` using atomic replacement and a previous-version backup. The file is ignored by Git. Set `DIET_LOCAL_STORE_PATH` to override the location or `DIET_DISABLE_PERSISTENCE=1` for isolated tests.

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
