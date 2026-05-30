from collections import defaultdict


class ConversationManager:
    """スレッド単位で会話履歴をインメモリ管理する。"""

    def __init__(self, max_turns: int = 10) -> None:
        # user + assistant で 1 turn なので上限は turns * 2 メッセージ
        self._max_messages = max_turns * 2
        self._histories: dict[str, list[dict]] = defaultdict(list)

    def add_user_message(self, thread_id: str, text: str) -> None:
        self._histories[thread_id].append({"role": "user", "content": text})
        self._trim(thread_id)

    def add_assistant_message(self, thread_id: str, text: str) -> None:
        self._histories[thread_id].append({"role": "assistant", "content": text})
        self._trim(thread_id)

    def get_messages(self, thread_id: str) -> list[dict]:
        return list(self._histories[thread_id])

    def _trim(self, thread_id: str) -> None:
        history = self._histories[thread_id]
        if len(history) > self._max_messages:
            self._histories[thread_id] = history[-self._max_messages :]
