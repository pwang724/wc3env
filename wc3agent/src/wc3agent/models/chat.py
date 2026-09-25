"""A text-in, text-out chat model over HTTPS: OpenAI-compatible or Anthropic. No game imports."""

from __future__ import annotations

import http.client
import json
import time
from dataclasses import dataclass
from urllib.parse import urlparse

RETRIES = 3
PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
}
# A Claude subscription token (from `claude setup-token`) starts with this. Anthropic accepts it only with the
# OAuth beta header and with Claude Code's identity as the first system block.
SUBSCRIPTION_PREFIX = "sk-ant-oat"
SUBSCRIPTION_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


@dataclass(frozen=True)
class ChatModel:
    provider: str
    model: str
    api_key: str
    base_url: str = ""
    max_tokens: int = 4000  # reasoning counts against it; at 1500, 1% of turns ran out before answering
    timeout: float = 120.0
    reasoning: str = "medium"  # how long the model thinks per turn; the game runs meanwhile in realtime

    @classmethod
    def from_environment(cls, env):
        """MACRO_PROVIDER (openai | anthropic), MACRO_MODEL, optional MACRO_BASE_URL, MACRO_API_KEY,
        MACRO_REASONING (how hard it thinks: low | medium | high | xhigh; default medium).
        Anthropic takes ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN for a Claude subscription; the API key wins."""
        provider = env.get("MACRO_PROVIDER", "")
        if provider not in PROVIDERS:
            raise ValueError("Set MACRO_PROVIDER to openai (any OpenAI-compatible endpoint) or anthropic")
        key = env.get("MACRO_API_KEY") or env.get(PROVIDERS[provider][1], "")
        if provider == "anthropic":
            key = key or env.get("ANTHROPIC_AUTH_TOKEN", "")
        if not key or not env.get("MACRO_MODEL"):
            also = " or ANTHROPIC_AUTH_TOKEN" if provider == "anthropic" else ""
            raise ValueError(f"Set MACRO_MODEL and {PROVIDERS[provider][1]}{also} (or MACRO_API_KEY)")
        return cls(
            provider,
            env["MACRO_MODEL"],
            key,
            env.get("MACRO_BASE_URL", ""),
            reasoning=env.get("MACRO_REASONING", "medium"),
        )

    def complete(self, system, messages):
        """messages: [{"role": "user" | "assistant", "content": text}]. Returns (text, record)."""
        url = urlparse((self.base_url or PROVIDERS[self.provider][0]).rstrip("/"))
        if self.provider == "anthropic":
            headers = {"anthropic-version": "2023-06-01"}
            # The system prompt never changes within a game: cache it.
            blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if self.api_key.startswith(SUBSCRIPTION_PREFIX):
                headers |= {"Authorization": f"Bearer {self.api_key}", "anthropic-beta": "oauth-2025-04-20"}
                blocks.insert(0, {"type": "text", "text": SUBSCRIPTION_IDENTITY})
            else:
                headers["x-api-key"] = self.api_key
            # The history only grows between trims: mark the newest message too, so the next turn reads
            # everything up to here from cache and pays full price only for its own new exchange.
            *earlier, last = messages
            text = [{"type": "text", "text": last["content"], "cache_control": {"type": "ephemeral"}}]
            path = "/messages"
            body = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "output_config": {"effort": self.reasoning},
                "system": blocks,
                "messages": [*earlier, {**last, "content": text}],
            }
        else:
            path, headers = "/chat/completions", {"Authorization": f"Bearer {self.api_key}"}
            body = {
                "model": self.model,
                "max_completion_tokens": self.max_tokens,
                "reasoning_effort": self.reasoning,
                "messages": [{"role": "system", "content": system}, *messages],
            }
        connect = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        started, waited = time.perf_counter(), 0.0
        for attempt in range(RETRIES + 1):
            connection = connect(url.netloc, timeout=self.timeout)
            try:
                connection.request(
                    "POST",
                    url.path + path,
                    json.dumps(body).encode("utf-8"),
                    {"Content-Type": "application/json", **headers},
                )
                response = connection.getresponse()
                raw = response.read().decode("utf-8").replace(self.api_key, "[REDACTED]")
            except (OSError, http.client.HTTPException):  # a dropped or garbled connection: same as a busy server
                if attempt == RETRIES:
                    raise
                waited += 2.0 * (attempt + 1)
                time.sleep(2.0 * (attempt + 1))
                continue
            finally:
                connection.close()
            # A rate limit or an overloaded server passes: wait and ask again. The wait counts as thinking time.
            if response.status not in (429, 500, 502, 503, 529) or "insufficient_quota" in raw or attempt == RETRIES:
                break
            pause = float(response.getheader("retry-after") or 0) or 2.0 * (attempt + 1)
            waited += pause
            time.sleep(pause)
        record = {
            "request": body,
            "status": response.status,
            "seconds": round(time.perf_counter() - started, 3),
            "attempts": attempt + 1,
            "waited": round(waited, 3),
        }
        if response.status != 200:
            raise RuntimeError(f"{self.provider} HTTP {response.status}: {raw[:500]}")
        data = json.loads(raw)
        record["response"] = data
        record["usage"] = data.get("usage", {})
        if self.provider == "anthropic":
            text = "".join(part.get("text", "") for part in data["content"] if part.get("type") == "text")
        else:
            text = data["choices"][0]["message"]["content"] or ""
        return text, record
