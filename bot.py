import os
import logging
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

IT_CHANNEL_NAME = os.environ.get("IT_CHANNEL_NAME", "it")

@app.event("message")
def handle_message_events(event, say, client):
    try:
        if event.get("subtype"):
            return

        channel_id = event.get("channel")
        channel_info = client.conversations_info(channel=channel_id)
        channel_name = channel_info["channel"]["name"]

        if channel_name != IT_CHANNEL_NAME:
            return

        user_message = event.get("text", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        logger.info(f"Processing IT ticket: {user_message}")

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """You are an IT support first responder bot. Your job is to:
1. Acknowledge the user's issue professionally
2. Provide step-by-step troubleshooting instructions
3. Ask relevant diagnostic questions if needed
4. Suggest common fixes based on the problem described

Keep responses clear, concise, and helpful. Use bullet points for steps.
If the issue is complex, provide initial troubleshooting steps and mention that IT staff will follow up if needed."""
                },
                {
                    "role": "user",
                    "content": f"IT Issue: {user_message}"
                }
            ],
            temperature=0.7,
            max_tokens=800
        )

        ai_response = response.choices[0].message.content

        say(
            text=ai_response,
            thread_ts=thread_ts
        )

        logger.info("Response sent successfully")

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        say(
            text="I encountered an error processing your request. An IT team member will assist you shortly.",
            thread_ts=event.get("thread_ts") or event.get("ts")
        )

@app.event("app_mention")
def handle_mentions(event, say):
    user_id = event["user"]
    say(f"Hi <@{user_id}>! I'm monitoring all messages in the IT channel and will respond with helpful suggestions automatically. Just post your IT issue and I'll help troubleshoot!")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    logger.info("IT Support Bot is starting...")
    handler.start()
