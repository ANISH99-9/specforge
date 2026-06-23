"""
SpecForge — Multi-Provider LLM Client
Abstracts Groq and Google behind a single interface.

Model tier chain (auto-fallback on failure):
  Primary:   Google  gemini-2.5-flash
  Fallback:  Groq  llama-3.1-8b-instant
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from typing import Any, Optional, Type
from pydantic import BaseModel

import groq
from google.api_core import exceptions as google_exceptions

from config import settings, estimate_cost

logger = logging.getLogger(__name__)

_RATE_LIMIT_ERRORS = (
    groq.RateLimitError,
    google_exceptions.ResourceExhausted,
)

_AUTH_ERRORS = (
    groq.AuthenticationError,
    google_exceptions.Unauthenticated,
    google_exceptions.PermissionDenied,
)


def _is_rate_or_quota_error(exc: Exception) -> bool:
    if isinstance(exc, _RATE_LIMIT_ERRORS):
        return True
    if isinstance(exc, groq.APIStatusError):
        code = getattr(exc, "status_code", 0)
        if code in (413, 429):
            return True
        body = str(exc).lower()
        return "rate_limit" in body or "tokens per minute" in body
    return False


class LLMResponse(BaseModel):
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    duration_ms: int = 0


class LLMClient:
    """
    Unified async LLM client with automatic provider fallback.
    All pipeline stages use Gemini 2.5 Flash; failures cascade to
    OpenRouter then Groq emergency tier.
    """

    def __init__(self) -> None:
        self.settings = settings
        self._groq_client: Any = None

    # ── Lazy client initialisation ────────────────────────────────

    def _groq(self):
        if self._groq_client is None:
            from groq import Groq
            self._groq_client = Groq(api_key=settings.groq_api_key)
        return self._groq_client

    def _provider_has_key(self, provider: str) -> bool:
        keys = {
            "google": settings.google_ai_api_key,
            "groq": settings.groq_api_key,
        }
        return bool(keys.get(provider, "").strip())

    def available_providers(self) -> list[str]:
        return [p for p in ("google", "groq") if self._provider_has_key(p)]

    def _fallback_chain(self, provider: str, model: str) -> list[tuple[str, str]]:
        """Build deduplicated chain: requested pair first, then tier defaults."""
        chain: list[tuple[str, str]] = [(provider, model)]
        for tier in settings.model_tier_chain:
            if tier not in chain:
                chain.append(tier)
        return [(p, m) for p, m in chain if self._provider_has_key(p)]

    def _should_fallback(self, exc: Exception) -> bool:
        if isinstance(exc, _AUTH_ERRORS):
            return True
        if _is_rate_or_quota_error(exc):
            return True
        msg = str(exc).lower()
        fallback_signals = (
            "api key", "api_key", "authentication", "unauthorized",
            "invalid key", "permission denied", "forbidden",
            "not found", "does not exist", "model", "quota",
            "billing", "access denied", "credentials",
        )
        return any(sig in msg for sig in fallback_signals)

    # ── Core structured call ──────────────────────────────────────

    async def complete_structured(
        self,
        provider: str,
        model: str,
        system: str,
        user: str,
        response_model: Type[BaseModel],
        temperature: float = 0.0,
    ) -> tuple[BaseModel, LLMResponse]:
        """
        Returns (parsed_pydantic_model, llm_response_metadata).
        Uses JSON mode / structured output — never free text.
        """
        t0 = time.monotonic()

        raw_json, meta = await self.complete_raw(
            provider=provider,
            model=model,
            system=system,
            user=user,
            temperature=temperature,
            return_meta=True,
        )

        from validation.validator import auto_fix_json
        cleaned = auto_fix_json(raw_json)
        data = json.loads(cleaned)
        instance = response_model.model_validate(data)

        meta.duration_ms = int((time.monotonic() - t0) * 1000)
        return instance, meta

    async def complete_raw(
        self,
        provider: str,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        return_meta: bool = False,
        max_tokens: int = 2000,
    ) -> str | tuple[str, LLMResponse]:
        """
        Returns raw JSON string from the LLM.
        Tries Primary → Fallback → Emergency providers automatically.
        """
        t0 = time.monotonic()
        chain = self._fallback_chain(provider, model)

        if not chain:
            raise RuntimeError(
                "No LLM API keys configured. Set at least one of "
                "GOOGLE_AI_API_KEY or GROQ_API_KEY in backend/.env"
            )

        last_error: Optional[Exception] = None

        for prov, mod in chain:
            try:
                text, in_tok, out_tok = await self._call_with_retries(
                    prov, mod, system, user, temperature, max_tokens
                )
                cost = estimate_cost(mod, in_tok, out_tok)
                duration = int((time.monotonic() - t0) * 1000)

                if prov != provider or mod != model:
                    logger.info(
                        f"[LLM] Used fallback tier {prov}/{mod} "
                        f"(requested {provider}/{model})"
                    )

                logger.info(
                    f"[LLM] {prov}/{mod} | {in_tok}+{out_tok} tok "
                    f"| ${cost:.5f} | {duration}ms"
                )

                meta = LLMResponse(
                    content=text,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost,
                    provider=prov,
                    model=mod,
                    duration_ms=duration,
                )
                return (text, meta) if return_meta else text

            except Exception as e:
                last_error = e
                remaining = [f"{p}/{m}" for p, m in chain if (p, m) != (prov, mod)]
                if self._should_fallback(e) and remaining:
                    logger.warning(
                        f"[LLM] {prov}/{mod} failed ({e!s:.120}). "
                        f"Trying next tier: {remaining[0]}"
                    )
                    continue
                raise

        raise RuntimeError(
            f"All LLM providers failed. Last error: {last_error}"
        ) from last_error

    async def _call_with_retries(
        self,
        provider: str,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int = 2000,
    ) -> tuple[str, int, int]:
        max_retries = 5
        base_delay = 2.0
        last_rate_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                if provider == "groq":
                    return await asyncio.to_thread(
                        self._groq_call, model, system, user, temperature, max_tokens
                    )
                if provider == "google":
                    return await asyncio.to_thread(
                        self._google_call, model, system, user, temperature, max_tokens
                    )
                raise ValueError(f"Unknown provider: {provider}")

            except _RATE_LIMIT_ERRORS as e:
                last_rate_error = e
                if attempt == max_retries - 1:
                    logger.error(
                        f"[LLM] Rate limit retries exhausted for {provider}/{model}."
                    )
                    raise

                delay = base_delay * (2 ** attempt)
                try:
                    if hasattr(e, "response") and e.response is not None:
                        headers = e.response.headers
                        if "retry-after" in headers:
                            delay = float(headers["retry-after"])
                        elif "x-ratelimit-reset" in headers:
                            val_str = str(headers["x-ratelimit-reset"]).rstrip("s")
                            delay = max(0.5, float(val_str))
                except Exception:
                    pass

                try:
                    match = re.search(
                        r"try again in (\d+(?:\.\d+)?)s", str(e), re.IGNORECASE
                    )
                    if match:
                        delay = float(match.group(1))
                except Exception:
                    pass

                delay = max(0.5, delay + 0.5)
                logger.warning(
                    f"[LLM] Rate limit on {provider}/{model}. "
                    f"Retrying in {delay:.2f}s ({attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)

            except groq.APIStatusError as e:
                if _is_rate_or_quota_error(e):
                    last_rate_error = e
                    if attempt == max_retries - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"[LLM] Groq quota hit on {provider}/{model}. "
                        f"Retrying in {delay:.2f}s ({attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

        raise last_rate_error or RuntimeError("LLM call failed")

    # ── Provider implementations ──────────────────────────────────

    def _groq_call(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int = 2000,
    ) -> tuple[str, int, int]:
        is_reasoning = "deepseek" in model.lower() or "r1" in model.lower()
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not is_reasoning:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self._groq().chat.completions.create(**kwargs)
        except groq.APIStatusError as e:
            # Groq JSON mode returns partial output in failed_generation when truncated
            body = str(e)
            if "json_validate_failed" in body or "failed_generation" in body:
                from validation.validator import extract_failed_generation, salvage_truncated_json
                partial = extract_failed_generation(body)
                if partial:
                    logger.warning(
                        f"[LLM] Groq json_validate_failed — salvaging partial JSON "
                        f"({len(partial)} chars)"
                    )
                    return partial, 0, 0
                salvaged = salvage_truncated_json(body)
                if salvaged:
                    return json.dumps(salvaged), 0, 0
            raise

        choice = response.choices[0].message.content or "{}"
        choice = re.sub(
            r"<think>.*?</think>", "", choice, flags=re.DOTALL
        ).strip()
        choice = re.sub(r"^```(?:json)?\s*", "", choice, flags=re.IGNORECASE)
        choice = re.sub(r"\s*```$", "", choice).strip()
        usage = response.usage
        return choice, usage.prompt_tokens, usage.completion_tokens

    def _google_call(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int = 2000,
    ) -> tuple[str, int, int]:
        # New Google GenAI SDK (google-genai)
        # User-requested form:
        #   from google import genai
        #   client = genai.Client(api_key=GOOGLE_API_KEY)
        #   response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        from google import genai  # type: ignore

        client = genai.Client(api_key=settings.google_ai_api_key)
        prompt = f"{system}\n\n{user}"

        # Try to enforce JSON responses when supported; otherwise rely on prompt constraints.
        text = "{}"
        in_tok = out_tok = 0

        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                    "max_output_tokens": max_tokens,
                },
            )
        except TypeError:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )

        # Response text
        try:
            text = getattr(response, "text", None) or "{}"
        except Exception:
            text = "{}"

        # Best-effort token usage
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
                out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        except Exception:
            in_tok = out_tok = 0

        return text, in_tok, out_tok


# Singleton
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
