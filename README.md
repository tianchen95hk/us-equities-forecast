# US Equities Forward-Looking Forecast System

Minimal but production-minded single-repo Python app for directional forecasting on US equities context (`SPY`, `QQQ`, `IWM`, `VIX`, `US10Y`, `DXY`, `OIL`) with two hard auxiliary signals (`BTC`, `USDJPY`).

The system is explicitly designed to avoid hindsight bias and price-target logic.

## Architecture Highlights

- Collectors only collect raw inputs (manual/live/mock) and contain no forecasting logic.
- Pipeline stages each have one responsibility:
  - normalize inputs
  - collect earnings revision proxy (FMP)
  - compute deterministic five-factor snapshot and dominant factor
  - extract events
  - map states + generate draft forecast (single combined stage)
  - review for anti-hindsight (consumes rule report)
  - deterministic local repair + re-check rules
  - publish only reviewed forecast
- Prompt loading is centralized via `app/utils/prompt_loader.py`.
- Validation rules (`app/rules/*`) are independent from LLM implementation.
- Storage layer persists metadata/artifacts/forecasts and is prompt-agnostic.
- Input freshness gate (default enabled):
  - news <= 72 hours
  - market indicators <= 60 minutes
  - overridable per run via CLI/API.
- Latest-available fallback (default enabled):
  - if freshness gate is exceeded but data exists, pipeline can continue with latest available inputs
  - fallback still enforces a hard cap (`LATEST_AVAILABLE_MAX_*`) to prevent very old data.
- Deterministic five-factor engine:
  - `earnings_revision`, `volatility`, `rates`, `dollar`, `energy_geopolitics`
  - dominant factor uses `abs(weight*score)`, supports tie output.

### Strict 3-Call LLM Chain

1. `event_extraction`
2. `state_and_forecast` (state mapping + forecast draft in one call)
3. `anti_hindsight_review` (receives `draft_rule_report`)

No extra LLM repair call is used.

## Project Structure

- `app/main.py`
- `app/config.py`
- `app/exceptions.py`
- `app/llm_client.py`
- `app/schemas.py`
- `app/utils/prompt_loader.py`
- `app/collectors/news.py`
- `app/collectors/market_data.py`
- `app/collectors/earnings_revision.py`
- `app/pipeline/normalize.py`
- `app/pipeline/factors.py`
- `app/pipeline/extract_events.py`
- `app/pipeline/map_states.py`
- `app/pipeline/generate_forecast.py` (legacy compatibility helper; not used in strict 3-call orchestrator)
- `app/pipeline/review_forecast.py`
- `app/pipeline/publish_forecast.py`
- `app/pipeline/orchestrator.py`
- `app/rules/schema_check.py`
- `app/rules/anti_hindsight.py`
- `app/storage/db.py`
- `app/storage/models.py`
- `prompts/event_extraction.txt`
- `prompts/state_mapping.txt`
- `prompts/forecast.txt`
- `prompts/anti_hindsight_review.txt`
- `data/mock/news_latest.json`
- `data/mock/market_latest.json`
- `tests/test_rules.py`
- `tests/test_publish_forecast.py`
- `requirements.txt`
- `.env.example`

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run Once (Cron-Friendly CLI)

```bash
python -m app.main run
```

默认 CLI 输出为**中文可读文本面板**（不是 JSON）。
并且当 `OUTPUT_LANGUAGE=zh` 时，系统会要求模型将最终预测正文也输出为中文。

切换完整输出：

```bash
python -m app.main run --output-style full
```

切换 Telegram 展示结构（包含运行时间、采集时间、审查时间）：

```bash
python -m app.main run --output-style telegram
```

切换渲染格式（默认 `text`，脚本场景可用 `json`）：

```bash
python -m app.main run --output-format text
python -m app.main run --output-format json
```

切换英文简版：

```bash
python -m app.main run --output-lang en
```

Optional manual inputs:

```bash
python -m app.main run --news-file ./data/mock/news_latest.json --market-file ./data/mock/market_latest.json
```

Try live data first:

```bash
python -m app.main run --live
```

