import os
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, redirect, session, jsonify, url_for
from flask_cors import CORS
import requests

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-change-in-production')
CORS(app)

# Конфигурация (задайте эти переменные в Railway)
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
BASE_URL = os.getenv('BASE_URL', 'https://telegramcallbot-production.up.railway.app')

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Хранилище пользователей (в продакшене использовать базу данных)
users_storage = {}

def get_flow():
    """Создание OAuth Flow"""
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{BASE_URL}/auth/google/callback"]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/auth/google/callback"
    )
    return flow

def send_telegram_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Сообщение отправлено в чат {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return False

def get_calendar_events(credentials_dict):
    """Получение событий из Google Calendar"""
    try:
        credentials = Credentials(
            token=credentials_dict['token'],
            refresh_token=credentials_dict.get('refresh_token'),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET
        )

        service = build('calendar', 'v3', credentials=credentials)

        now = datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + timedelta(days=7)).isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=20,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        logger.info(f"Получено {len(events)} событий")
        return events
    except Exception as e:
        logger.error(f"Ошибка получения событий: {e}")
        return []

def check_upcoming_events():
    """Проверка предстоящих событий и отправка напоминаний"""
    logger.info("Проверка предстоящих событий...")

    for user_id, user_data in users_storage.items():
        if 'credentials' not in user_data or 'chat_id' not in user_data:
            continue

        try:
            events = get_calendar_events(user_data['credentials'])
            now = datetime.utcnow()

            for event in events:
                start = event.get('start', {})
                start_time = start.get('dateTime', start.get('date'))

                if 'T' in start_time:
                    event_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    event_time_naive = event_time.replace(tzinfo=None)
                    time_diff = (event_time_naive - now).total_seconds() / 60

                    # Напоминание за 15 минут
                    if 14 <= time_diff <= 16:
                        summary = event.get('summary', 'Без названия')
                        message = f"🔔 <b>Напоминание!</b>\n\n📅 {summary}\n⏰ Начало через 15 минут"
                        send_telegram_message(user_data['chat_id'], message)

        except Exception as e:
            logger.error(f"Ошибка проверки событий для пользователя {user_id}: {e}")

# Запуск планировщика
scheduler = BackgroundScheduler()
scheduler.add_job(check_upcoming_events, 'interval', minutes=1)
scheduler.start()

# ==================== ROUTES ====================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'API is running', 'users': len(users_storage)}), 200

@app.route('/auth/google', methods=['GET'])
def auth_google():
    """Начало OAuth авторизации"""
    telegram_user_id = request.args.get('user_id')
    chat_id = request.args.get('chat_id')

    if not telegram_user_id:
        return jsonify({'error': 'user_id is required'}), 400

    session['telegram_user_id'] = telegram_user_id
    session['chat_id'] = chat_id

    flow = get_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )

    session['state'] = state
    return redirect(authorization_url)

@app.route('/auth/google/callback', methods=['GET'])
def auth_google_callback():
    """Callback после OAuth авторизации"""
    try:
        flow = get_flow()
        flow.fetch_token(authorization_response=request.url)

        credentials = flow.credentials
        telegram_user_id = session.get('telegram_user_id')
        chat_id = session.get('chat_id')

        if telegram_user_id:
            users_storage[telegram_user_id] = {
                'credentials': {
                    'token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                    'token_uri': credentials.token_uri,
                    'client_id': credentials.client_id,
                    'client_secret': credentials.client_secret
                },
                'chat_id': chat_id
            }

            # Отправляем подтверждение в Telegram
            if chat_id:
                send_telegram_message(
                    chat_id,
                    "✅ <b>Google Calendar подключён!</b>\n\nТеперь вы будете получать напоминания о событиях за 15 минут до начала."
                )

            logger.info(f"Пользователь {telegram_user_id} авторизован")

        return """
        <html>
        <head><title>Успешно!</title></head>
        <body style="font-family: Arial; text-align: center; padding-top: 50px;">
            <h1>✅ Авторизация успешна!</h1>
            <p>Google Calendar подключён к боту.</p>
            <p>Можете закрыть это окно и вернуться в Telegram.</p>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
        return f"Ошибка авторизации: {e}", 400

@app.route('/events/<user_id>', methods=['GET'])
def get_user_events(user_id):
    """Получение событий пользователя"""
    if user_id not in users_storage:
        return jsonify({'error': 'User not authorized'}), 401

    user_data = users_storage[user_id]
    if 'credentials' not in user_data:
        return jsonify({'error': 'No credentials'}), 401

    events = get_calendar_events(user_data['credentials'])

    formatted_events = []
    for event in events:
        start = event.get('start', {})
        formatted_events.append({
            'id': event.get('id'),
            'summary': event.get('summary', 'Без названия'),
            'start': start.get('dateTime', start.get('date')),
            'location': event.get('location', ''),
            'description': event.get('description', '')
        })

    return jsonify({'status': 'ok', 'events': formatted_events}), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Обработка Telegram webhook"""
    data = request.get_json()

    if 'message' in data:
        message = data['message']
        chat_id = message['chat']['id']
        user_id = str(message['from']['id'])
        text = message.get('text', '')

        if text == '/start':
            auth_url = f"{BASE_URL}/auth/google?user_id={user_id}&chat_id={chat_id}"
            reply = f"👋 Привет!\n\nЯ бот для напоминаний о событиях Google Calendar.\n\n🔗 <a href=\"{auth_url}\">Нажмите здесь для подключения Google Calendar</a>"
            send_telegram_message(chat_id, reply)

        elif text == '/events':
            if user_id in users_storage and 'credentials' in users_storage[user_id]:
                events = get_calendar_events(users_storage[user_id]['credentials'])
                if events:
                    reply = "📅 <b>Ваши события:</b>\n\n"
                    for event in events[:5]:
                        summary = event.get('summary', 'Без названия')
                        start = event.get('start', {})
                        start_time = start.get('dateTime', start.get('date'))
                        reply += f"• {summary}\n  ⏰ {start_time[:16].replace('T', ' ')}\n\n"
                else:
                    reply = "📭 Нет предстоящих событий на ближайшие 7 дней"
            else:
                reply = "❌ Сначала подключите Google Calendar командой /start"
            send_telegram_message(chat_id, reply)

        elif text == '/status':
            if user_id in users_storage:
                reply = "✅ Google Calendar подключён"
            else:
                reply = "❌ Google Calendar не подключён. Используйте /start"
            send_telegram_message(chat_id, reply)

        elif text == '/help':
            reply = """📖 <b>Доступные команды:</b>

/start - Подключить Google Calendar
/events - Показать предстоящие события
/status - Проверить статус подключения
/help - Показать эту справку

🔔 Бот автоматически присылает напоминания за 15 минут до начала события."""
            send_telegram_message(chat_id, reply)

    return jsonify({'ok': True})

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """Установка Telegram webhook"""
    webhook_url = f"{BASE_URL}/webhook"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"

    response = requests.get(url)
    return jsonify(response.json())

@app.route('/delete_webhook', methods=['GET'])
def delete_webhook():
    """Удаление Telegram webhook"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    response = requests.get(url)
    return jsonify(response.json())

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
