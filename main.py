import os
import time
import asyncio
import json
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import requests

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Set in Render Env Vars
CHAT_ID = os.getenv("CHAT_ID")      # Set in Render Env Vars
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
TELEGRAM_FILE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

app = FastAPI()

# Enable CORS for InfinityFree frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace * with your InfinityFree URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VideoRequest(BaseModel):
    url: str
    format_type: str  # 'hd', 'normal', 'audio'

def delete_telegram_message(message_id: int):
    """Deletes the message (and video) from Telegram after a delay."""
    time.sleep(60)  # Wait 60 seconds to allow user to start download
    try:
        del_url = f"{TELEGRAM_API_URL}/deleteMessage"
        requests.post(del_url, json={"chat_id": CHAT_ID, "message_id": message_id})
        logger.info(f"Deleted message {message_id} from Telegram.")
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")

def upload_to_telegram(file_path: str):
    """Uploads file to Telegram and returns file_path and message_id."""
    url = f"{TELEGRAM_API_URL}/sendDocument"
    with open(file_path, "rb") as f:
        response = requests.post(url, data={"chat_id": CHAT_ID}, files={"document": f})
    
    if response.status_code != 200:
        raise Exception("Telegram Upload Failed")
    
    result = response.json()
    file_id = result["result"]["document"]["file_id"]
    message_id = result["result"]["message_id"]
    
    # Get direct file path
    path_response = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}")
    file_path_remote = path_response.json()["result"]["file_path"]
    
    return f"{TELEGRAM_FILE_URL}/{file_path_remote}", message_id

@app.get("/health")
async def health_check():
    return {"status": "active", "message": "Backend is running"}

@app.post("/process-video")
async def process_video(request: VideoRequest, background_tasks: BackgroundTasks):
    try:
        url = request.url
        format_type = request.format_type
        
        # Configure yt-dlp
        ydl_opts = {
            'outtmpl': '%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }

        if format_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}],
            })
        elif format_type == 'hd':
            ydl_opts.update({'format': 'bestvideo+bestaudio/best', 'merge_output_format': 'mp4'})
        else: # normal
            ydl_opts.update({'format': 'best[height<=480]', 'merge_output_format': 'mp4'})

        # Download Video Locally
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if format_type == 'audio':
                filename = filename.rsplit(".", 1)[0] + ".mp3"

        # Upload to Telegram
        tg_link, message_id = upload_to_telegram(filename)

        # Cleanup Local File
        if os.path.exists(filename):
            os.remove(filename)

        # Schedule Telegram Deletion (Pseudo "5 seconds after download" logic)
        # We give the user 60 seconds to click the link before deleting from chat history
        background_tasks.add_task(delete_telegram_message, message_id)

        return {
            "status": "success", 
            "download_url": tg_link, 
            "title": info.get('title', 'Video'),
            "thumbnail": info.get('thumbnail', '')
        }

    except Exception as e:
        logger.error(str(e))
        return {"status": "error", "message": str(e)}

