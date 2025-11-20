import os
import logging
import subprocess
from datetime import datetime, timedelta
from collections import Counter
import re

logger = logging.getLogger(__name__)

def analyze_slack_history(client, channel_id, days=7):
    """Analyze Slack history for the past N days"""
    try:
        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        start_ts = start_time.timestamp()

        # Fetch messages from Slack
        result = client.conversations_history(
            channel=channel_id,
            oldest=str(start_ts),
            limit=1000
        )

        messages = result.get("messages", [])

        # Analyze messages
        bot_responses = 0
        user_tickets = 0
        escalations = 0
        issue_categories = []
        response_times = []
        threads_with_followup = 0

        # Track threads
        threads = {}

        for message in messages:
            # Skip bot messages for ticket counting
            if message.get("bot_id"):
                bot_responses += 1
                # Check if this is in a thread
                thread_ts = message.get("thread_ts")
                if thread_ts:
                    if thread_ts not in threads:
                        threads[thread_ts] = {"bot_responses": 0, "user_messages": 0, "escalated": False}
                    threads[thread_ts]["bot_responses"] += 1
            else:
                # User message - potential ticket
                if not message.get("thread_ts") or message.get("thread_ts") == message.get("ts"):
                    # Top-level message = new ticket
                    user_tickets += 1
                    text = message.get("text", "")
                    category = categorize_issue(text)
                    issue_categories.append(category)

                    # Store thread start time
                    thread_ts = message.get("ts")
                    threads[thread_ts] = {
                        "start_time": float(thread_ts),
                        "bot_responses": 0,
                        "user_messages": 1,
                        "escalated": False
                    }
                else:
                    # Follow-up message in thread
                    thread_ts = message.get("thread_ts")
                    if thread_ts in threads:
                        threads[thread_ts]["user_messages"] += 1

            # Check for escalation (thumbs down reaction or escalation message)
            reactions = message.get("reactions", [])
            for reaction in reactions:
                if reaction.get("name") in ["thumbsdown", "-1"]:
                    escalations += 1
                    thread_ts = message.get("thread_ts") or message.get("ts")
                    if thread_ts in threads:
                        threads[thread_ts]["escalated"] = True

            # Check for escalation in text
            text = message.get("text", "").lower()
            if "issue needs escalation" in text or "🔴" in text:
                escalations += 1
                thread_ts = message.get("thread_ts") or message.get("ts")
                if thread_ts in threads:
                    threads[thread_ts]["escalated"] = True

        # Calculate metrics from threads
        for thread_ts, thread_data in threads.items():
            if thread_data.get("user_messages", 0) > 1:
                threads_with_followup += 1

        # Calculate resolution rate (tickets without escalation)
        resolved = user_tickets - escalations
        resolution_rate = (resolved / user_tickets * 100) if user_tickets > 0 else 0

        # Count common issues
        issue_counts = Counter(issue_categories)

        # Estimated response time (bot responds in ~2-3 seconds, manual would be 15-30 min)
        avg_bot_response_time = 3  # seconds
        avg_manual_response_time = 20 * 60  # 20 minutes in seconds
        time_saved_seconds = user_tickets * (avg_manual_response_time - avg_bot_response_time)
        time_saved_hours = time_saved_seconds / 3600

        return {
            "period_days": days,
            "start_date": start_time,
            "end_date": end_time,
            "total_tickets": user_tickets,
            "bot_responses": bot_responses,
            "escalations": escalations,
            "resolved": resolved,
            "resolution_rate": resolution_rate,
            "common_issues": issue_counts.most_common(5),
            "avg_response_time": avg_bot_response_time,
            "time_saved_hours": time_saved_hours,
            "tickets_with_followup": threads_with_followup,
            "followup_rate": (threads_with_followup / user_tickets * 100) if user_tickets > 0 else 0
        }

    except Exception as e:
        logger.error(f"Error analyzing Slack history: {str(e)}")
        return None

