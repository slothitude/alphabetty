"""Workflow automation endpoints — n8n integration."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from core.auth import get_current_user
from models.user import User
from core import n8n

router = APIRouter(tags=["workflows"])


class WorkflowCreate(BaseModel):
    name: str
    nodes: Optional[list[dict]] = None
    connections: Optional[dict] = None
    active: bool = False


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    nodes: Optional[list[dict]] = None
    connections: Optional[dict] = None
    active: Optional[bool] = None


class WorkflowRun(BaseModel):
    data: Optional[dict] = None


@router.get("/workflows/health")
async def workflow_health(user: User = Depends(get_current_user)):
    return await n8n.health()


@router.get("/workflows")
async def list_workflows(user: User = Depends(get_current_user)):
    return await n8n.list_workflows()


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, user: User = Depends(get_current_user)):
    return await n8n.get_workflow(workflow_id)


@router.post("/workflows")
async def create_workflow(body: WorkflowCreate, user: User = Depends(get_current_user)):
    return await n8n.create_workflow(
        name=body.name, nodes=body.nodes,
        connections=body.connections, active=body.active,
    )


@router.patch("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowUpdate,
                          user: User = Depends(get_current_user)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return await n8n.update_workflow(workflow_id, **fields)


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, user: User = Depends(get_current_user)):
    return await n8n.delete_workflow(workflow_id)


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: str, body: WorkflowRun | None = None,
                       user: User = Depends(get_current_user)):
    return await n8n.execute_workflow(workflow_id, data=body.data if body else None)


@router.get("/workflows/{workflow_id}/executions")
async def list_executions(workflow_id: str, limit: int = 20,
                          user: User = Depends(get_current_user)):
    return await n8n.list_executions(workflow_id, limit=limit)
