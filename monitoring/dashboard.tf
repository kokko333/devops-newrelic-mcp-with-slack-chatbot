resource "newrelic_one_dashboard_json" "ai_monitoring" {
  json = templatefile("${path.module}/dashboard.json.tftpl", {
    account_id = var.newrelic_account_id
  })
}
