from flask import Flask, request, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)

meetings_storage = {}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'API is running'}), 200

@app.route('/save_meetings', methods=['POST'])
def save_meetings():
    try:
        data = request.get_json()
        meetings = data.get('meetings', [])
        meetings_storage['all'] = meetings
        return jsonify({'status': 'ok', 'message': f'Saved {len(meetings)} meetings'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/get_meetings', methods=['GET'])
def get_meetings():
    try:
        meetings = meetings_storage.get('all', [])
        return jsonify({'status': 'ok', 'meetings': meetings}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
