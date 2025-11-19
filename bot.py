import os
import logging
import time
import re
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
        # Ignore messages with subtypes (edits, deletes, etc)
        if event.get("subtype"):
            return

        # Ignore bot messages (including our own)
        if event.get("bot_id") or event.get("bot_profile"):
            return

        # Check if this is a thread reply or new message
        is_thread_reply = event.get("thread_ts") is not None and event.get("thread_ts") != event.get("ts")

        channel_id = event.get("channel")
        channel_info = client.conversations_info(channel=channel_id)
        channel_name = channel_info["channel"]["name"]

        if channel_name != IT_CHANNEL_NAME:
            return

        user_message = event.get("text", "")

        # Handle thread replies (follow-up messages)
        if is_thread_reply:
            thread_ts = event.get("thread_ts")
            logger.info(f"Thread reply detected: {user_message}")

            # Check if user needs more help using keywords
            help_keywords = ["need help", "more help", "didn't work", "doesn't work", "not working",
                           "still not", "still broken", "same problem", "same issue", "not fixed",
                           "tried that", "already tried", "escalate", "still having"]

            needs_help = any(keyword in user_message.lower() for keyword in help_keywords)

            if needs_help:
                logger.info("User needs additional help, providing follow-up response")

                # Get thread history for context
                try:
                    replies = client.conversations_replies(
                        channel=channel_id,
                        ts=thread_ts,
                        limit=10
                    )

                    # Build context from previous messages
                    context_messages = []
                    for msg in replies.get("messages", [])[:5]:  # Last 5 messages for context
                        role = "assistant" if msg.get("bot_id") else "user"
                        context_messages.append({
                            "role": role,
                            "content": msg.get("text", "")
                        })

                    # Add system message
                    context_messages.insert(0, {
                        "role": "system",
                        "content": """You are an IT support bot helping with a follow-up question. The user tried the previous troubleshooting steps but still needs help. Provide:
1. Alternative solutions or more advanced troubleshooting
2. Ask clarifying questions about what they've tried
3. If the issue seems complex, suggest escalating to IT team
Keep responses concise and helpful."""
                    })

                    # Add current message
                    context_messages.append({
                        "role": "user",
                        "content": user_message
                    })

                    # Get follow-up response from ChatGPT
                    response = openai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=context_messages,
                        temperature=0.3,
                        max_tokens=500
                    )

                    follow_up_response = response.choices[0].message.content

                    say(
                        text=f"{follow_up_response}\n\n💡 **Tip:** You can also react with 👎 on my previous message to escalate to the assigned IT team member.",
                        thread_ts=thread_ts
                    )

                    logger.info("Follow-up response sent")

                except Exception as e:
                    logger.error(f"Error providing follow-up: {str(e)}")

            return  # Don't continue to new ticket processing

        # Handle new top-level messages
        thread_ts = event.get("ts")

        logger.info(f"New IT ticket detected: {user_message}")
        logger.info("Waiting for Assist bot to respond first...")

        # Wait for Assist bot to respond (check every 2 seconds, max 20 seconds)
        assist_responded = False
        max_attempts = 10  # 10 attempts x 2 seconds = 20 seconds max

        for attempt in range(max_attempts):
            time.sleep(2)

            # Check if there are any replies in the thread
            try:
                replies = client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=10
                )

                # Look for Assist bot's response
                for message in replies.get("messages", []):
                    if message.get("bot_id") or message.get("app_id"):
                        # Found a bot response, assume it's Assist
                        logger.info(f"Assist bot responded (attempt {attempt + 1})")
                        assist_responded = True
                        break

                if assist_responded:
                    break

            except Exception as e:
                logger.error(f"Error checking for Assist response: {str(e)}")
                break

        if not assist_responded:
            logger.warning("Assist bot didn't respond within 20 seconds, responding anyway")

        logger.info(f"Processing IT ticket: {user_message}")

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """You are an IT support first responder bot. You need to be smart about categorizing requests:

**For ACCESS REQUESTS** (asking for access to apps, software, systems, accounts, permissions):
- Respond with: "Thank you for your request. We are working on getting you access and will follow up shortly."
- Do NOT provide troubleshooting steps
- Examples: "I need access to Salesforce", "Can I get Slack admin access?", "Need Zoom license"

**For TECHNICAL ISSUES** (Level 1 troubleshooting - problems with existing systems):
- Acknowledge the issue professionally
- Provide clear step-by-step troubleshooting instructions
- Ask relevant diagnostic questions if needed
- Use bullet points for steps
- Examples: "WiFi not connecting", "Printer won't print", "Can't login to email", "Computer is slow"

Keep all responses clear, concise, and helpful. If the issue is complex, provide initial troubleshooting steps and mention that IT staff will follow up if needed."""
                },
                {
                    "role": "user",
                    "content": f"IT Request: {user_message}"
                }
            ],
            temperature=0.3,
            max_tokens=800
        )

        ai_response = response.choices[0].message.content

        # Add feedback instructions
        feedback_text = f"{ai_response}\n\n---\n**Did this help?**\n• React with :thumbsup: if the issue is resolved\n• React with :thumbsdown: if you need further assistance (I'll escalate to the assigned IT team member)"

        say(
            text=feedback_text,
            thread_ts=thread_ts
        )

        logger.info("Response sent successfully")

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        say(
            text="I encountered an error processing your request. An IT team member will assist you shortly.",
            thread_ts=event.get("thread_ts") or event.get("ts")
        )

