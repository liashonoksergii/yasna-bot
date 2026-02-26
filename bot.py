import os
import logging
import base64
import json
from datetime import datetime, timedelta
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
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "Ты помощник для семьи. Общайся только на русском языке. Если нужно добавить событие в календарь — отвечай ТОЛЬКО валидным JSON: {\"action\":\"add_event\",\"title\":\"название\",\"date\":\"YYYY-MM-DD\",\"time\":\"HH:MM\",\"duration\":60,\"description\":\"описание\"}")

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
    # ── 1. Проверяем что переменная вообще есть
    if not GOOGLE_TOKEN:
        logger.warning("GOOGLE_TOKEN не задан в переменных Railway")
        return None

    try:
        token_json = base64.b64decode(GOOGLE_TOKEN).decode("utf-8")
        token_data = json.loads(token_json)
        logger.info(f"Token data keys: {list(token_data.keys())}")  # для отладки
    except Exception as e:
        logger.error(f"Не удалось декодировать GOOGLE_TOKEN: {e}")
        return None

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        logger.error("refresh_token отсутствует в GOOGLE_TOKEN")
        return None

    try:
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )

        # ── 2. Обновляем токен только если он истёк или невалидный
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                logger.info("Токен истёк, обновляем...")
                creds.refresh(Request())
                logger.info("Токен успешно обновлён")
            else:
                logger.error("Токен недействителен и не может быть обновлён")
                return None

        return creds

    except Exception as e:
        logger.error(f"Ошибка при создании/обновлении credentials: {e}")
        return None

def add_to_calendar(event_data):
    creds = get_credentials()
    if not creds:
        logger.error("Не удалось получить credentials для Calendar")
        return False
    try:
        service = build("calendar", "v3", credentials=creds)

        start_dt = f"{event_data['date']}T{event_data['time']}:00"
        duration = event_data.get("duration", 60)

        # ── 3. Правильно считаем время окончания
        start_time = datetime.fromisoformat(start_dt)
        end_time = start_time + timedelta(minutes=duration)
        end_dt = end_time.strftime("%Y-%m-%dT%H:%M:%S")

        # ── 4. Напоминания: за 1 день (1440 мин) и за 2 часа (120 мин)
        event = {
            "summary": event_data["title"],
            "description": event_data.get("description", ""),
            "start": {"dateTime": start_dt, "timeZone": "Europe/Berlin"},
            "end": {"dateTime": end_dt, "timeZone": "Europe/Berlin"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 1440},  # за 1 день
                    {"method": "popup", "minutes": 120},   # за 2 часа
                ]
            }
        }

        result = service.events().insert(calendarId="primary", body=event).execute()
        logger.info(f"Событие создано: {result.get('htmlLink')}")
        return True
    except Exception as e:
        logger.error(f"Ошибка Calendar API: {e}")
        return False

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
        caption = update.message.caption or "Переведи и проанализируй эту записку"
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
                    f"`{token_b64}`"
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
        system=SYSTEM_PROMPT,
        messages=conversation_history[user_id]
    )

    assistant_message = response.content[0].text
    conversation_history[user_id].append({"role": "assistant", "content": assistant_message})

    try:
        event_data = json.loads(assistant_message.strip())
        if event_data.get("action") == "add_event":
            if not GOOGLE_TOKEN:
                auth_url = get_auth_url()
                await update.message.reply_text(
                    f"Для добавления в календарь нужна авторизация.\n\n"
                    f"1. Открой ссылку:\n{auth_url}\n\n"
                    f"2. Войди в Google и скопируй код\n"
                    f"3. Отправь мне: AUTH:код"
                )
            else:
                success = add_to_calendar(event_data)
                if success:
                    await update.message.reply_text(
                        f"✅ Добавлено в календарь!\n\n"
                        f"📅 {event_data['title']}\n"
                        f"🗓 {event_data['date']} в {event_data['time']}\n"
                        f"⏰ Напоминания: за 1 день и за 2 часа"
                    )
                else:
                    await update.message.reply_text(
                        "❌ Ошибка при добавлении в календарь.\n"
                        "Проверь логи в Railway → View logs"
                    )
            return
    except (json.JSONDecodeError, KeyError):
        pass

    await update.message.reply_text(assistant_message)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
