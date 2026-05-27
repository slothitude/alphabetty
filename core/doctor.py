"""Dr. Claude Code — Self-healing doctor for Alphabetty.

Monitors Lappy services (SearXNG, Ollama, LiteLLM). When a service fails
consecutively, SSHes to Lappy and runs Claude Code headless to diagnose
and fix the issue.
"""

import asyncio
import logging
import time
import json
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("doctor")

# ─── Service Health Tracking ───

@dataclass
class ServiceHealth:
    name: str
    check_fn: callable = None
    consecutive_failures: int = 0
    last_error: str = ""
    last_doctor_call: float = 0.0
    healthy: bool = True

_services: dict[str, ServiceHealth] = {}
_COOLDOWN = 300  # 5 min between doctor calls per service
_FAILURE_THRESHOLD = 3  # consecutive failures before auto-call


def register_service(name: str, check_fn):
    """Register a health check function for a named service."""
    _services[name] = ServiceHealth(name=name, check_fn=check_fn)


async def run_health_checks():
    """Probe all registered services. Auto-call doctor after threshold failures."""
    from config import settings
    if not settings.doctor_enabled:
        return

    for name, svc in _services.items():
        try:
            ok, err = await svc.check_fn()
        except Exception as e:
            ok, err = False, str(e)

        if ok:
            svc.consecutive_failures = 0
            svc.healthy = True
            svc.last_error = ""
        else:
            svc.consecutive_failures += 1
            svc.healthy = False
            svc.last_error = err
            logger.warning(f"[doctor] {name} unhealthy ({svc.consecutive_failures}x): {err}")

            if svc.consecutive_failures >= _FAILURE_THRESHOLD:
                now = time.time()
                if now - svc.last_doctor_call >= _COOLDOWN:
                    logger.info(f"[doctor] Auto-calling doctor for {name}")
                    svc.last_doctor_call = now
                    # Fire and forget — don't block health loop
                    asyncio.create_task(
                        call_doctor(symptom=err, service=name, auto=True)
                    )


def get_health_status() -> dict:
    """Return current health status for all registered services."""
    return {
        name: {
            "healthy": svc.healthy,
            "consecutive_failures": svc.consecutive_failures,
            "last_error": svc.last_error,
            "last_doctor_call": svc.last_doctor_call,
        }
        for name, svc in _services.items()
    }


# ─── SSH + Claude Code Invocation ───

async def call_doctor(symptom: str, service: str = "", auto: bool = False) -> dict:
    """SSH to Lappy, run Claude Code headless to diagnose and fix."""
    from config import settings

    if not settings.doctor_enabled:
        return {"error": "Doctor is disabled (ALPHABETTY_DOCTOR_ENABLED=false)"}

    if not settings.ssh_pass:
        return {"error": "No SSH password configured (ALPHABETTY_SSH_PASS)"}

    prompt = _build_prompt(service, symptom, _services.get(service))
    result = await _ssh_exec(
        host=settings.ssh_host,
        host_tailscale=settings.ssh_host_tailscale,
        user=settings.ssh_user,
        password=settings.ssh_pass,
        port=settings.ssh_port,
        prompt=prompt,
        timeout=settings.doctor_timeout,
    )

    if result.get("success"):
        logger.info(f"[doctor] Fix applied for {service}: {result.get('output', '')[:200]}")
    else:
        logger.error(f"[doctor] Failed to fix {service}: {result.get('error', '')}")

    return result


async def _ssh_exec(host: str, host_tailscale: str, user: str, password: str,
                    port: int, prompt: str, timeout: int) -> dict:
    """SSH with Tailscale fallback. Write prompt to temp file, run Claude Code headless."""
    loop = asyncio.get_event_loop()

    def _run():
        import paramiko
        from io import StringIO

        # Try LAN first, then Tailscale fallback
        connected = False
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for try_host in [host, host_tailscale]:
            try:
                client.connect(try_host, port=port, username=user, password=password,
                               timeout=15, allow_agent=False, look_for_keys=False)
                connected = True
                logger.info(f"[doctor] SSH connected to {try_host}")
                break
            except Exception as e:
                logger.debug(f"[doctor] SSH to {try_host} failed: {e}")
                continue

        if not connected:
            return {"error": f"SSH failed to both {host} and {host_tailscale}", "success": False}

        try:
            # Write prompt to temp file on Lappy via SFTP
            prompt_path = "C:\\Users\\aaron\\doctor_prompt.txt"
            sftp = client.open_sftp()
            with sftp.file(prompt_path, "w") as f:
                f.write(prompt)
            sftp.close()
            logger.info(f"[doctor] Wrote diagnostic prompt to {prompt_path}")

            # Run Claude Code headless
            cmd = (
                f'claude -p "Read {prompt_path} and fix the described issue. '
                f'Delete {prompt_path} when done." '
                f'--allowedTools "Bash" --output-format text'
            )
            logger.info(f"[doctor] Running: {cmd[:120]}...")

            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            output = stdout.read().decode("utf-8", errors="replace")
            err_output = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()

            # Truncate output
            output = output[:5000]

            if exit_code != 0 and not output:
                return {
                    "error": f"claude exited {exit_code}: {err_output[:500]}",
                    "success": False,
                }

            return {"output": output, "success": True}

        except Exception as e:
            return {"error": f"SSH exec failed: {e}", "success": False}
        finally:
            client.close()

    return await loop.run_in_executor(None, _run)


def _build_prompt(service: str, error: str, svc: ServiceHealth = None) -> str:
    """Generate the diagnostic prompt for Claude Code."""
    failures = svc.consecutive_failures if svc else 1
    return f"""You are Dr. Claude Code — infrastructure self-healing agent.

## Symptom
Service: {service}
Error: {error}
Consecutive failures: {failures}

## Lappy Key Paths
- Media stack: D:\\docker\\media-stack\\docker-compose.yml
- SearXNG settings: D:\\docker\\media-stack\\searxng\\settings.yml
- Traefik: C:\\traefik\\
- MCP servers: C:\\mcp-servers\\
- Alphabetty: C:\\Users\\aaron\\Desktop\\alphabetty\\

## Key Commands
- docker ps -a
- docker logs <container_name> --tail 50
- docker restart <container_name>
- cd D:\\docker\\media-stack && docker compose restart <service>
- netstat -ano | findstr :<port>
- tasklist | findstr docker

## Rules
1. Diagnose the failure
2. Fix it (restart container, clear lock, etc.)
3. Verify the fix worked
4. Report what was wrong and what you did
5. Do NOT reboot. Do NOT stop working services.
"""


# ─── Default Health Checks ───

async def _check_searxng():
    """Check SearXNG is responding."""
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"http://{settings.ssh_host}:8888/search?q=test&format=json")
            if r.status_code == 200:
                return True, ""
            return False, f"SearXNG returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"SearXNG unreachable: {e}"


async def _check_ollama():
    """Check Ollama is responding."""
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://{settings.ssh_host}:11434/api/tags")
            if r.status_code == 200:
                return True, ""
            return False, f"Ollama returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"Ollama unreachable: {e}"


async def _check_litellm():
    """Check LiteLLM proxy is responding."""
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://{settings.ssh_host}:4000/v1/models")
            if r.status_code == 200:
                return True, ""
            return False, f"LiteLLM returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"LiteLLM unreachable: {e}"


# ─── Register Default Services ───

def _init_defaults():
    """Register the default Lappy health checks."""
    register_service("searxng", _check_searxng)
    register_service("ollama", _check_ollama)
    register_service("litellm", _check_litellm)


_init_defaults()
