import sys
from pathlib import Path
import sqlite3
import re

_HERE = Path(__file__).resolve()
PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import auto_yt.services.tts_service as tts
from auto_yt.main import get_clean_script_for_tts, apply_tts_filters

DB_PATH = PROJECT_ROOT / "data" / "database.db"

def update_latest_video():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM videos ORDER BY id DESC LIMIT 1')
    row = c.fetchone()
    
    if not row:
        print("No videos in database.")
        conn.close()
        return
        
    video_id = row['id']
    generated_script = row['generated_script']
    
    if "### [AUDIO]" in generated_script:
        print("Video already has TTS audio.")
        conn.close()
        return
        
    print(f"Extracting script for Video ID: {video_id}")
    script_for_tts = get_clean_script_for_tts(generated_script)
    
    if not script_for_tts:
        print("Could not extract script from generated_script.")
        conn.close()
        return
        
    print("Applying filters...")
    filtered_script = apply_tts_filters(script_for_tts)
    
    try:
        voice_id = "e1d9617c-045c-4072-8d17-9be0ec113723"
        print("Generating TTS...")
        audio_url = tts.generate_tts(filtered_script, voice_id)
        if audio_url:
            new_generated_script = generated_script + f"\n\n### [AUDIO]\n{audio_url}"
            c.execute('UPDATE videos SET generated_script = ? WHERE id = ?', (new_generated_script, video_id))
            conn.commit()
            print(f"Successfully updated Video ID {video_id} with audio URL: {audio_url}")
        else:
            print("Failed to get audio_url.")
    except Exception as e:
        print(f"Error generating TTS: {e}")
        
    conn.close()

if __name__ == "__main__":
    update_latest_video()
