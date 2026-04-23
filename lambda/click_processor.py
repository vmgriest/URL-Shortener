import json
import os
import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])


def handler(event, context):
    for record in event["Records"]:
        body = json.loads(record["body"])
        table.put_item(Item={
            "short_code": body["short_code"],
            "timestamp":  body["timestamp"],
            "user_agent": body.get("user_agent", ""),
            "ip":         body.get("ip", ""),
        })