def categorize_issue(issue_text):
    """Categorize the issue based on keywords"""
    text = issue_text.lower()

    if any(word in text for word in ["vpn", "connect", "network", "wifi", "internet", "connection"]):
        return "Network/VPN"
    elif any(word in text for word in ["okta", "login", "password", "access", "sso", "authenticate", "2fa"]):
        return "Authentication/Access"
    elif any(word in text for word in ["email", "gmail", "outlook", "calendar", "mail"]):
        return "Email"
    elif any(word in text for word in ["slow", "freeze", "crash", "performance", "hang", "lag"]):
        return "Performance"
    elif any(word in text for word in ["install", "update", "software", "app", "application"]):
        return "Software/Apps"
    elif any(word in text for word in ["device", "laptop", "computer", "mac", "jamf", "hardware"]):
        return "Device/Hardware"
    elif any(word in text for word in ["access to", "need access", "request access", "permission"]):
        return "Access Request"
    elif any(word in text for word in ["snowflake", "github", "figma", "jira", "aws"]):
        return "SaaS Access"
    else:
        return "Other"

def generate_weekly_report_markdown(metrics):
    """Generate markdown report from metrics"""
    if not metrics:
        return "# Error\n\nUnable to generate report - no metrics data available."

    start = metrics["start_date"].strftime("%B %d, %Y")
    end = metrics["end_date"].strftime("%B %d, %Y")

    report = f"""# 📊 IT AI Support - Weekly Report
**Week of {start} - {end}**

---

## 📈 Performance Summary

| Metric | Value |
|--------|-------|
| **Total Tickets Handled** | {metrics['total_tickets']} |
| **Bot Responses Sent** | {metrics['bot_responses']} |
| **Average Response Time** | {metrics['avg_response_time']} seconds |
| **Resolution Rate** | {metrics['resolution_rate']:.1f}% |
| **Tickets Escalated** | {metrics['escalations']} |
| **Tickets Resolved by Bot** | {metrics['resolved']} |

---

## ⏱️ Impact & Time Savings

### Response Time Improvement
- **IT AI Support:** ~{metrics['avg_response_time']} seconds (instant)
- **Manual Response (baseline):** ~15-30 minutes
- **Improvement:** **99.7% faster** initial response

### Time Saved
- **Estimated Time Saved This Week:** **{metrics['time_saved_hours']:.1f} hours**
- **Assumptions:**
  - Bot handles first response in {metrics['avg_response_time']}s
  - Manual first response would take ~20 minutes
  - {metrics['resolved']} tickets resolved without IT team involvement

### Cost Efficiency
- **Tickets Handled 24/7:** {metrics['total_tickets']}
- **IT Team Time Freed Up:** ~{(metrics['time_saved_hours']):.1f} hours
- **Estimated Value:** ~${(metrics['time_saved_hours'] * 50):.2f} (at $50/hour IT labor)

---

## 🔥 Most Common Issues

"""

    for i, (category, count) in enumerate(metrics['common_issues'], 1):
        percentage = (count / metrics['total_tickets'] * 100) if metrics['total_tickets'] > 0 else 0
        report += f"{i}. **{category}** - {count} tickets ({percentage:.1f}%)\n"

    report += f"""
---

## 📊 Ticket Breakdown

### Resolution Status
- ✅ **Resolved by Bot:** {metrics['resolved']} ({metrics['resolution_rate']:.1f}%)
- 🔴 **Escalated to IT Team:** {metrics['escalations']} ({(metrics['escalations']/metrics['total_tickets']*100) if metrics['total_tickets'] > 0 else 0:.1f}%)
- 💬 **Required Follow-up:** {metrics['tickets_with_followup']} ({metrics['followup_rate']:.1f}%)

### Engagement
- **Average Conversations per Ticket:** {(metrics['bot_responses'] / metrics['total_tickets']):.1f} if metrics['total_tickets'] > 0 else 0
- **Users Asking Follow-up Questions:** {metrics['followup_rate']:.1f}%

---

## 💡 Key Insights

"""

    # Add insights based on data
    if metrics['resolution_rate'] > 60:
        report += "- ✅ **High resolution rate** - Bot is effectively handling most issues without escalation\n"
    elif metrics['resolution_rate'] < 40:
        report += "- ⚠️ **Lower resolution rate** - Consider reviewing prompts or adding more context\n"
    else:
        report += "- 📊 **Moderate resolution rate** - Bot is learning and improving\n"

    if metrics['avg_response_time'] < 5:
        report += "- ⚡ **Excellent response time** - Users receiving instant help\n"

    if metrics['common_issues']:
        top_issue, top_count = metrics['common_issues'][0]
        report += f"- 🔍 **Focus area:** {top_issue} ({top_count} tickets) - Consider creating KB article or improving responses\n"

    if metrics['escalations'] < metrics['total_tickets'] * 0.3:
        report += f"- 🎯 **Low escalation rate** - Bot is successfully resolving most issues independently\n"

    if metrics['followup_rate'] > 50:
        report += f"- 💬 **High engagement** - Users are actively conversing with the bot\n"

    report += f"""
---

## 🎯 Recommendations

"""

    # Add recommendations based on metrics
    if metrics['common_issues'] and metrics['common_issues'][0][1] > metrics['total_tickets'] * 0.3:
        top_issue = metrics['common_issues'][0][0]
        report += f"1. **Create documentation** for {top_issue} issues (most common this week)\n"

    if metrics['escalations'] > metrics['total_tickets'] * 0.4:
        report += "2. **Review escalated tickets** to identify patterns and improve bot responses\n"

    if metrics['resolution_rate'] > 70:
        report += "3. **Excellent performance** - Continue monitoring and maintain current approach\n"

    report += f"""
---

## 📅 Historical Comparison

*Track trends week-over-week by reviewing previous reports in this directory*

---

**Report Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Data Source:** TheGuarantors Slack #it channel
**Analysis Period:** {metrics['period_days']} days
"""

    return report

