#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

read_env_default() {
  local key="$1"
  "$PYTHON_BIN" - "$key" <<'PY'
from pathlib import Path
import sys
try:
    from dotenv import dotenv_values
except Exception:
    print("")
    raise SystemExit(0)

key = sys.argv[1]
env_path = Path(".env")
if not env_path.exists():
    print("")
    raise SystemExit(0)
values = dotenv_values(env_path)
print(values.get(key) or "")
PY
}

require_env() {
  local key="$1"
  if [ -z "${!key:-}" ]; then
    echo "[ERROR] Missing required environment variable: $key"
    exit 1
  fi
}

mask_key() {
  local value="${1:-}"
  local length="${#value}"
  if [ "$length" -le 8 ]; then
    echo "****"
    return
  fi
  local prefix="${value:0:4}"
  local suffix="${value:length-4:4}"
  echo "${prefix}****${suffix}"
}

LLM_API_KEY="${LLM_API_KEY:-$(read_env_default LLM_API_KEY)}"
NEWS_API_KEY="${NEWS_API_KEY:-$(read_env_default NEWS_API_KEY)}"
FMP_API_KEY="${FMP_API_KEY:-$(read_env_default FMP_API_KEY)}"
LLM_BASE_URL="${LLM_BASE_URL:-$(read_env_default LLM_BASE_URL)}"
LLM_MODEL="${LLM_MODEL:-$(read_env_default LLM_MODEL)}"
NEWS_API_URL="${NEWS_API_URL:-$(read_env_default NEWS_API_URL)}"
FMP_BASE_URL="${FMP_BASE_URL:-$(read_env_default FMP_BASE_URL)}"

LLM_BASE_URL="${LLM_BASE_URL:-https://api.minimax.io/v1}"
LLM_MODEL="${LLM_MODEL:-MiniMax-M2.5}"
NEWS_API_URL="${NEWS_API_URL:-https://newsapi.org/v2/everything}"
FMP_BASE_URL="${FMP_BASE_URL:-https://financialmodelingprep.com/stable}"

require_env "LLM_API_KEY"
require_env "NEWS_API_KEY"
require_env "FMP_API_KEY"

echo "[INFO] Using production keys (masked):"
echo "  - LLM_API_KEY=$(mask_key "$LLM_API_KEY")"
echo "  - NEWS_API_KEY=$(mask_key "$NEWS_API_KEY")"
echo "  - FMP_API_KEY=$(mask_key "$FMP_API_KEY")"

echo "[STEP] Connectivity check: MiniMax"
MINIMAX_TMP="$(mktemp)"
MINIMAX_STATUS="$(curl -sS -o "$MINIMAX_TMP" -w "%{http_code}" \
  -H "Authorization: Bearer ${LLM_API_KEY}" \
  -H "Content-Type: application/json" \
  "${LLM_BASE_URL%/}/chat/completions" \
  -d "{
    \"model\": \"${LLM_MODEL}\",
    \"temperature\": 1.0,
    \"max_tokens\": 64,
    \"response_format\": {\"type\": \"json_object\"},
    \"messages\": [
      {\"role\": \"system\", \"content\": \"Return JSON only\"},
      {\"role\": \"user\", \"content\": \"{\\\"ping\\\":\\\"ok\\\"}\"}
    ]
  }")"
if [ "$MINIMAX_STATUS" != "200" ]; then
  echo "[ERROR] MiniMax connectivity failed (HTTP ${MINIMAX_STATUS})"
  sed -n '1,12p' "$MINIMAX_TMP"
  rm -f "$MINIMAX_TMP"
  exit 1
fi
"$PYTHON_BIN" - "$MINIMAX_TMP" <<'PY'
import json, sys
payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
choices = payload.get("choices", [])
if not choices:
    raise SystemExit("MiniMax response has no choices")
print("[OK] MiniMax responded with choices:", len(choices))
PY
rm -f "$MINIMAX_TMP"

echo "[STEP] Connectivity check: NewsAPI"
NEWS_FROM="$("$PYTHON_BIN" - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(hours=72)).isoformat().replace("+00:00", "Z"))
PY
)"
NEWS_TMP="$(mktemp)"
NEWS_STATUS="$(curl -sS -o "$NEWS_TMP" -w "%{http_code}" \
  -G "${NEWS_API_URL}" \
  --data-urlencode 'q="S&P 500" OR "Nasdaq 100" OR "Federal Reserve"' \
  --data-urlencode "from=${NEWS_FROM}" \
  --data-urlencode 'language=en' \
  --data-urlencode 'sortBy=publishedAt' \
  --data-urlencode 'pageSize=5' \
  --data-urlencode "apiKey=${NEWS_API_KEY}")"
