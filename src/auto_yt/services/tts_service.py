import urllib.request
import urllib.error
import json
import os
import time
import sys

API_KEY = os.environ.get("GENMAX_API_KEY", "")
BASE_URL = "https://api.genmax.io/v1"

def generate_tts(text: str, voice_id: str) -> str:
    if not API_KEY:
        raise ValueError("GENMAX_API_KEY environment variable is required.")

    print(">>> GENERATING AUDIO (TTS)...", file=sys.stderr)
    
    # 1. Start Task
    url = f"{BASE_URL}/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "provider": "minimax",
        "model_id": "speech-2.8-hd",
        "language_code": "Vietnamese",
        "voice_settings": {
            "speed": 0.95,
            "pitch": 0,
            "vol": 1.0
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("xi-api-key", API_KEY)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            task_id = res_data.get('id')
            if not task_id:
                raise Exception("Failed to receive Task ID from TTS API.")
            print(f"    -> Sent TTS request successfully (Task ID: {task_id})", file=sys.stderr)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8')
        raise Exception(f"TTS API error: {e.code} - {err_msg}")
    except Exception as e:
        raise Exception(f"TTS API error: {e}")

    # 2. Polling for Status
    history_url = f"{BASE_URL}/history/{task_id}"
    req_history = urllib.request.Request(history_url, method="GET")
    req_history.add_header("xi-api-key", API_KEY)
    req_history.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    max_retries = 240  # 240 * 5s = 20 minutes max wait
    for i in range(max_retries):
        # Print progress every 30s (every 6 polls)
        if i % 6 == 0:
            elapsed = i * 5
            print(f"    -> TTS đang xử lý... ({elapsed}s đã chờ, tối đa 20 phút)", file=sys.stderr)

        try:
            with urllib.request.urlopen(req_history, timeout=10) as response:
                status_data = json.loads(response.read().decode('utf-8'))
                status = status_data.get("status")
                
                if status == "completed":
                    audio_url = status_data.get("result", {}).get("audio_url")
                    print(f"    -> Audio generated successfully: {audio_url}", file=sys.stderr)
                    return audio_url
                elif status == "failed":
                    error_msg = status_data.get("error", "Unknown error")
                    raise Exception(f"Tạo TTS thất bại: {error_msg}")
                else:
                    # pending or processing
                    time.sleep(5)
        except urllib.error.HTTPError as e:
            time.sleep(5)  # Ignore temporary HTTP errors during polling
        except Exception as e:
            if "Tạo TTS thất bại" in str(e):
                raise e
            time.sleep(5)
            
    raise Exception("Quá thời gian chờ (Timeout) khi tạo Audio.")
