import logging
import re

import newrelic.agent

from claude_client import ClaudeClient
from conversation_manager import ConversationManager

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


class SlackHandler:
    """Slack の app_mention および DM メッセージを受け取り、Claude の回答を返す。"""

    def __init__(
        self,
        app,
        claude_client: ClaudeClient,
        conversation_manager: ConversationManager,
    ) -> None:
        self._claude = claude_client
        self._conv = conversation_manager
        app.event("app_mention")(self._handle_mention)
        app.event("message")(self._handle_dm)

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    async def _handle_mention(self, event: dict, say, client) -> None:
        await self._process(event, say, client)

    async def _handle_dm(self, event: dict, say, client) -> None:
        # DM のみ対応。ボット自身の投稿とサブタイプ付きイベント（join 等）は無視する。
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        await self._process(event, say, client)

    # ------------------------------------------------------------------ #
    # Core processing
    # ------------------------------------------------------------------ #

    async def _process(self, event: dict, say, client) -> None:
        channel: str = event["channel"]
        # スレッド内メッセージなら thread_ts、新規メッセージなら ts をスレッド ID とする
        thread_ts: str = event.get("thread_ts") or event["ts"]
        user_text: str = _MENTION_RE.sub("", event.get("text", "")).strip()

        if not user_text:
            return

        # APM は通常 HTTP リクエストをトランザクション単位として自動検出するが、
        # このボットは Socket Mode（WebSocket 常時接続）で動作するため HTTP エンドポイントを持たない。
        # BackgroundTask として明示的にマークすることで APM がトランザクションを認識し、
        # Bedrock 呼び出し等の外部サービス計測や AI Monitoring の LLM スパンが記録される。
        with newrelic.agent.BackgroundTask(
            newrelic.agent.application(),
            name="slack-message-handler",
            group="Slack",
        ):
            # 処理中プレースホルダーを投稿
            placeholder = await say(text=":mag: 調査中...", thread_ts=thread_ts)

            try:
                self._conv.add_user_message(thread_ts, user_text)
                messages = self._conv.get_messages(thread_ts)

                response_text = await self._claude.generate_response(messages)
                self._conv.add_assistant_message(thread_ts, response_text)

                await client.chat_update(
                    channel=channel,
                    ts=placeholder["ts"],
                    text=response_text,
                )
            except Exception as exc:
                logger.exception("Error processing message: %s", exc)
                # 会話履歴から未完了のユーザーメッセージを除去して一貫性を保つ
                history = self._conv.get_messages(thread_ts)
                if history and history[-1]["role"] == "user":
                    self._conv._histories[thread_ts].pop()

                await client.chat_update(
                    channel=channel,
                    ts=placeholder["ts"],
                    text=f":x: エラーが発生しました。しばらく待ってから再度お試しください。\n```{exc}```",
                )
