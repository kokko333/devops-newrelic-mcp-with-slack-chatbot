import asyncio
import logging

import anthropic

from nr_mcp_client import NRMCPClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """\
あなたは New Relic の監視データに精通した AI アシスタントです。
New Relic MCP ツールを使用してシステムの監視データを取得し、分かりやすく日本語で回答してください。

New Relic アカウント ID: {account_id}

回答時のガイドライン:
- 数値データは単位を含めて具体的に提示する
- 問題が見つかった場合は原因の推測と推奨アクションを提示する
- データが取得できない場合や質問が監視範囲外の場合は正直にその旨を伝える
- NRQL クエリを実行する際は上記アカウント ID を使用する
"""

_MAX_TOOL_ITERATIONS = 10  # 無限ループ防止


class ClaudeClient:
    """Amazon Bedrock 経由で Claude を呼び出し、NR MCP ツールを使用して回答を生成する。"""

    def __init__(
        self,
        model_id: str,
        nr_mcp_client: NRMCPClient,
        account_id: str,
        aws_region: str,
    ) -> None:
        self._model_id = model_id
        self._nr_mcp = nr_mcp_client
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(account_id=account_id)
        self._bedrock = anthropic.AsyncAnthropicBedrock(aws_region=aws_region)

    async def generate_response(self, messages: list[dict]) -> str:
        """会話履歴（現在のユーザーメッセージを含む）を受け取り、Claude の最終回答を返す。

        Claude がツール使用を要求した場合は NR MCP を呼び出すループを回す。
        """
        tools = self._nr_mcp.get_claude_tool_definitions()
        current_messages = list(messages)

        for iteration in range(_MAX_TOOL_ITERATIONS):
            response = await self._bedrock.messages.create(
                model=self._model_id,
                max_tokens=4096,
                system=self._system_prompt,
                tools=tools,
                messages=current_messages,
            )
            logger.info(
                "Claude response: stop_reason=%s iteration=%d",
                response.stop_reason,
                iteration,
            )

            if response.stop_reason == "end_turn":
                text_blocks = [
                    b.text for b in response.content if hasattr(b, "text")
                ]
                return "\n".join(text_blocks)

            if response.stop_reason == "tool_use":
                current_messages.append(
                    {"role": "assistant", "content": response.content}
                )
                tool_results = await self._execute_tools(response.content)
                current_messages.append({"role": "user", "content": tool_results})
            else:
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                break

        return "回答を生成できませんでした。しばらく待ってから再度お試しください。"

    async def _execute_tools(self, content_blocks: list) -> list[dict]:
        """tool_use ブロックを並列実行し、tool_result リストを返す。"""
        tool_use_blocks = [b for b in content_blocks if b.type == "tool_use"]

        results = await asyncio.gather(
            *[
                self._nr_mcp.call_tool(block.name, block.input)
                for block in tool_use_blocks
            ],
            return_exceptions=True,
        )

        tool_results = []
        for block, result in zip(tool_use_blocks, results):
            if isinstance(result, Exception):
                logger.error("Tool %s failed: %s", block.name, result)
                content = f"ツール呼び出しエラー: {result}"
            else:
                content = str(result)

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                }
            )

        return tool_results
