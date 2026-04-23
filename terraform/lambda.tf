data "archive_file" "click_processor" {
  type        = "zip"
  source_file = "${path.module}/../lambda/click_processor.py"
  output_path = "${path.module}/../lambda/click_processor.zip"
}

resource "aws_lambda_function" "click_processor" {
  function_name    = "${var.project_name}-click-processor"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "click_processor.handler"
  filename         = data.archive_file.click_processor.output_path
  source_code_hash = data.archive_file.click_processor.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.click_events.name
    }
  }

  tags = { Name = "${var.project_name}-click-processor" }
}

resource "aws_lambda_event_source_mapping" "sqs" {
  event_source_arn = aws_sqs_queue.click_events.arn
  function_name    = aws_lambda_function.click_processor.arn
  batch_size       = 10
  enabled          = true
}
