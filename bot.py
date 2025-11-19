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

def get_similar_past_tickets(client, channel_id, user_message, limit=5):
    """Search past IT tickets for similar issues and resolutions"""
    try:
        # Get recent messages from the channel (last 100 messages)
        history = client.conversations_history(
            channel=channel_id,
            limit=100
        )

        # Look for threads that might contain resolutions
        similar_tickets = []
        for message in history.get("messages", []):
            msg_text = message.get("text", "").lower()

            # Check if this ticket is similar to the current issue
            # Simple keyword matching (could be enhanced with embeddings)
            user_keywords = set(user_message.lower().split())
            msg_keywords = set(msg_text.split())

            # If there's significant overlap, consider it similar
            overlap = len(user_keywords & msg_keywords)
            if overlap >= 2 and message.get("thread_ts"):
                # Get the thread to see resolution
                try:
                    thread = client.conversations_replies(
                        channel=channel_id,
                        ts=message.get("ts"),
                        limit=10
                    )

                    thread_messages = thread.get("messages", [])
                    if len(thread_messages) > 2:  # Has conversation/resolution
                        similar_tickets.append({
                            "issue": msg_text[:200],
                            "thread": thread_messages[:5]
                        })
                except:
                    pass

            if len(similar_tickets) >= limit:
                break

        return similar_tickets
    except Exception as e:
        logger.error(f"Error fetching past tickets: {str(e)}")
        return []

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

        # Handle thread replies (follow-up messages) - respond to ALL user messages
        if is_thread_reply:
            thread_ts = event.get("thread_ts")
            logger.info(f"Thread conversation: {user_message}")

            # Get thread history for context
            try:
                replies = client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=20
                )

                # Find assignee from Assist bot message
                assignee_mention = None
                for msg in replies.get("messages", []):
                    text = msg.get("text", "")
                    if "Assignee:" in text or "assignee" in text.lower():
                        # Extract user mention if present
                        user_match = re.search(r'<@(\w+)>', text)
                        if user_match:
                            assignee_mention = f"<@{user_match.group(1)}>"
                        break

                # Build conversation context
                context_messages = []
                for msg in replies.get("messages", []):
                    role = "assistant" if msg.get("bot_id") else "user"
                    msg_text = msg.get("text", "")
                    # Clean up the message (remove reaction instructions)
                    if "**Did this help?**" not in msg_text:
                        context_messages.append({
                            "role": role,
                            "content": msg_text
                        })

                # Add system message with escalation instructions
                context_messages.insert(0, {
                    "role": "system",
                    "content": f"""You are TheGuarantors IT Support Bot, having a natural conversation with a TheGuarantors employee about their IT issue.

Your goals:
1. Have a helpful, friendly conversational dialogue (not robotic) - represent TheGuarantors' supportive culture
2. Ask clarifying questions to understand the problem better
3. Provide step-by-step guidance based on what they tell you
4. Remember what they've already tried (from conversation history)
5. If the issue persists after troubleshooting, or seems complex, or user is uncertain, ALWAYS suggest escalation
6. When escalating, say: "I'm going to escalate this to the IT team who can help you further."

Be conversational, empathetic, and helpful. Keep responses concise (3-5 sentences max).
After 2-3 failed attempts or when user seems stuck, always escalate."""
                })

                # Add current user message
                context_messages.append({
                    "role": "user",
                    "content": user_message
                })

                # Get ChatGPT response
                response = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=context_messages,
                    temperature=0.7,
                    max_tokens=400
                )

                chat_response = response.choices[0].message.content

                # Detect if user is stuck or uncertain
                stuck_keywords = ["didn't work", "doesn't work", "not working", "still", "same",
                                "don't know", "not sure", "uncertain", "confused", "tried everything"]
                user_is_stuck = any(keyword in user_message.lower() for keyword in stuck_keywords)

                # If escalating or user is stuck, add feedback option and mention assignee
                if "escalat" in chat_response.lower() or user_is_stuck:
                    if assignee_mention:
                        # Mention the assignee in escalation
                        if "escalat" in chat_response.lower():
                            chat_response = chat_response.replace("the IT team", assignee_mention).replace("IT team", assignee_mention)

                        # Add escalation option with thumbs down
                        chat_response += f"\n\n---\n**Need help from the team?**\nReact with 👎 to this message and I'll escalate to {assignee_mention} immediately."
                    else:
                        chat_response += "\n\n---\n**Need help from the team?**\nReact with 👎 to this message and I'll escalate to the IT team immediately."

                say(
                    text=chat_response,
                    thread_ts=thread_ts
                )

                logger.info("Conversation response sent")

            except Exception as e:
                logger.error(f"Error in conversation: {str(e)}")

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

        # Get similar past tickets for context
        past_tickets = get_similar_past_tickets(client, channel_id, user_message, limit=3)
        past_context = ""
        if past_tickets:
            past_context = "\n\n**Past Similar Tickets:**\n"
            for i, ticket in enumerate(past_tickets, 1):
                past_context += f"{i}. Issue: {ticket['issue'][:100]}...\n"

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"""You are TheGuarantors IT Support Bot - the first responder for IT issues at TheGuarantors.

**Company Context:**
- You're helping TheGuarantors employees with their IT needs
- Be professional, friendly, and efficient
- Follow TheGuarantors IT team's approach to troubleshooting{past_context}

**For ACCESS REQUESTS** (asking for access to apps, software, systems, accounts, permissions):
- Respond with: "Thank you for your request! Our IT team is working on getting you access and will follow up shortly."
- Do NOT provide troubleshooting steps
- Examples: "I need access to Salesforce", "Can I get Slack admin access?", "Need Zoom license"

**For TECHNICAL ISSUES** (Level 1 troubleshooting):
- Acknowledge the issue professionally with TheGuarantors' friendly tone
- Provide clear step-by-step troubleshooting instructions
- Reference similar past tickets if relevant
- Ask relevant diagnostic questions if needed
- Use bullet points for steps

Keep all responses clear, concise, and helpful. If the issue is complex, provide initial troubleshooting steps and mention that the IT team will follow up if needed."""
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

        # Don't add automatic feedback - let conversation flow naturally
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
