"""LLM client abstraction with OpenAI-compatible and deterministic mock providers."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.schemas import AntiHindsightStatus, DirectionalBias


class LLMResponseError(RuntimeError):
    """Raised when model response is malformed or structurally invalid."""


class BaseLLMClient(ABC):
    """Interface for strict-JSON prompt completion."""

    @abstractmethod
    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate JSON for a named task and payload."""


class OpenAICompatibleLLMClient(BaseLLMClient):
    """Client for OpenAI-compatible `/chat/completions` APIs."""

    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise ValueError("LLM_API_KEY is required for non-mock providers")
        self._settings = settings
        base_url = settings.llm_base_url.rstrip("/")
        self._endpoint = (
            f"{base_url}/chat/completions"
            if not base_url.endswith("/chat/completions")
            else base_url
        )
        self._validate_provider_constraints()

    def _validate_provider_constraints(self) -> None:
        provider = self._settings.llm_provider

        if provider == "minimax":
            if "api.minimax.io" not in self._endpoint:
                raise ValueError(
                    "For LLM_PROVIDER=minimax, set LLM_BASE_URL to MiniMax OpenAI-compatible endpoint, "
                    "for example https://api.minimax.io/v1"
                )

            # MiniMax OpenAI-compatible docs specify temperature range (0.0, 1.0].
            if not (0.0 < self._settings.llm_temperature <= 1.0):
                raise ValueError(
                    "For LLM_PROVIDER=minimax, LLM_TEMPERATURE must be in (0.0, 1.0]. "
                    "Use 1.0 unless you intentionally tune it."
                )

    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        base_max_tokens = int(self._settings.llm_max_tokens) if self._settings.llm_max_tokens > 0 else 0
        token_candidates: list[int] = [base_max_tokens] if base_max_tokens > 0 else [0]
        if base_max_tokens > 0:
            token_candidates.append(min(4096, max(base_max_tokens + 300, int(base_max_tokens * 1.8))))

        last_exc: Exception | None = None
        for candidate_max_tokens in token_candidates:
            request_payload = {
                "model": self._settings.llm_model,
                "temperature": self._settings.llm_temperature,
                "seed": 42,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"task_name": task_name, "payload": payload},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
            }
            if candidate_max_tokens > 0:
                request_payload["max_tokens"] = candidate_max_tokens
            if self._settings.llm_provider == "minimax":
                # MiniMax OpenAI-compatible API supports reasoning_split to keep
                # reasoning content out of message.content and preserve clean JSON output.
                request_payload["reasoning_split"] = True

            headers = {
                "Authorization": f"Bearer {self._settings.llm_api_key}",
                "Content-Type": "application/json",
            }

            try:
                timeout = httpx.Timeout(
                    connect=min(12.0, self._settings.llm_timeout_seconds),
                    read=self._settings.llm_timeout_seconds,
                    write=min(12.0, self._settings.llm_timeout_seconds),
                    pool=min(12.0, self._settings.llm_timeout_seconds),
                )
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(self._endpoint, headers=headers, json=request_payload)
                    response.raise_for_status()
                    body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = LLMResponseError(f"LLM request failed for task `{task_name}`: {exc}")
                continue

            try:
                content = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                last_exc = LLMResponseError(
                    f"Model response missing choices/message/content for task `{task_name}`"
                )
                continue

            try:
                return _parse_json_or_raise(content=content, task_name=task_name)
            except LLMResponseError as exc:
                last_exc = exc
                continue

        if last_exc is None:
            raise LLMResponseError(f"LLM request failed for task `{task_name}` with unknown error")
        raise last_exc


