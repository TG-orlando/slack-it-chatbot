# Slack IT Support ChatBot

AI-powered first response chatbot for your Slack #it channel using ChatGPT. Automatically responds to IT questions with helpful troubleshooting steps.

## Features

- Monitors all messages in your #it channel
- Responds in threads to keep the channel organized
- Provides step-by-step troubleshooting using ChatGPT
- Suggests common fixes for IT issues
- Professional and helpful responses

## Quick Deploy to Railway (Free)

### Prerequisites
1. OpenAI API Key - Get one at https://platform.openai.com/api-keys
2. Slack Workspace Admin Access

### Step 1: Create Slack App

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name it "IT Support Bot" and select your workspace
4. Click "Create App"

### Step 2: Configure Slack App

1. **Enable Socket Mode:**
   - Go to "Socket Mode" in the left sidebar
   - Toggle "Enable Socket Mode" to ON
   - Name the token "IT Bot Socket" and click "Generate"
   - **COPY THE TOKEN** (starts with `xapp-`) - you'll need this as `SLACK_APP_TOKEN`

2. **Subscribe to Events:**
   - Go to "Event Subscriptions" in the left sidebar
   - Toggle "Enable Events" to ON
   - Under "Subscribe to bot events", click "Add Bot User Event"
   - Add these events:
     - `message.channels`
     - `app_mention`
   - Click "Save Changes"

3. **Add Bot Scopes:**
   - Go to "OAuth & Permissions" in the left sidebar
   - Scroll to "Scopes" → "Bot Token Scopes"
   - Add these scopes:
     - `chat:write`
     - `channels:history`
     - `channels:read`
     - `app_mentions:read`

4. **Install to Workspace:**
   - Scroll to top of "OAuth & Permissions" page
   - Click "Install to Workspace"
   - Click "Allow"
   - **COPY THE BOT TOKEN** (starts with `xoxb-`) - you'll need this as `SLACK_BOT_TOKEN`

5. **Add Bot to #it Channel:**
   - Go to your #it channel in Slack
   - Type `/invite @IT Support Bot` (or whatever you named it)

### Step 3: Deploy to Railway

1. Go to https://railway.app and sign up/login with GitHub
2. Click "New Project" → "Deploy from GitHub repo"
3. Select this repository: `slack-it-chatbot`
4. Click "Add Variables" and add these:
   - `SLACK_BOT_TOKEN`: Your xoxb- token from Step 2.4
   - `SLACK_APP_TOKEN`: Your xapp- token from Step 2.1
   - `OPENAI_API_KEY`: Your OpenAI API key
   - `IT_CHANNEL_NAME`: `it` (or your channel name without #)
5. Click "Deploy"

That's it! Your bot will start running on Railway's free tier and respond to all messages in your #it channel.

## Testing

Post a message in your #it channel like:
```
My laptop won't connect to WiFi
```

The bot should respond in a thread with troubleshooting steps!

## Local Development

```bash
# Clone the repo
git clone https://github.com/TG-orlando/slack-it-chatbot.git
cd slack-it-chatbot

# Install dependencies
pip install -r requirements.txt

# Copy .env.example to .env and fill in your tokens
cp .env.example .env

# Run the bot
python bot.py
```

## Troubleshooting

**Bot not responding?**
- Make sure the bot is invited to the #it channel
- Check Railway logs for errors
- Verify all environment variables are set correctly
- Ensure Socket Mode is enabled in Slack App settings

**OpenAI API errors?**
- Verify your OpenAI API key is valid
- Check you have credits in your OpenAI account

## Cost

- Railway: Free tier (500 hours/month - more than enough)
- OpenAI: ~$0.01-0.05 per response (using gpt-4o-mini)
- Slack: Free

## Support

For issues, check the Railway logs or create an issue in this repository.
