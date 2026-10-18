# AI 个性化饮食管理平台

V1.2 / P0.1 implementation.

See [CHANGELOG.md](CHANGELOG.md) for version highlights, verification results, and known boundaries.

## Current scope

The current implementation follows the frozen P0.1 loop:

```text
phone or WeChat login
-> onboarding
-> goal proposal and confirmation
-> manual meal record or explicit example draft
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

The model can call `get_today_context`, `search_food`, `search_online_nutrition`, `convert_food_portion`, `calculate_nutrition`, `propose_meal_record`, `preview_meal_plans`, `propose_memory`, and `get_recent_meals`. It cannot access the Store directly. Proposal tools stage `AIAction` objects only; the existing confirm endpoints remain the sole path for creating formal meal records or long-term memories.

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

### 个性化评估与本地 RAG

安装 `backend/requirements.txt` 后，服务会在 `DIET_CHROMA_PATH` 指定目录创建 Chroma 本地向量库；未设置时默认使用 `backend/data/chroma`。指南片段和用户饮食-体感记录使用不同集合，体感检索始终带当前用户 ID 过滤，不会把全部历史塞入模型上下文。

`POST /api/v1/ai/assess` 支持按单餐、指定日期或最近 7 天生成结构化评估；`GET /api/v1/trends/7-days` 返回 7 天趋势；`POST /api/v1/meals/{meal_id}/feedback` 补录餐后体感。大模型只生成固定 JSON，后端用 Pydantic 校验并把营养值处理为整数区间，前端不直接渲染模型自由文本。

长期记忆只存指南与已保存的饮食-体感记录；普通对话仅在审计轨迹中保存本轮摘要。正式记餐、长期偏好和目标修改继续需要用户确认。模型或 Chroma 暂时不可用时会安全降级，不影响本地 Demo 启动。

### 语音录入

AI 助手输入框支持浏览器原生 Web Speech API。点击“开始录音”后可连续口述饮食，点击“结束录音”只会停止识别并保留转写文本，不会自动发送；用户可以先修改识别错误，再走原有自然语言分析和动作确认链路。浏览器不支持、没有麦克风权限或识别失败时，文字输入仍可正常使用，不需要配置额外 ASR 密钥。

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

Foods now use a traceable source chain:

```text
local reviewed catalog and user-confirmed nutrition label
-> cached barcode result
-> Open Food Facts barcode API
-> Open Food Facts name/brand search
-> official brand/government/open-database page found by DeepSeek web search
-> explicit low-confidence estimate
```

The Agent always calls the local `search_food` tool first. When there is no exact
match, `search_online_nutrition` may use a barcode source or DeepSeek Responses
`web_search` to extract a structured candidate from a direct HTTPS evidence
page. Search result pages, local/private URLs, implausible values, and uncited
results are rejected. The candidate stays inside the current Agent run until a
user confirms the meal action; cancellation persists neither the candidate nor
the meal. Nutrition totals are recalculated by the backend from the sourced
per-100g values and confirmed weight.

Enable the source chain in the backend `.env`:

```env
DIET_ONLINE_FOOD_SEARCH=1
DIET_ONLINE_FOOD_MODEL=deepseek-v4-flash
DIET_ONLINE_FOOD_TIMEOUT_SECONDS=30
DIET_ONLINE_FOOD_MAX_OUTPUT_TOKENS=4000
```

`DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL` are shared with the DeepSeek Agent
configuration. The frontend receives source URLs and confidence labels but
never receives the API key. If web search is unavailable, barcode lookup and
the existing local/manual-label flows continue to work.

`GET /api/v1/foods/barcode/{barcode}` performs a read-only barcode lookup and caches successful results. `POST /api/v1/foods/label` stores a user-confirmed package label and gives it priority for that account without exposing it to other users. Food records retain barcode, brand, provider, provider version, source URL, observation time, user-verification state, and confidence. Total sugar and added sugar are separate nullable fields; missing label values must remain unknown rather than being saved as zero.

External barcode lookup uses Open Food Facts API v3. The backend sends an identifying user agent and defaults to a four-second timeout. Production deployments should set:

```powershell
$env:DIET_FOOD_SOURCE_USER_AGENT="your-app/version (contact@example.com)"
$env:DIET_FOOD_SOURCE_TIMEOUT="4"
```

Image search is intentionally not used as nutrition evidence: a product photo
does not reliably identify recipe, pack size, or label values. The current
version does not open a camera, upload an image, or claim to identify food from
a photograph. The Record page offers manual search as the primary workflow and
an explicitly labelled fixed example draft only to demonstrate editing,
confirmation, and deterministic nutrition calculation. A future photo flow
must read a user-provided package label or meal photo and still pass its result
through the same source, calculation, confidence, and confirmation boundaries.

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
- wire WeChat login APIs through providers; add image upload only together with a real, disclosed recognition capability;
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

Open `http://127.0.0.1:5174`. The frontend requests the same-origin `/api/v1`
path, and Vite proxies `/api` to `http://127.0.0.1:8000` during local
development. To use another local backend, copy `frontend/.env.example` to
`frontend/.env.local` and set `VITE_DEV_API_PROXY_TARGET`.

## Frontend deployment

The production default is also the same-origin `/api/v1`, which is recommended
when a reverse proxy serves the frontend and FastAPI under one HTTPS domain.
For a separate API domain, set this before building:

```powershell
cd frontend
$env:VITE_API_BASE_URL="https://api.example.com/api/v1"
npm.cmd run build
```

`VITE_API_BASE_URL` may be a relative path or an absolute HTTPS URL; trailing
slashes are normalized. Public deployments must use HTTPS for both frontend and
API to avoid mixed-content blocking. Do not place API keys in any `VITE_`
variable because Vite embeds those values in the browser bundle.

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

真实产品任务集位于 `backend/evals/product_scenarios.json`，覆盖口语记餐、模糊份量、餐后体感、偏好保存、计划摄入、营养问答和医疗安全边界。测试不会请求外部模型：

```powershell
cd backend
python -m pytest tests/test_product_eval.py -q -p no:cacheprovider
```

Read the product and implementation contracts in `docs/` before extending P0.
# 微信小程序导入入口

原生微信小程序前端已放在 [`miniprogram/`](./miniprogram/)。请用微信开发者工具直接导入该目录；导入前先修改 `miniprogram/utils/config.js` 的 HTTPS API 地址，并在 `miniprogram/project.config.json` 中填写真实 AppID。详细步骤见 [`miniprogram/README.md`](./miniprogram/README.md)。