`STRICT_LIVE_MODE=true`（默认）时：
- 不允许 `LLM_PROVIDER=mock`
- live 采集失败且无 latest cache 时直接报错
- 不会回退到 `data/mock/*`
- 返回 `runtime_assertions`，可审计本次是否走了 mock/source fallback

Override freshness windows for one run:

```bash
python -m app.main run --max-news-age-hours 48 --max-market-age-minutes 30
```

Disable freshness hard gate for one run:

```bash
python -m app.main run --disable-freshness-gate
```

If you want strict rejection (disable latest-available fallback), set in `.env`:

```env
ALLOW_LATEST_AVAILABLE_FALLBACK=false
```

性能优化相关参数（适合真实 API）：

```env
COLLECT_IN_PARALLEL=true
LLM_MAX_TOKENS=1200
LLM_COMPACT_NEWS_ITEMS=8
LLM_COMPACT_TEXT_CHARS=320
LIVE_NEWS_PAGE_SIZE=12
```

说明：
- `COLLECT_IN_PARALLEL=true`：新闻和市场并发采集
- `LLM_COMPACT_*`：压缩传给模型的新闻上下文，降低延迟
- CLI `telegram` 输出会显示每个 LLM 阶段耗时，便于定位瓶颈

生产环境一键真实连通+回归验证（禁止 mock）：

```bash
bash scripts/prod_live_verify.sh
```

该脚本会执行：
- MiniMax / NewsAPI / FMP 连通性检查
- 1 次 `telegram` live smoke
- 1 次 `full` live 回归
- 严格断言 `runtime_assertions.all_passed=true`，且新闻/市场来源为 live

## Optional API

```bash
python -m app.main serve --host 127.0.0.1 --port 8000
```

Endpoints:
- `GET /health`
- `POST /run`
- `GET /forecast/latest`

## OpenClaw Quick Deploy

Clone/pull + install dependencies + run in one script (default: **no venv**):

```bash
bash scripts/openclaw_install_and_run.sh
```

Common overrides:

```bash
REPO_DIR=$HOME/us-equities-forecast \
PYTHON_BIN=python3.11 \
RUN_ARGS="--live --output-style simple --output-lang zh" \
bash scripts/openclaw_install_and_run.sh
```

Enable venv only when you explicitly want it:

```bash
USE_VENV=true bash scripts/openclaw_install_and_run.sh
```

If you want non-user global pip install in no-venv mode:

```bash
PIP_USER_INSTALL=false bash scripts/openclaw_install_and_run.sh
```

## Artifacts and Storage

Each run writes separated artifacts:

- `artifacts/<run_id>/raw/news_raw.json`
- `artifacts/<run_id>/raw/market_indicators_raw.json`
- `artifacts/<run_id>/intermediate/normalized_inputs.json`
- `artifacts/<run_id>/intermediate/input_freshness_report.json`
- `artifacts/<run_id>/intermediate/earnings_revision_proxy.json`
- `artifacts/<run_id>/intermediate/factor_state_snapshot.json`
- optional fallback marker: `artifacts/<run_id>/intermediate/input_latest_available_fallback.json`
- `artifacts/<run_id>/intermediate/structured_events.json`
- `artifacts/<run_id>/intermediate/state_mapping.json`
- `artifacts/<run_id>/intermediate/forecast_draft.json`
- `artifacts/<run_id>/intermediate/draft_rule_report.json`
- `artifacts/<run_id>/intermediate/anti_hindsight_review.json`
- `artifacts/<run_id>/intermediate/confidence_breakdown.json`
- `artifacts/<run_id>/intermediate/post_review_rule_report.json`
- `artifacts/<run_id>/intermediate/post_repair_rule_report.json`
- approved: `artifacts/<run_id>/final/final_forecast.json`
- rejected: `artifacts/<run_id>/final/review_rejected.json`
- rejected by stale inputs: `artifacts/<run_id>/final/input_rejected.json`
- both approved/rejected: `artifacts/<run_id>/final/analysis_trace.json`

`analysis_trace.json` now includes factor visibility fields:
- `factor_snapshot`
- `dominant_factor`
- `dominant_factor_explainer`
- `earnings_revision_proxy_summary`

SQLite tables:
- `runs`
- `artifacts`
- `forecasts`

