# LlmChatCompletionSummary イベント（デフォルト保存 30日）をモデル別トークン数メトリクスに変換し
# Dimensional Metrics として 13ヶ月保存する。
# summary() は sum / count / min / max を一度に生成するため、ダッシュボードで
# sum() を使って月次合計トークン数を集計できる。

resource "newrelic_events_to_metrics_rule" "llm_token_count" {
  account_id  = var.newrelic_account_id
  name        = "LLM Chat Completion Token Count by Model"
  description = "モデルごとのトークン使用量を Dimensional Metrics に変換（保存期間 13ヶ月）"
  nrql        = "SELECT summary(token_count) FROM LlmChatCompletionSummary FACET response.model"
}
