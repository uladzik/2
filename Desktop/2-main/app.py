import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, redirect, session, jsonify
from flask_cors import CORS
import requests

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-me-in-production')
CORS(app)

# Конфигурация
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
BASE_URL = os.getenv('BASE_URL', '')

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Хранилище пользователей
users_storage = {}

def send_telegram_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return False

def get_calendar_events(credentials_dict):
    """Получение событий из Google Calendar"""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

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

        return events_result.get('items', [])
    except Exception as e:
        logger.error(f"Ошибка получения событий: {e}")
        return []

# ==================== ROUTES ====================

@app.route('/', methods=['GET'])
def index():
    return jsonify({'status': 'ok', 'message': 'Calendar Bot API'}), 200

@app.route('/health', methods=['GET'])
def health():
    config_ok = all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, TELEGRAM_BOT_TOKEN, BASE_URL])
    return jsonify({
        'status': 'ok',
        'config': 'complete' if config_ok else 'missing variables',
        'users': len(users_storage)
    }), 200

@app.route('/auth/google', methods=['GET'])
def auth_google():
    """Начало OAuth авторизации"""
    from google_auth_oauthlib.flow import Flow

    telegram_user_id = request.args.get('user_id')
    chat_id = request.args.get('chat_id')

    if not telegram_user_id:
        return jsonify({'error': 'user_id is required'}), 400

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify({'error': 'Google credentials not configured'}), 500

    session['telegram_user_id'] = telegram_user_id
    session['chat_id'] = chat_id

    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{BASE_URL}/auth/google/callback"]
        }
    }

    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=f"{BASE_URL}/auth/google/callback")
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    session['state'] = state

    return redirect(authorization_url)

@app.route('/auth/google/callback', methods=['GET'])
def auth_google_callback():
    """Callback после OAuth авторизации"""
    from google_auth_oauthlib.flow import Flow

    try:
        client_config = {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"{BASE_URL}/auth/google/callback"]
            }
        }

        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=f"{BASE_URL}/auth/google/callback")
        flow.fetch_token(authorization_response=request.url)

        credentials = flow.credentials
        telegram_user_id = session.get('telegram_user_id')
        chat_id = session.get('chat_id')

        if telegram_user_id:
            users_storage[telegram_user_id] = {
                'credentials': {
                    'token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                },
                'chat_id': chat_id
            }

            if chat_id:
                send_telegram_message(chat_id, "✅ <b>Google Calendar подключён!</b>\n\nИспользуйте /events для просмотра событий.")

        return """
        <html>
        <head><title>Успешно!</title></head>
        <body style="font-family: Arial; text-align: center; padding-top: 50px;">
            <h1>✅ Авторизация успешна!</h1>
            <p>Можете закрыть это окно и вернуться в Telegram.</p>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
        return f"Ошибка: {e}", 400

@app.route('/events/<user_id>', methods=['GET'])
def get_user_events(user_id):
    """Получение событий пользователя"""
    if user_id not in users_storage:
        return jsonify({'error': 'User not authorized'}), 401

    events = get_calendar_events(users_storage[user_id]['credentials'])

    formatted = []
    for event in events:
        start = event.get('start', {})
        formatted.append({
            'summary': event.get('summary', 'Без названия'),
            'start': start.get('dateTime', start.get('date')),
        })

    return jsonify({'status': 'ok', 'events': formatted}), 200

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
            reply = f"👋 Привет!\n\nЯ бот для напоминаний Google Calendar.\n\n🔗 <a href=\"{auth_url}\">Подключить Google Calendar</a>"
            send_telegram_message(chat_id, reply)

        elif text == '/events':
            if user_id in users_storage:
                events = get_calendar_events(users_storage[user_id]['credentials'])
                if events:
                    reply = "📅 <b>Ваши события:</b>\n\n"
                    for event in events[:5]:
                        summary = event.get('summary', 'Без названия')
                        start = event.get('start', {})
                        start_time = start.get('dateTime', start.get('date', ''))
                        if start_time:
                            reply += f"• {summary}\n  ⏰ {start_time[:16].replace('T', ' ')}\n\n"
                else:
                    reply = "📭 Нет событий на ближайшие 7 дней"
            else:
                reply = "❌ Сначала подключите календарь: /start"
            send_telegram_message(chat_id, reply)

        elif text == '/status':
            if user_id in users_storage:
                reply = "✅ Google Calendar подключён"
            else:
                reply = "❌ Не подключён. Используйте /start"
            send_telegram_message(chat_id, reply)

        elif text == '/help':
            reply = "📖 <b>Команды:</b>\n\n/start - Подключить календарь\n/events - Показать события\n/status - Статус\n/help - Справка"
            send_telegram_message(chat_id, reply)

    return jsonify({'ok': True})

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """Установка Telegram webhook"""
    if not TELEGRAM_BOT_TOKEN or not BASE_URL:
        return jsonify({'error': 'Missing config'}), 500

    webhook_url = f"{BASE_URL}/webhook"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
    response = requests.get(url, timeout=10)
    return jsonify(response.json())

@app.route('/delete_webhook', methods=['GET'])
def delete_webhook():
    """Удаление Telegram webhook"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    response = requests.get(url, timeout=10)
    return jsonify(response.json())

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
