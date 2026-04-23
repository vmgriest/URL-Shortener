resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = 7
}

resource "aws_ecs_cluster" "main" {
  name = var.project_name
  tags = { Name = var.project_name }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.project_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = var.project_name
    image     = "${aws_ecr_repository.app.repository_url}:${var.app_image_tag}"
    essential = true

    portMappings = [{ containerPort = 8000 }]

    environment = [
      { name = "DATABASE_URL",           value = "postgresql+asyncpg://postgres:${var.db_password}@${aws_db_instance.postgres.address}:5432/urlshortener" },
      { name = "REDIS_URL",              value = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0" },
      { name = "BASE_URL",               value = "http://${aws_lb.app.dns_name}" },
      { name = "SQS_QUEUE_URL",          value = aws_sqs_queue.click_events.url },
      { name = "DYNAMODB_TABLE",         value = aws_dynamodb_table.click_events.name },
      { name = "AWS_DEFAULT_REGION",     value = var.aws_region },
      { name = "RATE_LIMIT_CAPACITY",    value = "20" },
      { name = "RATE_LIMIT_REFILL_RATE", value = "10" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name            = var.project_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.project_name
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}
