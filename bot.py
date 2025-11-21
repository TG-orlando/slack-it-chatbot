import os
import logging
import time
import re
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
import metrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

IT_CHANNEL_NAME = os.environ.get("IT_CHANNEL_NAME", "it")
BOT_NAME = "IT AI Support"

# Initialize scheduler for weekly reports
scheduler = BackgroundScheduler()

def schedule_weekly_report():
    """Schedule weekly report generation"""
    try:
        # Get channel ID for IT channel
        result = app.client.conversations_list()
        channels = result.get("channels", [])
        channel_id = None

        for channel in channels:
            if channel.get("name") == IT_CHANNEL_NAME:
                channel_id = channel.get("id")
                break

        if channel_id:
            metrics.generate_and_post_weekly_report(app.client, channel_id, post_to_slack=True)
        else:
            logger.error(f"Could not find channel: {IT_CHANNEL_NAME}")

    except Exception as e:
        logger.error(f"Error in scheduled report: {str(e)}")

# TheGuarantors IT Environment
THEGUARANTORS_TOOLS = """
**TheGuarantors IT Environment:**
- Email: Gmail (Google Workspace)
- Identity/SSO: Okta, Auth0, JumpCloud
- Security: SentinelOne, Cyberhaven, Duo, GreatHorn, Vanta, Arctic Wolf, Tenable
- Password Management: 1Password
- Device Management: Jamf
- VPN: AWS ClientVPN
- Collaboration: Slack, Zoom, Confluence, Loom
- Development: GitHub Enterprise, Cursor, Postman, Vercel
- Data: Snowflake, Databricks, DBT, Airbyte, Stitch, Datadog
- Project Management: Jira, Monday.com, Linear B, Airtable
- HR: Rippling, Lattice, WorkRamp
- Finance: Brex, Expensify, Carta, Chargebee
- Design: Figma, Adobe Creative Cloud, Canva
- Analytics: Mixpanel, Power BI, FullStory
- Customer Success: Zendesk, HubSpot, Gong
- Other: 1Password, Okta, AWS, Stripe, Salesforce, and 80+ other SaaS apps
"""

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

        # Handle thread replies (follow-up messages)
        if is_thread_reply:
            thread_ts = event.get("thread_ts")
            current_user = event.get("user")
            logger.info(f"Thread conversation from {current_user}: {user_message}")

            # Get thread history for context
            try:
                replies = client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=20
                )

                # Find the original ticket creator (first message in thread)
                original_creator = None
                assignee_mention = None
                assignee_user_id = None

                for msg in replies.get("messages", []):
                    # First non-bot message is the ticket creator
                    if not msg.get("bot_id") and original_creator is None:
                        original_creator = msg.get("user")

                    # Find assignee from Assist bot message
                    text = msg.get("text", "")
                    if "Assignee:" in text or "assignee" in text.lower():
                        user_match = re.search(r'<@(\w+)>', text)
                        if user_match:
                            assignee_user_id = user_match.group(1)
                            assignee_mention = f"<@{assignee_user_id}>"

                # Only respond to the ORIGINAL ticket creator, not assignee or other users
                if current_user != original_creator:
                    logger.info(f"Message from non-creator ({current_user}), not responding. Original creator: {original_creator}")
                    return

                # Check if this is a "thank you" / "got access" / completion message
                completion_keywords = ["thank you", "thanks", "got it", "works now", "working now",
                                      "all good", "all set", "have access", "got access", "resolved",
                                      "fixed", "working", "perfect", "great", "awesome", "appreciate",
                                      "sorted", "done", "completed", "solved"]

                is_completion = any(keyword in user_message.lower() for keyword in completion_keywords)

                if is_completion:
                    logger.info("Detected completion/thank you message from ticket creator")
                    say(
                        text="You're welcome! Glad we could help. If you need anything else, feel free to post a new message in this channel. Have a great day! 🎉",
                        thread_ts=thread_ts
                    )
                    return

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
                    "content": f"""You are IT AI Support, having a natural conversation with a TheGuarantors employee about their IT issue.

{THEGUARANTORS_TOOLS}

Your goals:
1. Have a helpful, friendly conversational dialogue (not robotic) - represent TheGuarantors' supportive culture
2. Ask clarifying questions to understand the problem better
3. Provide troubleshooting specific to TheGuarantors' tech stack (Okta, Gmail, Jamf, 1Password, AWS ClientVPN, etc.)
4. Remember what they've already tried (from conversation history)
5. If the issue persists after troubleshooting, or seems complex, or user is uncertain, ALWAYS suggest escalation
6. When escalating, say: "Let me escalate this to TheGuarantors IT team who can help you further."

**IMPORTANT RULES:**
- NEVER suggest: creating a ticket, emailing IT, reaching out, or contacting external support
- ONLY mention: TheGuarantors IT team (never "the IT team" - always "TheGuarantors IT team")
- For escalation: ONLY suggest using the thumbs down emoji (👎)

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

                # Always add follow-up options after troubleshooting
                if assignee_mention:
                    # Mention the assignee in escalation option
                    if "escalat" in chat_response.lower():
                        chat_response = chat_response.replace("the IT team", assignee_mention).replace("IT team", assignee_mention).replace("TheGuarantors IT team", assignee_mention)

                    chat_response += f"""

---
**Did this help resolve your issue?**
• ✅ If yes, let me know and I'll close this out!
• 💬 If not, tell me what's happening and I'll try another solution
• 👎 React with thumbs down to escalate to {assignee_mention}"""
                else:
                    chat_response += """

