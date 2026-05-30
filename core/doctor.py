"""Dr. Claude Code — Self-healing doctor for Alphabetty.

Monitors Lappy services (SearXNG, Ollama, LiteLLM). When a service fails
consecutively, tries Docker-first auto-fix, then escalates to Claude Code
headless via SSH.
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
    # Service-specific fix config
    container_name: str = ""           # Docker container to restart
    compose_dir: str = ""              # docker compose project dir on Lappy
    compose_service: str = ""          # service name inside compose
    auto_heal: bool = True             # False = monitor only, never auto-fix
    runbook: str = ""                  # Service-specific instructions for Claude
    # State
    consecutive_failures: int = 0
    last_error: str = ""
    last_doctor_call: float = 0.0
    healthy: bool = True

_services: dict[str, ServiceHealth] = {}
_COOLDOWN = 600          # 10 min between auto-heal attempts per service
_FAILURE_THRESHOLD = 3   # consecutive failures before auto-fix
_MAX_LOG_ENTRIES = 50
_fix_log: list[dict] = []


def register_service(name: str, check_fn, **kwargs):
    """Register a health check function for a named service."""
    _services[name] = ServiceHealth(name=name, check_fn=check_fn, **kwargs)


async def run_health_checks():
    """Probe all registered services. Auto-fix after threshold failures."""
    from config import settings
    if not settings.doctor_enabled:
        return

    for name, svc in _services.items():
        try:
            ok, err = await svc.check_fn()
        except Exception as e:
            ok, err = False, str(e)

        if ok:
            if svc.consecutive_failures > 0:
                logger.info(f"[doctor] {name} recovered (was failing {svc.consecutive_failures}x)")
            svc.consecutive_failures = 0
            svc.healthy = True
            svc.last_error = ""
        else:
            svc.consecutive_failures += 1
            svc.healthy = False
            svc.last_error = err
            logger.warning(f"[doctor] {name} unhealthy ({svc.consecutive_failures}x): {err}")

            if svc.auto_heal and svc.consecutive_failures >= _FAILURE_THRESHOLD:
                now = time.time()
                if now - svc.last_doctor_call >= _COOLDOWN:
                    svc.last_doctor_call = now
                    task = asyncio.create_task(
                        _auto_heal(svc, err)
                    )
                    def _log_exception(t):
                        if not t.cancelled() and (exc := t.exception()):
                            logger.error(f"[doctor] Auto-heal failed: {exc}")
                    task.add_done_callback(_log_exception)


async def _auto_heal(svc: ServiceHealth, error: str):
    """Try Docker-first fix, then escalate to Claude Code."""
    logger.info(f"[doctor] Auto-healing {svc.name}...")

    # Phase 1: Docker restart (fast, cheap)
    if svc.container_name:
        restarted = await _ssh_docker_restart(svc)
        if restarted:
            # Wait for service to come back
            await asyncio.sleep(10)
            # Verify
            try:
                ok, err = await svc.check_fn()
            except Exception:
                ok = False
            if ok:
                msg = f"[doctor] {svc.name} fixed by docker restart"
                logger.info(msg)
                _log_fix(svc.name, "docker_restart", True, msg)
                return
            else:
                logger.warning(f"[doctor] {svc.name} still unhealthy after restart, escalating to Claude Code")

    # Phase 2: Claude Code headless (slow, expensive)
    result = await call_doctor(symptom=error, service=svc.name, auto=True)

    # Phase 3: Post-fix verification
    if result.get("success"):
        await asyncio.sleep(5)
        try:
            ok, err = await svc.check_fn()
        except Exception:
            ok = False
        if ok:
            _log_fix(svc.name, "claude_code", True, "Service recovered after Claude Code fix")
        else:
            _log_fix(svc.name, "claude_code", False, f"Claude ran but service still unhealthy: {err}")
    else:
        _log_fix(svc.name, "claude_code", False, result.get("error", "unknown"))


def _log_fix(service: str, method: str, success: bool, detail: str):
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "service": service,
        "method": method,
        "success": success,
        "detail": detail[:500],
    }
    _fix_log.append(entry)
    if len(_fix_log) > _MAX_LOG_ENTRIES:
        _fix_log.pop(0)
    status = "OK" if success else "FAIL"
    print(f"[doctor] Fix {status}: {service} via {method} — {detail[:200]}")


def get_health_status() -> dict:
    """Return current health status for all registered services."""
    return {
        name: {
            "healthy": svc.healthy,
            "consecutive_failures": svc.consecutive_failures,
            "last_error": svc.last_error,
            "last_doctor_call": svc.last_doctor_call,
            "auto_heal": svc.auto_heal,
        }
        for name, svc in _services.items()
    }


def get_fix_log() -> list[dict]:
    """Return recent fix attempts."""
    return list(_fix_log)


# ─── Docker-First Fix ───

async def _ssh_docker_restart(svc: ServiceHealth) -> bool:
    """SSH to Lappy and restart the Docker container. Returns True if restart succeeded."""
    from config import settings
    if not settings.ssh_pass:
        return False

    loop = asyncio.get_event_loop()

    def _run():
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for host in [settings.ssh_host, settings.ssh_host_tailscale]:
            try:
                client.connect(host, port=settings.ssh_port, username=settings.ssh_user,
                               password=settings.ssh_pass, timeout=10,
                               allow_agent=False, look_for_keys=False)
                break
            except Exception:
                continue
        else:
            return False

        try:
            if svc.compose_dir and svc.compose_service:
                cmd = f"cd {svc.compose_dir} && docker compose restart {svc.compose_service}"
            else:
                cmd = f"docker restart {svc.container_name}"

            logger.info(f"[doctor] Running: {cmd}")
            stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8", errors="replace")[:500]
            return exit_code == 0
        except Exception as e:
            logger.error(f"[doctor] Docker restart failed: {e}")
            return False
        finally:
            client.close()

    return await loop.run_in_executor(None, _run)


# ─── SSH + Claude Code Invocation ───

async def call_doctor(symptom: str, service: str = "", auto: bool = False) -> dict:
    """SSH to Lappy, run Claude Code headless to diagnose and fix."""
    from config import settings

    if not settings.doctor_enabled:
        return {"error": "Doctor is disabled"}

    if not settings.ssh_pass:
        return {"error": "No SSH password configured"}

    svc = _services.get(service)
    prompt = _build_prompt(service, symptom, svc)
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
        logger.info(f"[doctor] Claude Code fix for {service}: {result.get('output', '')[:200]}")
    else:
        logger.error(f"[doctor] Claude Code failed for {service}: {result.get('error', '')}")

    return result


async def _ssh_exec(host: str, host_tailscale: str, user: str, password: str,
                    port: int, prompt: str, timeout: int) -> dict:
    """SSH with Tailscale fallback. Write prompt to temp file, run Claude Code headless."""
    loop = asyncio.get_event_loop()

    def _run():
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for try_host in [host, host_tailscale]:
            try:
                client.connect(try_host, port=port, username=user, password=password,
                               timeout=15, allow_agent=False, look_for_keys=False)
                break
            except Exception:
                continue
        else:
            return {"error": f"SSH failed to {host} and {host_tailscale}", "success": False}

        try:
            # Write prompt to temp file on Lappy via SFTP
            prompt_path = "C:\\Users\\aaron\\doctor_prompt.txt"
            sftp = client.open_sftp()
            with sftp.file(prompt_path, "w") as f:
                f.write(prompt)
            sftp.close()

            # Run Claude Code headless
            cmd = (
                f'claude -p "Read {prompt_path} and fix the described issue. '
                f'Delete {prompt_path} when done." '
                f'--allowedTools "Bash" --output-format text'
            )
            logger.info(f"[doctor] Running Claude Code: {cmd[:120]}...")

            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            output = stdout.read().decode("utf-8", errors="replace")
            err_output = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()

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
    """Generate the diagnostic prompt for Claude Code with service-specific runbook."""
    failures = svc.consecutive_failures if svc else 1
    runbook = svc.runbook if svc and svc.runbook else "No specific runbook. Diagnose from first principles."

    return f"""You are Dr. Claude Code — infrastructure self-healing agent on Lappy (Windows 11).

