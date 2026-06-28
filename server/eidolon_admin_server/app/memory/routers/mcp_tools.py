"""List MCP tools exposed by a user's agent_runner."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..mcp_client import list_tools
from ..schemas import McpToolOut, McpToolsResponse

router = APIRouter()


@router.get("/mcp/tools", response_model=McpToolsResponse)
async def mcp_tools(memory_realm_id: str = Query(...)) -> McpToolsResponse:
    raw = await list_tools(memory_realm_id)
    tools = [McpToolOut.model_validate(t) for t in raw]
    return McpToolsResponse(tools=tools, count=len(tools))