class MockLLMClient(BaseLLMClient):
    """Deterministic mock LLM backend used for local and CI execution."""

    NEGATIVE_HINTS = {
        "inflation",
        "hawkish",
        "tariff",
        "war",
        "selloff",
        "drop",
        "slowdown",
        "tightening",
        "higher yields",
        "recession",
    }
    POSITIVE_HINTS = {
        "cooling",
        "cut",
        "stimulus",
        "beat",
        "easing",
        "rebound",
        "improve",
        "decline in yields",
        "disinflation",
        "supportive",
    }

    _BANNED_PATTERNS = (
        re.compile(r"\btarget\s+price\b", re.IGNORECASE),
        re.compile(r"\bwill\s+reach\b", re.IGNORECASE),
        re.compile(r"\bbreak\s+above\b", re.IGNORECASE),
        re.compile(r"\bfall\s+to\b", re.IGNORECASE),
        re.compile(r"\bhit\s+\$?\d{3,6}(?:\.\d+)?\b", re.IGNORECASE),
        re.compile(r"目标价"),
        re.compile(r"突破\s*\d+(?:\.\d+)?"),
        re.compile(r"跌破\s*\d+(?:\.\d+)?"),
    )

    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        del system_prompt
        now = datetime.now(timezone.utc).isoformat()

        if task_name == "event_extraction":
            return self._mock_event_extraction(now, payload)

        if task_name == "state_and_forecast":
            return self._mock_state_and_forecast(now, payload)

        if task_name == "anti_hindsight_review":
            return self._mock_anti_hindsight_review(now, payload)

        # Backward-compatible tasks kept for safety.
        if task_name == "state_mapping":
            combined = self._mock_state_and_forecast(now, payload)
            return combined["state_mapping"]
        if task_name == "forecast_generation":
            combined = self._mock_state_and_forecast(now, payload)
            return combined["forecast_draft"]

        raise ValueError(f"Unsupported mock task: {task_name}")

    def _mock_event_extraction(self, now: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_inputs = payload["normalized_inputs"]
        events: list[dict[str, Any]] = []
        for idx, news_item in enumerate(normalized_inputs.get("news", [])[:5], start=1):
            headline = str(news_item.get("headline", ""))
            headline_lower = headline.lower()
            impact_bias = "neutral"
            if any(token in headline_lower for token in self.NEGATIVE_HINTS):
                impact_bias = "down"
            elif any(token in headline_lower for token in self.POSITIVE_HINTS):
                impact_bias = "up"

            category = "macro"
            if any(token in headline_lower for token in ("oil", "middle east", "opec")):
                category = "geopolitics"
            elif any(token in headline_lower for token in ("earnings", "guidance", "profit")):
                category = "earnings"
            elif any(token in headline_lower for token in ("vix", "volatility", "flow", "breadth")):
                category = "market"

            pathway = (
                "Higher macro uncertainty can compress valuation multiples."
                if impact_bias == "down"
                else "Easing pressure can support risk appetite and multiples."
                if impact_bias == "up"
                else "Signal is mixed and mostly affects risk appetite at the margin."
            )

            events.append(
                {
                    "event_id": f"E{idx}",
                    "category": category,
                    "description": headline,
                    "impact_bias": impact_bias,
                    "impact_pathway": pathway,
                    "confidence": 0.66 if impact_bias != "neutral" else 0.58,
                    "evidence_refs": [headline],
                }
            )

        if not events:
            events.append(
                {
                    "event_id": "E1",
                    "category": "market",
                    "description": "No fresh headlines provided; rely on current market state.",
                    "impact_bias": "neutral",
                    "impact_pathway": "Without fresh catalysts, cross-asset conditions dominate near-term direction.",
                    "confidence": 0.55,
                    "evidence_refs": ["market indicators"],
                }
            )

        return {
            "generated_at": now,
            "summary": "Events extracted from currently observable headlines only.",
            "events": events,
        }

    def _mock_state_and_forecast(self, now: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_inputs = payload["normalized_inputs"]
        structured_events = payload["structured_events"]
        output_language = str(payload.get("output_language", "en")).lower()
        use_zh = output_language == "zh"

        indicator_map = {item["symbol"]: item for item in normalized_inputs.get("indicators", [])}

        vix = float(indicator_map.get("VIX", {}).get("value", 18.0))
        us10y_change = float(indicator_map.get("US10Y", {}).get("change_pct", 0.0) or 0.0)
        dxy_change = float(indicator_map.get("DXY", {}).get("change_pct", 0.0) or 0.0)
        spy_change = float(indicator_map.get("SPY", {}).get("change_pct", 0.0) or 0.0)
        qqq_change = float(indicator_map.get("QQQ", {}).get("change_pct", 0.0) or 0.0)
        iwm_change = float(indicator_map.get("IWM", {}).get("change_pct", 0.0) or 0.0)

        risk_avg_change = (spy_change + qqq_change + iwm_change) / 3.0
        volatility_state = "elevated" if vix >= 22 else "contained"
        growth_state = "softening" if risk_avg_change < -0.35 else "stable"
        inflation_state = "sticky" if us10y_change > 0.25 else "moderating"
        liquidity_state = "tightening" if (us10y_change > 0.2 or dxy_change > 0.2) else "neutral"

        base_direction = (
            DirectionalBias.BEARISH
            if volatility_state == "elevated" or liquidity_state == "tightening"
            else DirectionalBias.BULLISH
            if risk_avg_change > 0.25
            else DirectionalBias.NEUTRAL
        )

        state_mapping = {
            "generated_at": now,
            "regime_label": f"{growth_state}-growth/{inflation_state}-inflation/{liquidity_state}-liquidity",
            "growth_state": growth_state,
            "inflation_state": inflation_state,
            "liquidity_state": liquidity_state,
            "volatility_state": volatility_state,
            "cross_asset_signals": [
                f"VIX at {vix:.2f} implies {volatility_state} risk premium.",
                f"US10Y daily change {us10y_change:.2f}% signals {liquidity_state} financial conditions.",
                f"DXY daily change {dxy_change:.2f}% reflects dollar pressure on risk assets.",
            ],
            "scenarios": [
                {
                    "name": "Base case: range trade",
                    "probability": 0.55,
                    "directional_implication": base_direction.value,
                    "key_conditions": [
                        "No macro shock in the next sessions",
                        "Volatility does not enter sustained higher regime",
                    ],
                },
                {
                    "name": "Risk-off extension",
                    "probability": 0.25,
                    "directional_implication": DirectionalBias.BEARISH.value,
                    "key_conditions": [
                        "Yields and dollar rise together",
                        "Volatility persists at elevated levels",
                    ],
                },
                {
                    "name": "Relief recovery",
                    "probability": 0.20,
                    "directional_implication": DirectionalBias.BULLISH.value,
                    "key_conditions": [
                        "Yields stabilize and volatility cools",
                        "Breadth improves beyond mega-cap concentration",
                    ],
                },
            ],
            "narrative": "State mapping uses currently observable cross-asset and event conditions without hindsight framing.",
        }

        scenario_bias = state_mapping.get("scenarios", [{}])[0].get(
            "directional_implication", DirectionalBias.NEUTRAL.value
        )
        event_bias_score = 0
        for event in structured_events.get("events", []):
            impact_bias = event.get("impact_bias")
            if impact_bias == "up":
                event_bias_score += 1
            elif impact_bias == "down":
                event_bias_score -= 1

        if event_bias_score <= -2:
            directional_bias = DirectionalBias.BEARISH.value
        elif event_bias_score >= 2:
            directional_bias = DirectionalBias.BULLISH.value
        else:
            directional_bias = scenario_bias

        confidence = 0.64 if directional_bias != DirectionalBias.NEUTRAL.value else 0.58

        if use_zh:
            forecast_draft = {
                "generated_at": now,
                "forecast_horizon": normalized_inputs.get("forecast_horizon", "5 trading days"),
                "market_universe": normalized_inputs.get("market_universe", []),
                "directional_bias": directional_bias,
                "confidence": confidence,
                "dominant_drivers": [
                    f"市场状态：{state_mapping.get('regime_label', 'unknown')}",
                    "波动率、利率与美元的跨资产共振",
                    "最新可观测新闻事件的方向脉冲",
                ],
                "supportive_evidence": [
                    "状态映射基于当前可观测的跨资产与事件条件，不使用后验信息。",
                    "VIX 维持在相对可控区间。",
                    "风险资产整体仍具备韧性。",
                ],
                "opposing_evidence": [
                    "事件信号并非单边，短期可能快速切换。",
                    "宏观意外可能逆转当前跨资产确认。",
                ],
                "upside_triggers": [
                    "波动率继续回落并维持低位",
                    "利率企稳且市场广度改善",
                ],
                "downside_triggers": [
                    "波动率上行并伴随美元走强",
                    "利率再次抬升且中小盘走弱",
                ],
                "invalidation_conditions": [
                    "跨资产信号与当前方向判断出现系统性反转",
                    "新增事件流与当前主假设明显相反",
                ],
                "monitoring_list": [
                    "VIX 状态是否持续",
                    "US10Y 日度方向",
                    "DXY 日度方向",
                    "油价冲击相关事件",
                    "SPY/QQQ/IWM 市场广度",
                ],
                "final_thesis": "方向判断基于当前可观测状态与催化剂；若跨资产确认减弱，应下调置信度并及时收敛风险暴露。",
            }
        else:
            forecast_draft = {
                "generated_at": now,
                "forecast_horizon": normalized_inputs.get("forecast_horizon", "5 trading days"),
                "market_universe": normalized_inputs.get("market_universe", []),
                "directional_bias": directional_bias,
                "confidence": confidence,
                "dominant_drivers": [
                    f"Regime: {state_mapping.get('regime_label', 'unknown')}",
                    "Cross-asset confirmation across volatility, rates, and dollar",
                    "Current-event impulse from latest observable headlines",
                ],
                "supportive_evidence": [
                    state_mapping.get("narrative", "State map indicates mixed but actionable conditions."),
                    *state_mapping.get("cross_asset_signals", [])[:2],
                ],
                "opposing_evidence": [
                    "Event mix is not one-sided and can rotate quickly.",
                    "Short-horizon macro surprises can reverse cross-asset confirmation.",
                ],
                "upside_triggers": [
                    "Volatility cools and remains contained",
                    "Rates stabilize while breadth improves",
                ],
                "downside_triggers": [
                    "Volatility rises with continued dollar strength",
                    "Rates rise further with weaker cyclical breadth",
                ],
                "invalidation_conditions": [
                    "Cross-asset signals flip against the current directional bias",
                    "Event flow turns decisively opposite to the current thesis",
                ],
                "monitoring_list": [
                    "VIX regime persistence",
                    "US10Y daily direction",
                    "DXY daily direction",
                    "Oil shock headlines",
                    "Breadth in SPY/QQQ/IWM",
                ],
                "final_thesis": "Directional bias follows current observable regime and catalysts; conviction declines when cross-asset confirmation weakens.",
            }

        return {
            "state_mapping": state_mapping,
            "forecast_draft": forecast_draft,
        }

    def _mock_anti_hindsight_review(self, now: str, payload: dict[str, Any]) -> dict[str, Any]:
        forecast_draft = payload["forecast_draft"]
        output_language = str(payload.get("output_language", "en")).lower()
        use_zh = output_language == "zh"

        rule_report = payload.get("rule_report", {})
        incoming_rule_issues = rule_report.get("issues", []) if isinstance(rule_report, dict) else []

        issues = _detect_mock_review_issues(forecast_draft)
        for issue in incoming_rule_issues:
            if isinstance(issue, dict):
                code = issue.get("code", "UNKNOWN")
                message = issue.get("message", "")
                issues.append(f"Rule report issue {code}: {message}")

        reviewed_forecast = _sanitize_reviewed_forecast(forecast_draft)
        status = AntiHindsightStatus.PASS.value if not issues else AntiHindsightStatus.FAIL.value
        reviewed_forecast["anti_hindsight_status"] = status

        review_summary = (
            "未发现目标价或后验叙述问题。"
            if not issues
            else "检测到违规措辞，已生成清洗后的审查版预测。"
        ) if use_zh else (
            "No target-style or hindsight language detected."
            if not issues
            else "Detected banned phrasing and generated sanitized reviewed forecast."
        )

        return {
            "reviewed_at": now,
            "anti_hindsight_status": status,
            "issues": issues,
            "review_summary": review_summary,
            "reviewed_forecast": reviewed_forecast,
        }


def _detect_mock_review_issues(payload: dict[str, Any]) -> list[str]:
    serialized = json.dumps(payload, ensure_ascii=False)
    issues: list[str] = []
    for pattern in MockLLMClient._BANNED_PATTERNS:
        if pattern.search(serialized):
            issues.append(f"Detected banned phrase pattern: `{pattern.pattern}`")
    if not payload.get("invalidation_conditions"):
        issues.append("Missing invalidation_conditions")
    return issues


def _sanitize_reviewed_forecast(draft: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(draft, ensure_ascii=False)
    replacements = {
        r"target\s+price": "directional threshold",
        r"will\s+reach": "may trend toward",
        r"break\s+above": "move stronger than",
        r"fall\s+to": "weaken toward",
        r"hit\s+\$?\d{3,6}(?:\.\d+)?": "touch a new zone",
        r"目标价": "方向阈值",
        r"突破": "走强至",
        r"跌破": "走弱至",
        r"触及\s*\d+(?:\.\d+)?\s*(?:点|美元|元)?": "触及关键区间",
    }
    for pattern, replacement in replacements.items():
        serialized = re.sub(pattern, replacement, serialized, flags=re.IGNORECASE)
    return json.loads(serialized)


def _parse_json_or_raise(content: str, task_name: str) -> dict[str, Any]:
    """Parse JSON, raising a dedicated error for malformed output."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Some providers can prepend or append plain text around JSON.
        extracted = _extract_outer_json_object(cleaned)
        if extracted is None:
            raise LLMResponseError(f"Malformed JSON for task `{task_name}`: {exc.msg}") from exc
        try:
            parsed = json.loads(extracted)
        except json.JSONDecodeError as recovered_exc:
            raise LLMResponseError(
                f"Malformed JSON for task `{task_name}` after extraction: {recovered_exc.msg}"
            ) from recovered_exc

    if not isinstance(parsed, dict):
        raise LLMResponseError(f"Expected JSON object for task `{task_name}`, got {type(parsed)}")
    return parsed


def _extract_outer_json_object(text: str) -> str | None:
    """Extract the first balanced top-level JSON object from text."""
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def build_llm_client(settings: Settings) -> BaseLLMClient:
    """Factory for configured LLM backend."""
    if settings.use_live_data and settings.strict_live_mode and settings.llm_provider == "mock":
        raise ValueError("Strict live mode forbids LLM_PROVIDER=mock. Use minimax/openai/kimi.")
    if settings.llm_provider == "mock":
        return MockLLMClient()
    return OpenAICompatibleLLMClient(settings)
