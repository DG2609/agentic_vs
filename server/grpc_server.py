"""
gRPC Tool Service — Typed tool execution layer.
Runs on port 50051, called internally by the Socket.IO server.
"""
import sys
import os
import json
import time
import asyncio
import logging
from concurrent import futures

import grpc

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.proto import tools_pb2
from server.proto import tools_pb2_grpc
from agent.graph import ALL_TOOLS
import config

logger = logging.getLogger(__name__)

# ── Tool Registry ───────────────────────────────────────────

TOOL_CATEGORIES = {
    'code_search': 'search', 'grep_search': 'search', 'batch_read': 'file',
    'semantic_search': 'search', 'index_codebase': 'search',
    'file_read': 'file', 'file_write': 'file', 'file_edit': 'file',
    'multi_edit': 'file', 'glob_search': 'search', 'file_list': 'file',
    'terminal_exec': 'code', 'code_analyze': 'code',
    'webfetch': 'web', 'web_search': 'web',
    'lsp_definition': 'lsp', 'lsp_references': 'lsp', 'lsp_hover': 'lsp',
    'lsp_symbols': 'lsp', 'lsp_diagnostics': 'lsp',
    'todo_read': 'task', 'todo_write': 'task',
    'question': 'task', 'plan_enter': 'task', 'plan_exit': 'task',
    'task_explore': 'agent', 'task_general': 'agent',
}

# Build tool function map
TOOL_MAP = {}
for tool in ALL_TOOLS:
    TOOL_MAP[tool.name] = tool


class ToolServiceServicer(tools_pb2_grpc.ToolServiceServicer):
    """gRPC service implementing typed tool execution."""

    async def ExecuteTool(self, request, context):
        """Execute a single tool by name with JSON arguments."""
        tool_name = request.tool_name
        
        if tool_name not in TOOL_MAP:
            return tools_pb2.ToolResponse(
                tool_name=tool_name,
                status="error",
                result=f"Unknown tool: {tool_name}",
                execution_time_ms=0,
            )

        tool = TOOL_MAP[tool_name]
        start = time.perf_counter()

        try:
            args = json.loads(request.arguments_json) if request.arguments_json else {}
            # Inject workspace if tool needs it
            if 'workspace' not in args and request.workspace:
                args['workspace'] = request.workspace

            result = await tool.ainvoke(args)
            elapsed_ms = (time.perf_counter() - start) * 1000

            result_str = str(result)
            return tools_pb2.ToolResponse(
                tool_name=tool_name,
                status="completed",
                result=result_str[:5000],
                execution_time_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Tool {tool_name} failed: {e}")
            return tools_pb2.ToolResponse(
                tool_name=tool_name,
                status="error",
                result=str(e),
                execution_time_ms=elapsed_ms,
            )

    async def ListTools(self, request, context):
        """List all available tools."""
        tools = []
        for tool in ALL_TOOLS:
            info = tools_pb2.ToolInfo(
                name=tool.name,
                description=tool.description or "",
                category=TOOL_CATEGORIES.get(tool.name, "other"),
            )
            # Extract params from tool schema if available
            if hasattr(tool, 'args_schema') and tool.args_schema:
                schema = tool.args_schema.model_json_schema()
                props = schema.get('properties', {})
                required = set(schema.get('required', []))
                for pname, pinfo in props.items():
                    info.params.append(tools_pb2.ToolParam(
                        name=pname,
                        type=pinfo.get('type', 'string'),
                        description=pinfo.get('description', ''),
                        required=pname in required,
                    ))
            tools.append(info)

        return tools_pb2.ToolList(tools=tools)

    async def GetToolInfo(self, request, context):
        """Get detailed info for a specific tool."""
        tool_name = request.tool_name
        if tool_name not in TOOL_MAP:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Tool not found: {tool_name}")
            return tools_pb2.ToolInfo()

        tool = TOOL_MAP[tool_name]
        info = tools_pb2.ToolInfo(
            name=tool.name,
            description=tool.description or "",
            category=TOOL_CATEGORIES.get(tool.name, "other"),
        )

        if hasattr(tool, 'args_schema') and tool.args_schema:
            schema = tool.args_schema.model_json_schema()
            props = schema.get('properties', {})
            required = set(schema.get('required', []))
            for pname, pinfo in props.items():
                info.params.append(tools_pb2.ToolParam(
                    name=pname,
                    type=pinfo.get('type', 'string'),
                    description=pinfo.get('description', ''),
                    required=pname in required,
                ))
        return info


async def serve(port: int = 50051):
    """Start the gRPC server."""
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    tools_pb2_grpc.add_ToolServiceServicer_to_server(ToolServiceServicer(), server)
    server.add_insecure_port(f'[::]:{port}')
    await server.start()
    logger.info(f"🔧 gRPC ToolService running on port {port}")
    await server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(serve())