## Governance Rules

`app/rules/schema_check.py` enforces:
- required forecast contract fields
- non-empty invalidation conditions
- non-empty and parseable `forecast_horizon`
- supportive/opposing evidence symmetry presence
- banned price-target phrase detection
- hindsight-style phrase detection

`app/rules/anti_hindsight.py` includes banned phrase patterns such as:
- `target price`
- `will reach`
- `break above`
- `fall to`
- `hit 7000`
- `目标价`
- `将到/会到`
- `突破/跌破`
- `触及xx点`
- `回看/事后看`

## Publish Gate

Publish is allowed only when both conditions hold:

- `anti_hindsight_status=PASS`
- `post_repair_rule_report.has_blocking_issues=false`

## Confidence Logic

Final confidence is deterministic and recalculated locally from observable consistency:

```text
0.40
+ 0.22 * scenario_alignment
+ 0.20 * event_consensus
+ 0.23 * cross_asset_confirmation
+ 0.12 * evidence_balance
- freshness_penalty
- risk_penalty
```

The computed score is clamped to `[0.35, 0.90]`, rounded to 2 decimals, and written to:

- `intermediate/confidence_breakdown.json`

If either fails:

- `runs.status=REVIEW_REJECTED`
- no row is written to `forecasts`
- output is written to `review_rejected.json`
- but analysis is still fully visible through:
  - `analysis_flow`
  - `analysis_variants` (draft/reviewed/repaired)
  - `publish_gate_report`
  - `analysis_trace.json`

## LLM Provider Switching

OpenAI-compatible abstraction is in `app/llm_client.py`.

Set in `.env`:
- `LLM_PROVIDER=mock` (default)
- `LLM_PROVIDER=openai` with `LLM_BASE_URL=https://api.openai.com/v1`
- `LLM_PROVIDER=kimi` with Kimi-compatible base URL
- `LLM_PROVIDER=minimax` with MiniMax OpenAI-compatible URL

### MiniMax Token Plan configuration

Based on MiniMax official OpenAI-compatible docs (`OPENAI_BASE_URL=https://api.minimax.io/v1`), use:

```env
LLM_PROVIDER=minimax
LLM_BASE_URL=https://api.minimax.io/v1
LLM_API_KEY=YOUR_MINIMAX_TOKEN_PLAN_KEY
LLM_MODEL=MiniMax-M2.5
LLM_TEMPERATURE=1.0
```

Notes:
- MiniMax OpenAI-compatible docs specify `temperature` range `(0.0, 1.0]`.
- The app validates this and fails fast on invalid config.

## Financial Market Data (FMP)

Live market indicators are now **FMP-first** with Yahoo fallback per missing instrument.
Raw market artifacts include `vendor_symbol` so you can audit which exact ticker/proxy was used.

Set in `.env`:

```env
USE_LIVE_DATA=true
FMP_API_KEY=YOUR_FMP_API_KEY
FMP_BASE_URL=https://financialmodelingprep.com/stable
```

FMP docs: https://site.financialmodelingprep.com/developer/docs

Current proxy fallback examples:
- `US10Y` may resolve to `IEF`
- `DXY` may resolve to `UUP`
- `OIL` may resolve to `USO`
- `BTC` may resolve to `BTCUSD` (or Yahoo `BTC-USD`)
- `USDJPY` may resolve to `USDJPY` (or Yahoo `JPY=X`)

## News API recommendation

Current collector implementation is already wired for **NewsAPI Everything endpoint**:
- endpoint: `https://newsapi.org/v2/everything`
- required key: `NEWS_API_KEY`
- docs: https://newsapi.org/docs/endpoints/everything

Set in `.env`:

```env
USE_LIVE_DATA=true
NEWS_API_KEY=YOUR_NEWSAPI_KEY
NEWS_API_URL=https://newsapi.org/v2/everything
```

(Alternative you may consider later: GNews `https://gnews.io/api/v4/...`, but that requires collector changes.)

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

## Future Upgrades

- Replace SQLite with PostgreSQL by changing `DATABASE_URL`.
- Add richer state features and scenario calibration.
- Add CI workflow for stage-level regression checks.
