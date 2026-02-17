import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

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
Ты помогаешь: переводить письма с немецкого, анализировать документы, напоминать о важных датах и задачах."""

conversation_history = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if ALLOWED_USER_ID != 0 and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Доступ запрещён.")
        return

    user_message = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({
        "role": "user",
        "content": user_message
    })

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=conversation_history[user_id]
    )

    assistant_message = response.content[0].text

    conversation_history[user_id].append({
        "role": "assistant",
        "content": assistant_message
    })

    await update.message.reply_text(assistant_message)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
```

И в `requirements.txt` должно быть:
```
python-telegram-bot==20.7
anthropic==0.18.1
