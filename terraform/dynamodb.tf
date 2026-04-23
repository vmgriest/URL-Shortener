resource "aws_dynamodb_table" "click_events" {
  name         = "${var.project_name}-clicks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "short_code"
  range_key    = "timestamp"

  attribute {
    name = "short_code"
    type = "S"
  }
  attribute {
    name = "timestamp"
    type = "S"
  }

  tags = { Name = "${var.project_name}-clicks" }
}
