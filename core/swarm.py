"""Inter-instance swarm routing with capability matching and circuit breaker.

Each Alphabetty instance declares capabilities via env vars. The SwarmRouter
tracks peer health, matches tool calls to capable peers, and falls back to
local execution when no peer is available. Pattern mirrors ProviderRouter.

Config (ALPHABETTY_ prefix):
  SWARM_NAME  — this instance's identity (e.g. "oracle", "lappy")
  SWARM_CAPS  — comma-separated capabilities
  SWARM_PEERS — comma-separated "name=url" pairs
  SWARM_KEY   — shared auth key for inter-instance calls
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ─── Capability model ───

TOOL_CAPABILITY_MAP: dict[str, str] = {
    # Browser tools → browser cap
    "browse": "browser",
    "click": "browser",
    "type_text": "browser",
    "extract": "browser",
    "screenshot": "browser",
    "scroll": "browser",
    "wait_for": "browser",
    "tab_list": "browser",
    "tab_new": "browser",
    "print_pdf": "browser",
    "youtube_play": "browser",
    "video_play": "browser",
    "signin_start": "browser",
    "signin_2fa": "browser",
    "signin_auto": "browser",
    "macro_record": "browser",
    "macro_stop": "browser",
    "macro_play": "browser",
    "insert_text": "browser",
    "click_at": "browser",
    "click_iframe": "browser",
    "type_iframe": "browser",
    # Image gen → image_gen cap
    "generate_image": "image_gen",
    # Doctor → doctor cap (self-healing SSH)
    "call_doctor": "doctor",
}

# Tools that should always execute locally (instance-specific config or state)
LOCAL_ONLY_TOOLS = {"search"}


# ─── Data classes ───

@dataclass
class PeerState:
    """Circuit breaker state for a peer."""
    last_health_check: float = 0.0
    healthy: bool = False
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_call: float = 0.0


@dataclass
class Peer:
    name: str
    url: str
    caps: set[str] = field(default_factory=set)
    state: PeerState = field(default_factory=PeerState)


# ─── Router ───

class SwarmRouter:
    """Lightweight peer registry with capability matching and circuit-breaker routing."""

    def __init__(self):
        self.peers: dict[str, Peer] = {}
        self.local_caps: set[str] = set()
        self.local_name: str = ""
        self._lock = asyncio.Lock()

    def init_peers(self):
        """Build peer list from config. Called once on startup."""
        if self.peers:
            return

        self.local_name = settings.swarm_name
        if settings.swarm_caps:
            self.local_caps = {c.strip() for c in settings.swarm_caps.split(",") if c.strip()}

        if not settings.swarm_peers:
            logger.info("Swarm: no peers configured")
            return

        for entry in settings.swarm_peers.split(","):
            entry = entry.strip()
            if "=" not in entry:
                continue
            name, url = entry.split("=", 1)
            name = name.strip()
            url = url.strip().rstrip("/")
            if name and url:
                self.peers[name] = Peer(name=name, url=url)

        logger.info(f"Swarm: local={self.local_name} caps={self.local_caps} peers={list(self.peers.keys())}")

    # ─── Health checking ───

    async def health_check_all(self):
        """Check all peers in parallel."""
        if not self.peers:
            return
        tasks = [self._check_peer(name) for name in self.peers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_peer(self, name: str):
        peer = self.peers.get(name)
        if not peer:
            return

        # Skip if in cooldown
        if time.time() < peer.state.cooldown_until:
            return

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{peer.url}/api/v1/swarm/status",
                    headers={"X-Swarm-Key": settings.swarm_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    peer.caps = set(data.get("capabilities", []))
                    peer.state.healthy = True
                    peer.state.consecutive_failures = 0
                    peer.state.last_health_check = time.time()
                    logger.debug(f"Swarm: peer {name} healthy, caps={peer.caps}")
                else:
                    self._record_failure(peer)
        except Exception as e:
            self._record_failure(peer)
            logger.debug(f"Swarm: peer {name} health check failed: {e}")

    def _record_failure(self, peer: Peer):
        peer.state.consecutive_failures += 1
        peer.state.healthy = False
        if peer.state.consecutive_failures >= 3:
            peer.state.cooldown_until = time.time() + 300  # 5 min
            logger.warning(f"Swarm: peer {peer.name} has {peer.state.consecutive_failures} failures, cooldown 5min")

    # ─── Routing ───

    def route_tool(self, tool_name: str, args: dict | None = None) -> Peer | None:
        """Check if a tool should be routed to a peer. Returns None for local execution."""
        # Never route these
        if tool_name in LOCAL_ONLY_TOOLS:
            return None

        # What capability does this tool need?
        required_cap = TOOL_CAPABILITY_MAP.get(tool_name)
        if not required_cap:
            return None  # Unknown tool — execute locally

        # Do we have the capability locally?
        if required_cap in self.local_caps:
            return None  # We can handle it

        # Find a healthy peer with the capability
        for peer in self.peers.values():
            if not peer.state.healthy:
                continue
            if time.time() < peer.state.cooldown_until:
                continue
            if required_cap in peer.caps:
                logger.info(f"Swarm: routing {tool_name} to {peer.name} (needs {required_cap})")
                return peer

        return None  # No capable peer — fall back to local

    def route_llm(self, intent: str = "agent") -> Peer | None:
        """Find a peer with better LLM capabilities for load distribution."""
        # Only route if local is llm_light and a peer has llm_heavy
        if "llm_heavy" in self.local_caps:
            return None  # We're already heavy

        for peer in self.peers.values():
            if peer.state.healthy and "llm_heavy" in peer.caps:
                logger.info(f"Swarm: routing LLM ({intent}) to {peer.name}")
                return peer

        return None

    # ─── Remote execution ───

    async def execute_remote(self, peer: Peer, tool_name: str, args: dict) -> dict:
        """Execute a tool on a remote peer via the swarm API."""
        url = f"{peer.url}/api/v1/swarm/execute"
        payload = {"tool": tool_name, "args": args}
        headers = {
            "Content-Type": "application/json",
            "X-Swarm-Key": settings.swarm_key,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                peer.state.last_call = time.time()
                return resp.json()
        except Exception as e:
            self._record_failure(peer)
            logger.warning(f"Swarm: remote execute failed on {peer.name}: {e}")
            raise

    async def call_llm_remote(self, peer: Peer, messages: list[dict], **kwargs) -> dict:
        """Proxy an LLM call through a peer's provider pool."""
        url = f"{peer.url}/api/v1/swarm/llm"
        payload = {"messages": messages, **kwargs}
        headers = {
            "Content-Type": "application/json",
            "X-Swarm-Key": settings.swarm_key,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                peer.state.last_call = time.time()
                return resp.json()
        except Exception as e:
            self._record_failure(peer)
            logger.warning(f"Swarm: remote LLM call failed on {peer.name}: {e}")
            raise

    # ─── Status ───

    def status(self) -> dict:
        """Return swarm status for this instance."""
        peers = []
        for name, peer in self.peers.items():
            peers.append({
                "name": name,
                "url": peer.url,
                "caps": sorted(peer.caps),
                "healthy": peer.state.healthy,
                "last_check": peer.state.last_health_check,
                "failures": peer.state.consecutive_failures,
            })
        return {
            "name": self.local_name,
            "capabilities": sorted(self.local_caps),
            "peers": peers,
        }


# ─── Singleton ───

swarm = SwarmRouter()