if [ "$NEWS_STATUS" != "200" ]; then
  echo "[ERROR] NewsAPI connectivity failed (HTTP ${NEWS_STATUS})"
  sed -n '1,12p' "$NEWS_TMP"
  rm -f "$NEWS_TMP"
  exit 1
fi
"$PYTHON_BIN" - "$NEWS_TMP" <<'PY'
import json, sys
payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
articles = payload.get("articles") or []
if not isinstance(articles, list):
    raise SystemExit("NewsAPI response missing articles list")
print("[OK] NewsAPI articles:", len(articles))
PY
rm -f "$NEWS_TMP"

echo "[STEP] Connectivity check: FMP"
FMP_TMP="$(mktemp)"
FMP_STATUS="$(curl -sS -o "$FMP_TMP" -w "%{http_code}" \
  -G "${FMP_BASE_URL%/}/quote" \
  --data-urlencode 'symbol=SPY' \
  --data-urlencode "apikey=${FMP_API_KEY}")"
if [ "$FMP_STATUS" != "200" ]; then
  echo "[ERROR] FMP connectivity failed (HTTP ${FMP_STATUS})"
  sed -n '1,12p' "$FMP_TMP"
  rm -f "$FMP_TMP"
  exit 1
fi
"$PYTHON_BIN" - "$FMP_TMP" <<'PY'
import json, sys
payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
if not isinstance(payload, list) or not payload:
    raise SystemExit("FMP quote response empty")
print("[OK] FMP quote rows:", len(payload))
PY
rm -f "$FMP_TMP"

run_and_assert() {
  local style="$1"
  local output_file
  output_file="$(mktemp)"

  STRICT_LIVE_MODE=true \
  USE_LIVE_DATA=true \
  LLM_PROVIDER=minimax \
  "$PYTHON_BIN" -m app.main run --live --output-lang zh --output-style "$style" > "$output_file"

  "$PYTHON_BIN" - "$style" "$output_file" <<'PY'
import json, sys
style = sys.argv[1]
path = sys.argv[2]
payload = json.loads(open(path, "r", encoding="utf-8").read())

if style == "telegram":
    conclusion = payload.get("结论", {})
    run_id = conclusion.get("运行ID") or payload.get("运行信息", {}).get("运行ID")
    publish_status = conclusion.get("发布状态") or payload.get("发布状态")
    runtime = payload.get("运行断言", {})
else:
    run_id = payload.get("run_id")
    publish_status = payload.get("publish_status")
    runtime = payload.get("runtime_assertions", {})

all_passed = bool(runtime.get("全部通过") if isinstance(runtime, dict) and "全部通过" in runtime else runtime.get("all_passed"))
news_source = runtime.get("新闻来源") if isinstance(runtime, dict) and "新闻来源" in runtime else runtime.get("news_source")
market_source = runtime.get("市场来源") if isinstance(runtime, dict) and "市场来源" in runtime else runtime.get("market_source")

if not all_passed:
    raise SystemExit("runtime_assertions did not pass")
if not isinstance(news_source, str) or not news_source.startswith("live"):
    raise SystemExit(f"news_source is not live*: {news_source}")
if market_source not in {"live_fmp", "live_fmp+yahoo"}:
    raise SystemExit(f"market_source is not live_fmp/live_fmp+yahoo: {market_source}")

print(f"[OK] {style} run | run_id={run_id} | publish_status={publish_status} | news={news_source} | market={market_source}")
PY
  rm -f "$output_file"
}

echo "[STEP] Live smoke run (telegram)"
run_and_assert "telegram"

echo "[STEP] Live regression run (full)"
run_and_assert "full"

echo "[DONE] Production live verification passed (no mock path)."
