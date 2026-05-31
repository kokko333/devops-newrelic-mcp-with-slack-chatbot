import newrelic.agent

import asyncio
import logging
import os
from dotenv import load_dotenv

# フレームワーク（slack_bolt）のインポートより前に initialize() を呼ぶ必要があるため、
# stdlib/dotenv のインポート直後に記述している。
load_dotenv()
newrelic.agent.initialize()

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from nr_mcp_client import NRMCPClient
from claude_client import ClaudeClient
from conversation_manager import ConversationManager
from slack_handler import SlackHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _require(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"必須の環境変数 {key!r} が設定されていません。")
    return value


async def main() -> None:
    slack_bot_token = _require("SLACK_BOT_TOKEN")
    slack_app_token = _require("SLACK_APP_TOKEN")
    nr_api_key = _require("NEW_RELIC_API_KEY")
    nr_account_id = os.environ.get("NEW_RELIC_ACCOUNT_ID", "")
    bedrock_model_id = _require("BEDROCK_MODEL_ID")
    aws_region = _require("AWS_DEFAULT_REGION")

    # ---- New Relic MCP -------------------------------------------------
    nr_mcp = NRMCPClient(api_key=nr_api_key)
    await nr_mcp.initialize()

    # ---- Claude (Bedrock) ----------------------------------------------
    claude = ClaudeClient(
        model_id=bedrock_model_id,
        nr_mcp_client=nr_mcp,
        account_id=nr_account_id,
        aws_region=aws_region,
    )

    # ---- Slack ---------------------------------------------------------
    app = AsyncApp(token=slack_bot_token)
    SlackHandler(app, claude, ConversationManager())

    handler = AsyncSocketModeHandler(app, slack_app_token)
    logger.info("Bot starting in Socket Mode (region=%s, model=%s)", aws_region, bedrock_model_id)

    try:
        await handler.start_async()
    finally:
        await nr_mcp.close()


if __name__ == "__main__":
    asyncio.run(main())
