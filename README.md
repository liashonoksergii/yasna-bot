# 🤖 Familien Helfer — AI Telegram Bot

An intelligent Telegram bot that helps families manage paperwork, 
translate German documents, and organize schedules via Google Calendar.

## ✨ Features

- 🇩🇪 Translates German documents and photos to Russian
- 📷 Reads handwritten notes via Claude Vision AI
- 📅 Adds events directly to Google Calendar
- 🔒 Access restricted to authorized user only
- 💬 Remembers conversation context

## 🛠 Tech Stack

- Python 3.13
- python-telegram-bot 21.9
- Anthropic Claude API
- Google Calendar API + OAuth 2.0
- Railway (cloud hosting)

## 🚀 How to Run
```bash
git clone https://github.com/liashonoksergii/yasna-bot.git
cd yasna-bot
pip install -r requirements.txt
python bot.py
```

## 🔐 Environment Variables
```
TELEGRAM_BOT_TOKEN=
ANTHROPIC_API_KEY=
ALLOWED_USER_ID=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

## 👨‍💻 Author

Sergii Liashonok — studying AI Automation at IT Career Hub  
📍 Heidelberg, Germany