def commit_report_to_github(report_content, filename):
    """Commit the report to GitHub"""
    try:
        # Create reports directory if it doesn't exist
        os.makedirs("reports", exist_ok=True)

        # Write report file
        filepath = f"reports/{filename}"
        with open(filepath, 'w') as f:
            f.write(report_content)

        # Git commands
        subprocess.run(["git", "config", "user.email", "bot@theguarantors.com"], check=True)
        subprocess.run(["git", "config", "user.name", "IT AI Support"], check=True)
        subprocess.run(["git", "add", filepath], check=True)
        subprocess.run(["git", "commit", "-m", f"Add weekly metrics report: {filename}"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)

        logger.info(f"Successfully committed report to GitHub: {filepath}")
        return True

    except Exception as e:
        logger.error(f"Error committing report to GitHub: {str(e)}")
        return False

def generate_and_post_weekly_report(client, channel_id, post_to_slack=True):
    """Generate weekly report and commit to GitHub"""
    try:
        logger.info("Generating weekly metrics report...")

        # Analyze Slack data
        metrics = analyze_slack_history(client, channel_id, days=7)

        if not metrics:
            logger.error("Failed to analyze Slack history")
            return False

        # Generate markdown report
        report_md = generate_weekly_report_markdown(metrics)

        # Create filename with date
        filename = f"weekly-report-{datetime.now().strftime('%Y-%m-%d')}.md"

        # Commit to GitHub
        success = commit_report_to_github(report_md, filename)

        if success and post_to_slack:
            # Post summary to Slack
            summary = f"""📊 **Weekly Metrics Report Generated!**

✅ **Report:** `reports/{filename}`
📈 **Tickets Handled:** {metrics['total_tickets']}
⏱️ **Time Saved:** {metrics['time_saved_hours']:.1f} hours
🎯 **Resolution Rate:** {metrics['resolution_rate']:.1f}%

View full report: https://github.com/TG-orlando/slack-it-chatbot/blob/main/reports/{filename}
"""

            try:
                client.chat_postMessage(
                    channel=channel_id,
                    text=summary
                )
                logger.info("Posted report summary to Slack")
            except Exception as e:
                logger.error(f"Error posting to Slack: {str(e)}")

        return success

    except Exception as e:
        logger.error(f"Error in generate_and_post_weekly_report: {str(e)}")
        return False
