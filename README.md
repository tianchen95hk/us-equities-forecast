# US Equities Forward-Looking Forecast System

Production-minded US equities directional forecast pipeline (strictly forward-looking, no hindsight target-price logic).

Default market universe:
`SPY, QQQ, IWM, VIX, US10Y, DXY, OIL, BTC, USDJPY`

## What It Does
- Collects latest news + market indicators (live or manual/mock).
- Runs 3-call LLM chain:
  1. `event_extraction`
  2. `state_and_forecast` (state mapping + draft forecast)
  3. `anti_hindsight_review`
- Applies deterministic rule checks and publish gate.
- Separates publishability from analysis visibility (reject still keeps full analysis trace).
- Outputs readable CLI panel by default (`--output-format text`).

## Quick Start

### 1) Install
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

### 2) Configure `.env`
Minimum for live mode:
```env
USE_LIVE_DATA=true
STRICT_LIVE_MODE=true

LLM_PROVIDER=minimax
LLM_BASE_URL=https://api.minimax.io/v1
LLM_API_KEY=YOUR_MINIMAX_KEY
LLM_MODEL=MiniMax-M2.5

FMP_API_KEY=YOUR_FMP_KEY
NEWS_API_KEY=YOUR_NEWSAPI_KEY
```

Optional output defaults:
```env
OUTPUT_LANGUAGE=zh
OUTPUT_STYLE=telegram
```

## Run

### Local run (default readable text panel)
```bash
python3 -m app.main run --live --output-lang zh --output-style telegram --output-format text
```

### JSON mode (for scripts)
```bash
python3 -m app.main run --live --output-style telegram --output-format json
```

### Common options
```bash
--max-news-age-hours 72
--max-market-age-minutes 60
--disable-freshness-gate
--allow-mock-fallback
```

## OpenClaw / Server Helper Script
Example runner:
```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/.openclaw/apps/us-equities-forecast"
LLM_PROVIDER=minimax \
LLM_TIMEOUT_SECONDS=120 \
REQUEST_TIMEOUT_SECONDS=20 \
STRICT_LIVE_MODE=true \
COLLECT_IN_PARALLEL=true \
python3 -m app.main run --live --output-lang zh --output-style telegram --output-format text
```

## Production Verification
Use real APIs only:
```bash
bash scripts/prod_live_verify.sh
```

Notes:
- Script now forces CLI JSON mode internally (`--output-format json`) for robust parsing.
- Verifies MiniMax / NewsAPI / FMP connectivity + live smoke/regression runs.

## Output Modes
- `--output-format text` (default): readable panel in terminal.
- `--output-format json`: machine-readable structured payload.

Styles:
- `--output-style simple`
- `--output-style telegram`
- `--output-style full`

## API (Optional)
```bash
python3 -m app.main serve --host 127.0.0.1 --port 8000
```
Endpoints:
- `GET /health`
- `POST /run`
- `GET /forecast/latest`

## Artifacts
Per run:
- `artifacts/<run_id>/raw/*`
- `artifacts/<run_id>/intermediate/*`
- `artifacts/<run_id>/final/final_forecast.json` (always saved)
- `artifacts/<run_id>/final/review_rejected.json` (when rejected)
- `artifacts/<run_id>/final/analysis_trace.json` (always saved)

## Key Rules
- Strictly forward-looking inputs only.
- No target-price / hindsight-style thesis.
- Must include triggers, invalidation conditions, monitoring list.
- Publish gate requires review + rule checks.

## Tests
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Config Tip
If your `.env` is older, ensure these keys exist explicitly:
- `MARKET_UNIVERSE` (include `BTC`, `USDJPY`)
- `EARNINGS_PROXY_TOP_TICKERS`
- `EARNINGS_PROXY_LIVE_MAX_TICKERS`
- `FACTOR_WEIGHT_*`
