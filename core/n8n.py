"""n8n REST API client — async httpx wrapper for workflow automation."""

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.n8n_api_key:
        headers["X-N8N-API-KEY"] = settings.n8n_api_key
    return headers


def _base() -> str:
    return settings.n8n_url.rstrip("/")


async def _request(method: str, path: str, **kwargs) -> dict[str, Any]:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=_headers(), **kwargs)
            resp.raise_for_status()
            if resp.status_code == 204:
                return {"ok": True}
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"n8n API error: {e.response.status_code} {e.response.text[:200]}")
        return {"error": f"n8n {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        logger.error(f"n8n request failed: {e}")
        return {"error": str(e)}


async def health() -> dict[str, Any]:
    return await _request("GET", "/healthz")


async def list_workflows() -> dict[str, Any]:
    return await _request("GET", "/api/v1/workflows")


async def get_workflow(workflow_id: str) -> dict[str, Any]:
    return await _request("GET", f"/api/v1/workflows/{workflow_id}")


async def create_workflow(name: str, nodes: list[dict] | None = None,
                          connections: dict | None = None,
                          active: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "active": active}
    if nodes is not None:
        payload["nodes"] = nodes
    if connections is not None:
        payload["connections"] = connections
    return await _request("POST", "/api/v1/workflows", json=payload)


async def update_workflow(workflow_id: str, **fields) -> dict[str, Any]:
    return await _request("PATCH", f"/api/v1/workflows/{workflow_id}", json=fields)


async def delete_workflow(workflow_id: str) -> dict[str, Any]:
    return await _request("DELETE", f"/api/v1/workflows/{workflow_id}")


async def activate_workflow(workflow_id: str) -> dict[str, Any]:
    return await update_workflow(workflow_id, active=True)


async def deactivate_workflow(workflow_id: str) -> dict[str, Any]:
    return await update_workflow(workflow_id, active=False)


async def execute_workflow(workflow_id: str, data: dict | None = None) -> dict[str, Any]:
    payload = {"workflowData": data} if data else {}
    return await _request("POST", f"/api/v1/workflows/{workflow_id}/run", json=payload)


async def list_executions(workflow_id: str | None = None,
                          limit: int = 20) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if workflow_id:
        params["workflowId"] = workflow_id
    return await _request("GET", "/api/v1/executions", params=params)