## Symptom
Service: {service}
Error: {error}
Consecutive failures: {failures}

## Service-Specific Runbook
{runbook}

## Lappy Key Paths
- Media stack compose: D:\\docker\\media-stack\\docker-compose.yml
- SearXNG settings: D:\\docker\\media-stack\\searxng\\settings.yml
- SearXNG container: searxng-vpn (runs via Gluetun VPN network)
- Ollama: Windows service (ollama.exe serve), NOT Docker
- LiteLLM proxy: D:\\docker\\litellm\\ (if installed)
- Traefik: C:\\traefik\\
- Alphabetty: C:\\Users\\aaron\\Desktop\\alphabetty\\

## Key Commands (Windows)
- docker ps -a
- docker logs <container> --tail 50
- docker restart <container>
- cd D:\\docker\\media-stack && docker compose restart <service>
- netstat -ano | findstr :<port>
- tasklist | findstr <process>

## Rules
1. Diagnose the failure first (read logs, check container status)
2. Fix it (restart container, clear lock, fix config, etc.)
3. Verify the fix worked (re-check health)
4. Report what was wrong and what you did
5. Do NOT reboot. Do NOT stop unrelated services.
"""


# ─── Default Health Checks ───

async def _check_searxng():
    """Check SearXNG is responding."""
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{settings.searxng_url}/search?q=test&format=json")
            if r.status_code == 200:
                data = r.json()
                results = len(data.get("results", []))
                return True, ""
            return False, f"SearXNG returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"SearXNG unreachable: {e}"


async def _check_ollama():
    """Check Ollama is responding."""
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"http://{settings.ssh_host}:11434/api/tags")
            if r.status_code == 200:
                return True, ""
            return False, f"Ollama returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"Ollama unreachable: {e}"


async def _check_bonsai():
    """Check Bonsai image backend is responding."""
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.bonsai_url}/backends")
            if r.status_code == 200:
                return True, ""
            return False, f"Bonsai returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"Bonsai unreachable: {e}"


# ─── Register Default Services ───

def _init_defaults():
    """Register the default Lappy health checks with service-specific config."""
    register_service("searxng", _check_searxng,
        container_name="searxng-vpn",
        compose_dir="D:\\docker\\media-stack",
        compose_service="searxng-vpn",
        auto_heal=True,
        runbook="""SearXNG search engine running in Docker container 'searxng-vpn'.