@app.event("reaction_added")
def handle_reaction(event, client, say):
    try:
        reaction = event.get("reaction")
        item = event.get("item", {})
        channel = item.get("channel")
        message_ts = item.get("ts")
        user = event.get("user")

        # Only handle thumbs down reactions
        if reaction not in ["-1", "thumbsdown"]:
            return

        logger.info(f"Thumbs down reaction detected from user {user}")

        # Get the thread messages to find the Assist bot message
        thread_ts = message_ts
        replies = client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=20
        )

        # Find the Assist bot message with assignee info
        assignee_name = None
        for message in replies.get("messages", []):
            text = message.get("text", "")
            # Look for "Assignee:" pattern in Assist bot message
            if "Assignee:" in text or "assignee" in text.lower():
                # Try to extract the assignee name
                # Pattern to match "Assignee: Name" or links to users
                match = re.search(r'Assignee:\s*([^\n]+)', text)
                if match:
                    assignee_text = match.group(1).strip()
                    # Extract user mention if present
                    user_match = re.search(r'<@(\w+)>', assignee_text)
                    if user_match:
                        assignee_name = f"<@{user_match.group(1)}>"
                    else:
                        # Try to find a name (first word after "Assignee:")
                        name_match = re.search(r'Assignee:\s*(\S+\s+\S+)', text)
                        if name_match:
                            assignee_name = name_match.group(1).strip()
                    break

        # Post escalation message
        if assignee_name:
            escalation_msg = f"🔴 **Issue needs escalation**\n\n<@{user}> indicated that the troubleshooting steps didn't resolve the issue.\n\n{assignee_name}, this ticket needs your attention."
        else:
            escalation_msg = f"🔴 **Issue needs escalation**\n\n<@{user}> indicated that the troubleshooting steps didn't resolve the issue.\n\nIT team, this ticket needs further assistance."

        say(
            text=escalation_msg,
            thread_ts=thread_ts
        )

        logger.info("Escalation message sent")

    except Exception as e:
        logger.error(f"Error handling reaction: {str(e)}")

@app.event("app_mention")
def handle_mentions(event, say):
    user_id = event["user"]
    say(f"Hi <@{user_id}>! I'm monitoring all messages in the IT channel and will respond with helpful suggestions automatically. Just post your IT issue and I'll help troubleshoot!")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    logger.info("IT Support Bot is starting...")
    handler.start()
