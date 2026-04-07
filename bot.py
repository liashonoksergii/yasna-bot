import os
import logging
import base64
import json
import re
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN")
SYSTEM_PROMPT_BASE = os.environ.get("SYSTEM_PROMPT", "Ты помощник для семьи. Общайся только на русском языке.")

def get_system_prompt():
    today = datetime.now().strftime("%d.%m.%Y")
    year = datetime.now().year
    return (
        f"{SYSTEM_PROMPT_BASE}\n\n"
        f"Сегодняшняя дата: {today}. Текущий год: {year}. "
        f"Всегда используй правильный год при создании событий.\n\n"
        f"Если в сообщении или на фото есть дата приёма, встречи, мероприятия или любого другого события — "
        f"автоматически создай JSON для календаря БЕЗ дополнительных вопросов. "
        f"Отвечай JSON объектами (можно несколько) без лишнего текста вокруг них:\n"
        f'{{"action":"add_event","title":"название","date":"YYYY-MM-DD","time":"HH:MM","duration":60,"description":"описание"}}'
    )

conversation_history = {}

def get_flow():
    return Flow.from_client_config(
        {
            "installed": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"]
            }
        },
        scopes=["https://www.googleapis.com/auth/calendar"],
        redirect_uri="urn:ietf:wg:oauth:2.0:oob"
    )

def get_auth_url():
    flow = get_flow()
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url

def get_credentials():
    if not GOOGLE_TOKEN:
        return None
    try:
        token_json = base64.b64decode(GOOGLE_TOKEN).decode("utf-8")
        token_data = json.loads(token_json)
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        creds.refresh(Request())
        return creds
    except Exception as e:
        logger.error(f"Credentials error: {e}")
        return None

def add_to_calendar(event_data):
    creds = get_credentials()
    if not creds:
        return False
    try:
        service = build("calendar", "v3", credentials=creds)
        start_dt = f"{event_data['date']}T{event_data['time']}:00"
        event = {
            "summary": event_data["title"],
            "description": event_data.get("description", ""),
            "start": {"dateTime": start_dt, "timeZone": "Europe/Berlin"},
            "end": {"dateTime": start_dt, "timeZone": "Europe/Berlin"}
        }
        service.events().insert(calendarId="primary", body=event).execute()
        return True
    except Exception as e:
        logger.error(f"Calendar error: {e}")
        return False

def extract_json_objects(text):
    results = []
    for match in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        try:
            obj = json.loads(match.group())
            if obj.get("action") == "add_event":
                results.append(obj)
        except:
            pass
    return results

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if ALLOWED_USER_ID != 0 and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Доступ запрещён.")
        return

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    if update.message.photo:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_data = base64.standard_b64encode(file_bytes).decode("utf-8")
        caption = update.message.caption or "Проанализируй это фото. Если есть дата события — сразу создай JSON для календаря."
        conversation_history[user_id].append({
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                {"type": "text", "text": caption}
            ]
        })
    else:
        text = update.message.text

        if text.startswith("AUTH:"):
            auth_code = text.replace("AUTH:", "").strip()
            try:
                flow = get_flow()
                flow.fetch_token(code=auth_code)
                creds = flow.credentials
                token_data = json.dumps({
                    "token": creds.token,
                    "refresh_token": creds.refresh_token
                })
                token_b64 = base64.b64encode(token_data.encode("utf-8")).decode("utf-8")
                await update.message.reply_text(
                    f"✅ Google Calendar подключён!\n\n"
                    f"Обнови GOOGLE_TOKEN в Railway Variables:\n\n"
                    f"{token_b64}"
                )
                return
            except Exception as e:
                await update.message.reply_text(f"Ошибка авторизации: {e}")
                return

        conversation_history[user_id].append({"role": "user", "content": text})

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=get_system_prompt(),
        messages=conversation_history[user_id]
    )

    assistant_message = response.content[0].text
    conversation_history[user_id].append({"role": "assistant", "content": assistant_message})

    events = extract_json_objects(assistant_message)

    if events:
        if not GOOGLE_TOKEN:
            auth_url = get_auth_url()
            await update.message.reply_text(
                f"Для добавления в календарь нужна авторизация.\n\n"
                f"1. Открой ссылку:\n{auth_url}\n\n"
                f"2. Войди в Google и скопируй код\n"
                f"3. Отправь мне: AUTH:код"
            )
        else:
            added = []
            for event_data in events:
                success = add_to_calendar(event_data)
                if success:
                    added.append(f"✅ {event_data['title']} — {event_data['date']} в {event_data['time']}")
                else:
                    added.append(f"❌ Ошибка: {event_data['title']}")
            await update.message.reply_text("\n".join(added))
    else:
        await update.message.reply_text(assistant_message)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
