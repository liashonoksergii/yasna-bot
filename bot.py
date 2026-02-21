import os
import logging
import base64
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import anthropic
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

SYSTEM_PROMPT = """Ты — помощник для семьи, которая заботится о ребёнке по имени Ясна (Yasna Liashonok, 11 лет).
У Ясны аутизм и эпилепсия. Она принимает Lamotrigin 200mg утром и 200mg вечером ежедневно.
Школа: Graf von Gallen, Гейдельберг. Перевозчик: R+R Tours.

Расписание Ясны:
- Пн-Ср: автобус в 8:20, домой в 15:30
- Чт: автобус в 8:20, уроки до 13:15, Lebenshilfe 13:15-16:00 (Kim), забрать в 16:30
- Пт: автобус в 8:20, уроки до 11:50, Lebenshilfe 11:50-15:30 (Kim), забрать в 15:30

Важные контакты:
- SPZ Dr. Bendl: +49 6221 56-4837, claudia.bendl@med.uni-heidelberg.de
- Jobcenter Frau Ersoy: 01602397112
- Школа (больничные): Krankmeldung@Galen-schule.de
- Lebenshilfe: Kim (четверг и пятница)

Ты общаешься ТОЛЬКО на русском языке.
Ты помогаешь: переводить письма с немецкого, анализировать документы и фото записок, напоминать о важных датах и задачах.

Если в сообщении есть дата и время события — извлеки их и предложи добавить в Google Calendar.
Отвечай в формате JSON только когда нужно добавить событие:
{"action": "add_event", "title": "название", "date": "YYYY-MM-DD", "time": "HH:MM", "duration": 60, "description": "описание"}
В остальных случаях отвечай обычным текстом."""

conversation_history = {}
user_tokens = {}

def get_auth_url():
    flow = Flow.from_client_config(
        {
            "installed": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"]
            }
        },
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url, flow

def add_to_calendar(token_info, event_data):
    creds = Credentials(
        token=token_info["token"],
        refresh_token=token_info["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET
    )
    service = build("calendar", "v3", credentials=creds)
    
    start_dt = f"{event_data['date']}T{event_data['time']}:00"
    
    event = {
        "summary": event_data["title"],
        "description": event_data.get("description", ""),
        "start": {"dateTime": start_dt, "timeZone": "Europe/Berlin"},
        "end": {"dateTime": start_dt, "timeZone": "Europe/Berlin"}
    }
    
    service.events().insert(calendarId="primary", body=event).execute()

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
                _, flow = get_auth_url()
                flow.fetch_token(code=auth_code)
                creds = flow.credentials
                user_tokens[user_id] = {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token
                }
                await update.message.reply_text("✅ Google Calendar подключён! Теперь я могу добавлять события.")
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
     clean = assistant_message
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()
        event_data = json.loads(clean)
        if event_data.get("action") == "add_event":
            if user_id not in user_tokens:
                auth_url, _ = get_auth_url()
                await update.message.reply_text(
                    f"Для добавления в календарь нужна авторизация.\n\n1. Открой эту ссылку:\n{auth_url}\n\n2. Войди в Google и скопируй код\n3. Отправь мне: AUTH:код"
                )
            else:
                add_to_calendar(user_tokens[user_id], event_data)
                await update.message.reply_text(f"✅ Добавлено в календарь: {event_data['title']} — {event_data['date']} в {event_data['time']}")
            return
    except json.JSONDecodeError:
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
