import json

standups = []

def lambda_handler(event, context):

    action = event.get("action", "")

    if action == "add_update":

        update = event.get("update", "")

        standups.append(update)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": f"Added update: {update}"
            })
        }

    elif action == "weekly_summary":

        summary = "\n".join(standups)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "summary": summary
            })
        }

    return {
        "statusCode": 400,
        "body": json.dumps({
            "error": "Unknown action"
        })
    }