Routes traffic through Gluetun VPN (shares network with gluetun container).
Common issues:
- Container crashed/exited → docker restart searxng-vpn
- All engines suspended → check settings at D:\\docker\\media-stack\\searxng\\settings.yml
  (timeouts too short, need retries)
- Gluetun VPN down → check gluetun container too, restart if needed
- Port 8888 not responding → container may be stuck, try docker compose down && up
Fix sequence: docker restart searxng-vpn → check logs → restart gluetun if needed → verify""")

    register_service("ollama", _check_ollama,
        container_name="",  # Not Docker — Windows process
        auto_heal=True,
        runbook="""Ollama LLM inference server. Runs as Windows process (NOT Docker).
Start with: ollama.exe serve
Common issues:
- Process crashed → restart with 'ollama.exe serve' (runs in background)
- GPU OOM → check with 'nvidia-smi', may need to reduce OLLAMA_MAX_LOADED_MODELS
- Model not found → 'ollama pull <model>'
- Port 11434 taken → 'netstat -ano | findstr 11434' to find conflicting process
Note: GPU env vars: OLLAMA_KV_CACHE_TYPE=q4_0, OLLAMA_FLASH_ATTENTION=1, OLLAMA_MAX_LOADED_MODELS=1""")

    register_service("bonsai", _check_bonsai,
        container_name="",
        auto_heal=False,
        runbook="""Bonsai Image backend on port 8000. Local image generation model.
Start: run D:\\Bonsai-Image-Demo\\start_bonsai.bat
API: POST /generate with {prompt, width, height, steps}
Returns raw PNG. First inference at new resolution ~60s (JIT compile), subsequent ~4-5s.
GPU: RTX 3060 Laptop 6GB. Safe resolutions: 512x512, 624x416, 416x624.
This is monitored but NOT auto-healed by Alphabetty.""")


_init_defaults()
