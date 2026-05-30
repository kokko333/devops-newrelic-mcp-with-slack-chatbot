import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MCP_ENDPOINT = "https://mcp.newrelic.com/mcp"
_PROTOCOL_VERSION = "2024-11-05"


class NRMCPClient:
    """New Relic MCP サーバー（Streamable HTTP / JSON-RPC 2.0）の HTTP クライアント。

    Anthropic SDK の MCP コネクタは Authorization: Bearer 形式のみ対応だが、
    NR MCP は Api-Key ヘッダーを要求するため、直接 HTTP で実装する。
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._session_id: str | None = None
        self._request_id = 0
        self.tools: list[dict] = []
        self._client = httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """MCP セッションを確立し、ツール定義を取得してキャッシュする。"""
        logger.info("NR MCP: initializing session...")
        await self._send_initialize()
        await self._send_initialized_notification()
        await self._load_tools()
        logger.info("NR MCP: ready. tools=%s", [t["name"] for t in self.tools])

    def get_claude_tool_definitions(self) -> list[dict]:
        """MCP ツール定義を Claude API の tools フォーマットに変換して返す。"""
        return [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get(
                    "inputSchema", {"type": "object", "properties": {}}
                ),
            }
            for tool in self.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """指定ツールを NR MCP 経由で実行し、テキスト結果を返す。

        セッション切れ（401/403/404）を検知した場合は自動的に再初期化する。
        """
        try:
            return await self._call_tool_once(tool_name, arguments)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 404) and self._session_id:
                logger.warning("NR MCP: session expired, reinitializing...")
                self._session_id = None
                await self.initialize()
                return await self._call_tool_once(tool_name, arguments)
            raise

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Api-Key": self._api_key,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, payload: dict) -> dict:
        response = await self._client.post(
            _MCP_ENDPOINT, json=payload, headers=self._headers()
        )
        response.raise_for_status()
        # セッション ID を初回レスポンスヘッダーから取得
        if not self._session_id:
            self._session_id = (
                response.headers.get("Mcp-Session-Id")
                or response.headers.get("X-Session-Id")
            )
        return response.json()

    async def _send_initialize(self) -> None:
        await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "newrelic-slack-bot", "version": "1.0.0"},
                },
            }
        )

    async def _send_initialized_notification(self) -> None:
        # 通知は fire-and-forget（202 / 200 / 204 どれでも OK）
        try:
            response = await self._client.post(
                _MCP_ENDPOINT,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._headers(),
                timeout=10.0,
            )
            if response.status_code not in (200, 202, 204):
                response.raise_for_status()
        except Exception as exc:
            logger.debug("NR MCP: initialized notification error (non-fatal): %s", exc)

    async def _load_tools(self) -> None:
        data = await self._post(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list"}
        )
        self.tools = data.get("result", {}).get("tools", [])

    async def _call_tool_once(self, tool_name: str, arguments: dict) -> str:
        logger.info("NR MCP tool call: %s  args=%s", tool_name, arguments)
        data = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
        result = data.get("result", {})
        content: list[dict] = result.get("content", [])
        texts = [item["text"] for item in content if item.get("type") == "text"]
        return "\n".join(texts) if texts else str(result)