---
**Did this help resolve your issue?**
• ✅ If yes, let me know and I'll close this out!
• 💬 If not, tell me what's happening and I'll try another solution
• 👎 React with thumbs down to escalate to TheGuarantors IT team"""

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

        # Check if this is a change request (not a technical issue)
        change_keywords = ["change", "update my", "modify", "edit my", "adjust", "configure",
                          "set up", "setup", "install", "add me", "remove me", "switch",
                          "device", "settings", "preferences", "configuration"]
        is_change_request = any(keyword in user_message.lower() for keyword in change_keywords)

        # Get assignee from Assist message for change requests
        assignee_mention = None
        if is_change_request:
            time.sleep(3)  # Wait for Assist to post
            try:
                replies = client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=10
                )
                for message in replies.get("messages", []):
                    text = message.get("text", "")
                    if "Assignee:" in text or "assignee" in text.lower():
                        user_match = re.search(r'<@(\w+)>', text)
                        if user_match:
                            assignee_mention = f"<@{user_match.group(1)}>"
                        break
            except:
                pass

        # If it's a change request, provide simple acknowledgment
        if is_change_request and assignee_mention:
            say(
                text=f"Thank you! We have received your request. {assignee_mention} is working on this and will reach out shortly.",
                thread_ts=thread_ts
            )
            logger.info("Change request acknowledged")
            return
        elif is_change_request:
            say(
                text="Thank you! We have received your request. Our IT team is working on this and will reach out shortly.",
                thread_ts=thread_ts
            )
            logger.info("Change request acknowledged")
            return

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"""You are IT AI Support - the first responder for IT issues at TheGuarantors.

{THEGUARANTORS_TOOLS}

**Your Role:**
- You're helping TheGuarantors employees with IT issues related to our tech stack
- Be professional, friendly, and efficient - represent our supportive culture
- Know our environment: Gmail, Okta SSO, Jamf for devices, 1Password, AWS ClientVPN, etc.
- Follow TheGuarantors IT team's approach to troubleshooting{past_context}

**For ACCESS REQUESTS** (asking for access to apps like Snowflake, GitHub, Figma, Jira, etc.):
- Respond with: "Thank you for your access request! TheGuarantors IT team is provisioning your access and will follow up shortly."
- Do NOT suggest creating tickets, emailing, or reaching out
- Do NOT provide troubleshooting steps
- Examples: "I need Snowflake access", "Can I get GitHub Enterprise added?", "Need Figma license"

**For TECHNICAL ISSUES** (Level 1 troubleshooting):
- Acknowledge the issue professionally with TheGuarantors' friendly tone
- Provide troubleshooting specific to our tools (Okta for SSO issues, Jamf for Mac problems, 1Password for credentials, etc.)
- Reference similar past tickets if relevant
- For VPN: mention AWS ClientVPN
- For email: mention Gmail/Google Workspace
- Use bullet points for steps
- NEVER mention: creating tickets, emailing IT, reaching out, external support
- ONLY mention TheGuarantors IT team
- For escalation: ONLY say "React with 👎 to escalate to TheGuarantors IT team"

Keep responses clear, concise, and helpful. NEVER suggest creating tickets or emailing - only thumbs down emoji for escalation."""
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

        # Get assignee from Assist message for escalation option
        assignee_for_escalation = None
        try:
            replies = client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=10
            )
            for msg in replies.get("messages", []):
                text = msg.get("text", "")
                if "Assignee:" in text or "assignee" in text.lower():
                    user_match = re.search(r'<@(\w+)>', text)
                    if user_match:
                        assignee_for_escalation = f"<@{user_match.group(1)}>"
                    break
        except:
            pass

        # Add follow-up question with options
        if assignee_for_escalation:
            followup_text = f"""{ai_response}

---
**Did this help resolve your issue?**
• ✅ If yes, let me know and I'll close this out!
• 💬 If not, tell me what's happening and I'll try another solution
• 👎 React with thumbs down to escalate to {assignee_for_escalation}"""
        else:
            followup_text = f"""{ai_response}

---
**Did this help resolve your issue?**
• ✅ If yes, let me know and I'll close this out!
• 💬 If not, tell me what's happening and I'll try another solution
• 👎 React with thumbs down to escalate to TheGuarantors IT team"""

        say(
            text=followup_text,
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
            escalation_msg = f"🔴 **Issue needs escalation**\n\n<@{user}> indicated that the troubleshooting steps didn't resolve the issue.\n\nTheGuarantors IT team, this ticket needs further assistance."

        say(
            text=escalation_msg,
            thread_ts=thread_ts
        )

        logger.info("Escalation message sent")

    except Exception as e:
        logger.error(f"Error handling reaction: {str(e)}")

@app.event("app_mention")
def handle_mentions(event, say, client):
    user_id = event["user"]
    message_text = event.get("text", "").lower()

    # Check if user is requesting a report
    if "report" in message_text or "metrics" in message_text or "stats" in message_text:
        say(f"Generating weekly metrics report... This may take a moment.")

        # Get channel ID
        channel_id = event.get("channel")
        success = metrics.generate_and_post_weekly_report(client, channel_id, post_to_slack=True)

        if success:
            say(f"✅ Report generated and committed to GitHub!")
        else:
            say(f"❌ Error generating report. Check logs for details.")
    else:
        say(f"Hi <@{user_id}>! I'm monitoring all messages in the IT channel and will respond with helpful suggestions automatically. Just post your IT issue and I'll help troubleshoot!\n\n💡 **Tip:** Mention me with 'report' or 'metrics' to generate a weekly performance report.")

if __name__ == "__main__":
    # Schedule weekly reports (every Monday at 9 AM)
    scheduler.add_job(
        schedule_weekly_report,
        'cron',
        day_of_week='mon',
        hour=9,
        minute=0
    )
    scheduler.start()
    logger.info("Weekly report scheduler started (runs every Monday at 9 AM)")

    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    logger.info(f"{BOT_NAME} is starting...")
    handler.start()
