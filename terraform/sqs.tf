resource "aws_sqs_queue" "click_events" {
  name                       = "${var.project_name}-click-events"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  tags                       = { Name = "${var.project_name}-click-events" }
}
