# LlmChatCompletionMessage の content フィールドに含まれるメールアドレスをマスキングする。
#
# 実装方針:
#   newrelic_obfuscation_expression でマスキングパターンを定義し、
#   newrelic_obfuscation_rule で対象フィールドへの適用ルールを設定する。
#
# 注意事項:
#   これらのリソースは New Relic の Log Obfuscation API を使用している。
#   ドキュメントではログ向けとして説明されているが、NRDB イベントへの適用も
#   NR の内部パイプラインで処理されるため動作する可能性がある。
#   terraform plan / apply 後に New Relic UI > AI monitoring > Drop Filters で
#   マスキングが実際に機能しているかを確認すること。
#
#   動作しない場合のフォールバック（手動対応）:
#   New Relic UI > AI monitoring > Drop Filters > Create filter
#     - Filter type: Obfuscate
#     - Event type: LlmChatCompletionMessage
#     - Attribute: content
#     - Regex: [a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}

resource "newrelic_obfuscation_expression" "email_address" {
  account_id  = var.newrelic_account_id
  name        = "email-address-pattern"
  description = "メールアドレスの検出パターン"
  regex        = "[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}"
}

resource "newrelic_obfuscation_rule" "mask_email_in_llm_content" {
  account_id  = var.newrelic_account_id
  name        = "AI Monitoring - Mask email in LLM content"
  description = "LlmChatCompletionMessage の content フィールドのメールアドレスを [MASKED] に置換"
  enabled     = true
  filter      = "eventType() = 'LlmChatCompletionMessage'"

  action {
    attribute     = ["content"]
    expression_id = newrelic_obfuscation_expression.email_address.id
    method        = "MASK"
  }
}
