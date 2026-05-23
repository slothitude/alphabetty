"""Multi-model LLM provider registry with automatic discovery and circuit-breaker routing.

Providers:
  - Z.ai (primary) — current default, reliable tool calling
  - OpenRouter — free models only (pricing.prompt == "0")
  - NVIDIA NIM — free models, discovered at runtime
  - Ollama — local, VRAM-limited, small models only

All use the OpenAI-compatible /v1/chat/completions format.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ─── Data classes ───

@dataclass
class ModelInfo:
    id: str
    provider: str  # "zai", "openrouter", "nvidia", "ollama"
    context_length: int = 0
    supports_tools: bool = True
    is_free: bool = False


@dataclass
class ProviderState:
    """Tracks rate-limit state for circuit-breaking."""
    last_call: float = 0.0
    calls_this_minute: int = 0
    minute_start: float = 0.0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    models: list[ModelInfo] = field(default_factory=list)
    state: ProviderState = field(default_factory=ProviderState)
    models_fetched_at: float = 0.0
    models_cache_ttl: int = 1800  # seconds


# ─── Router ───

class ProviderRouter:
    """Lightweight provider registry with model discovery and fallback routing."""

    def __init__(self):
        self.providers: dict[str, Provider] = {}
        self._lock = asyncio.Lock()
        self._call_locks: dict[str, asyncio.Lock] = {}

    def _init_providers(self):
        """Build provider list from config. Called once on startup."""
        if self.providers:
            return

        # Z.ai (primary) — always present
        if settings.llm_api_key:
            self.providers["zai"] = Provider(
                name="zai",
                base_url=settings.llm_url,
                api_key=settings.llm_api_key,
                models=[ModelInfo(id=settings.llm_model, provider="zai", is_free=True)],
                models_cache_ttl=999999,  # Static config, never refreshes
            )

        # OpenRouter — free models
        if settings.openrouter_api_key:
            self.providers["openrouter"] = Provider(
                name="openrouter",
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key,
                models_cache_ttl=3600,  # 1 hour
            )

        # NVIDIA NIM
        if settings.nvidia_api_key:
            self.providers["nvidia"] = Provider(
                name="nvidia",
                base_url=settings.nvidia_base_url,
                api_key=settings.nvidia_api_key,
                models_cache_ttl=7200,  # 2 hours
            )

        # Ollama — local
        if settings.ollama_url:
            ollama_base = settings.ollama_url.rsplit("/v1/", 1)[0] if "/v1/" in settings.ollama_url else settings.ollama_url
            self.providers["ollama"] = Provider(
                name="ollama",
                base_url=settings.ollama_url,
                api_key="",
                models=[ModelInfo(id=settings.ollama_model, provider="ollama", is_free=True)],
                models_cache_ttl=1800,  # 30 min
            )
            # Store base URL for /api/tags discovery
            self.providers["ollama"]._tags_url = f"{ollama_base}/api/tags"

        logger.info(f"Initialized {len(self.providers)} providers: {list(self.providers.keys())}")

    # ─── Model discovery ───

    async def refresh_models(self, provider_name: str | None = None):
        """Discover models from one or all providers."""
        self._init_providers()
        names = [provider_name] if provider_name else list(self.providers.keys())

        for name in names:
            provider = self.providers.get(name)
            if not provider:
                continue

            # Check cache
            age = time.time() - provider.models_fetched_at
            if age < provider.models_cache_ttl and provider.models:
                logger.debug(f"Skipping {name} model refresh (cache TTL {provider.models_cache_ttl}s, age {age:.0f}s)")
                continue

            try:
                if name == "openrouter":
                    await self._discover_openrouter(provider)
                elif name == "nvidia":
                    await self._discover_nvidia(provider)
                elif name == "ollama":
                    await self._discover_ollama(provider)
                # zai is static, skip
            except Exception as e:
                logger.warning(f"Model discovery failed for {name}: {e}")

    async def _discover_openrouter(self, provider: Provider):
        """Fetch free models from OpenRouter."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {provider.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        models = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            prompt_price = pricing.get("prompt", "1")
            # Free models have prompt == "0"
            if prompt_price == "0":
                models.append(ModelInfo(
                    id=m["id"],
                    provider="openrouter",
                    context_length=m.get("context_length", 0),
                    supports_tools=True,  # Most OpenRouter models support tools
                    is_free=True,
                ))

        provider.models = models
        provider.models_fetched_at = time.time()
        logger.info(f"OpenRouter: discovered {len(models)} free models")

    async def _discover_nvidia(self, provider: Provider):
        """Fetch available models from NVIDIA NIM."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {provider.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            if mid:
                models.append(ModelInfo(
                    id=mid,
                    provider="nvidia",
                    context_length=0,
                    supports_tools=True,
                    is_free=True,
                ))

        provider.models = models
        provider.models_fetched_at = time.time()
        logger.info(f"NVIDIA NIM: discovered {len(models)} models")

    async def _discover_ollama(self, provider: Provider):
        """Fetch installed models from Ollama."""
        tags_url = getattr(provider, "_tags_url", settings.ollama_url.replace("/v1/chat/completions", "/api/tags"))
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(tags_url)
                resp.raise_for_status()
                data = resp.json()

            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if name:
                    models.append(ModelInfo(
                        id=name,
                        provider="ollama",
                        is_free=True,
                    ))

            provider.models = models
            provider.models_fetched_at = time.time()
            logger.info(f"Ollama: discovered {len(models)} models")
        except Exception:
            # Ollama might be down — keep the default model
            logger.debug("Ollama discovery failed, keeping default model")

    # ─── Rate limiting & circuit breaker ───

    def _can_call(self, provider: Provider) -> bool:
        """Check if a provider is available (not in cooldown)."""
        state = provider.state
        now = time.time()

        # Check cooldown
        if state.cooldown_until and now < state.cooldown_until:
            return False

        # Minimum 2s spacing between calls to free providers
        if provider.name != "zai" and (now - state.last_call) < 2.0:
            return False

        return True

    def _record_success(self, provider: Provider):
        state = provider.state
        state.consecutive_failures = 0
        state.last_call = time.time()

    def _record_failure(self, provider: Provider, status_code: int = 0, retry_after: int = 0):
        state = provider.state
        state.consecutive_failures += 1
        state.last_call = time.time()

        if status_code == 429:
            # Rate limited
            cooldown = retry_after if retry_after else 60
            state.cooldown_until = time.time() + cooldown
            logger.warning(f"Provider {provider.name} rate-limited (429), cooldown {cooldown}s")
        elif state.consecutive_failures >= 3:
            # Too many failures
            state.cooldown_until = time.time() + 300  # 5 min
            logger.warning(f"Provider {provider.name} has {state.consecutive_failures} consecutive failures, cooldown 5min")

    async def _wait_for_slot(self, provider: Provider):
        """Wait until minimum spacing is met for this provider."""
        if provider.name == "zai":
            return
        elapsed = time.time() - provider.state.last_call
        if elapsed < 2.0:
            await asyncio.sleep(2.0 - elapsed)

    # ─── Routing ───

    def _get_fallback_chain(self, intent: str = "agent") -> list[Provider]:
        """Return ordered list of providers for a given intent."""
        self._init_providers()

        if intent == "agent":
            order = ["zai", "nvidia", "openrouter", "ollama"]
        elif intent == "chat":
            order = ["zai", "openrouter", "nvidia", "ollama"]
        elif intent == "planning":
            order = ["openrouter", "nvidia", "ollama", "zai"]
        else:
            order = ["zai", "openrouter", "nvidia", "ollama"]

        chain = []
        for name in order:
            p = self.providers.get(name)
            if p and p.api_key and self._can_call(p):
                chain.append(p)
        return chain

    async def call(self, messages: list[dict], model: str | None = None,
                   base_url: str | None = None, api_key: str | None = None,
                   intent: str = "agent", stream: bool = False,
                   tools: list[dict] | None = None, max_tokens: int = 16384) -> httpx.Response:
        """Make an LLM call through the provider fallback chain.

        If base_url/api_key are provided (explicit overrides), call that directly.
        Otherwise, route through the fallback chain.
        """
        self._init_providers()

        # Direct override — skip routing
        if base_url and api_key:
            return await self._single_call(base_url, api_key, model or settings.llm_model,
                                           messages, stream=stream, tools=tools, max_tokens=max_tokens)

        # If model specified, find its provider
        if model:
            for p in self.providers.values():
                for m in p.models:
                    if m.id == model:
                        if self._can_call(p):
                            return await self._single_call(
                                p.base_url, p.api_key, model,
                                messages, stream=stream, tools=tools, max_tokens=max_tokens,
                            )
                        break

        # Fallback chain
        chain = self._get_fallback_chain(intent)
        last_error = None

        for provider in chain:
            try:
                await self._wait_for_slot(provider)
                mdl = model or self._pick_model(provider)
                client, resp = await self._single_call(
                    provider.base_url, provider.api_key, mdl,
                    messages, stream=stream, tools=tools, max_tokens=max_tokens,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    self._record_failure(provider, 429, retry_after)
                    last_error = f"{provider.name} rate-limited"
                    await client.aclose()
                    continue
                resp.raise_for_status()
                self._record_success(provider)
                return client, resp
            except httpx.HTTPStatusError as e:
                self._record_failure(provider, e.response.status_code)
                last_error = str(e)
                logger.warning(f"Provider {provider.name} failed: {e}, trying next")
                continue
            except Exception as e:
                self._record_failure(provider)
                last_error = str(e)
                logger.warning(f"Provider {provider.name} failed: {e}, trying next")
                continue

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    def _pick_model(self, provider: Provider) -> str:
        """Pick a default model from a provider, preferring known-good models."""
        if not provider.models:
            if provider.name == "zai":
                return settings.llm_model
            if provider.name == "ollama":
                return settings.ollama_model
            return ""

        # Prefer known-good chat models for each provider
        preferred = {
            "openrouter": ["deepseek/deepseek-v4-flash:free", "openrouter/owl-alpha", "google/gemma-3-27b-it:free"],
            "nvidia": ["meta/llama-3.3-70b-instruct", "nvidia/llama-3.1-nemotron-70b-instruct", "mistralai/mixtral-8x22b-instruct-v0.1"],
        }
        prefs = preferred.get(provider.name, [])
        for pref in prefs:
            for m in provider.models:
                if m.id == pref:
                    return m.id

        # Fall back to first model with :free suffix (OpenRouter convention)
        for m in provider.models:
            if ":free" in m.id:
                return m.id

        # Last resort: first available
        return provider.models[0].id

    async def _single_call(self, url: str, api_key: str, model: str,
                           messages: list[dict], stream: bool = False,
                           tools: list[dict] | None = None,
                           max_tokens: int = 16384) -> httpx.Response:
        """Make a single LLM call to one provider."""
        # Append /chat/completions if not already in URL
        if not url.rstrip("/").endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
        req = client.build_request("POST", url, json=payload, headers=headers)
        return client, await client.send(req, stream=stream)

    # ─── Public API ───

    def list_models(self) -> list[dict]:
        """Return all known models grouped by provider."""
        self._init_providers()
        result = []
        for name, provider in self.providers.items():
            for m in provider.models:
                result.append({
                    "id": m.id,
                    "provider": name,
                    "context_length": m.context_length,
                    "supports_tools": m.supports_tools,
                    "is_free": m.is_free,
                })
        return result

    def get_provider_for_model(self, model_id: str) -> Provider | None:
        """Find the provider that has a given model."""
        self._init_providers()
        for p in self.providers.values():
            if any(m.id == model_id for m in p.models):
                return p
        return None


# ─── Singleton ───

router = ProviderRouter()
