from datetime import datetime
import mimetypes
import os
from flask import Flask, request
import requests
from pathlib import Path
from dotenv import load_dotenv

app = Flask(__name__)

# Load environment variables from the .env file in the project root
# (If server.py is inside 'src/', resolve one level up to find .env)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- CONFIGURATION ---
VERIFY_TOKEN = "my_custom_webhook_secret_123"
ACCESS_TOKEN = "META_ACCESS_TOKEN"  # Paste your Bearer token here
LOG_FILE = "messages.txt"
BASE_ATTACHMENT_DIR = "attachments"

# --- STORAGE PATH CONFIGURATION ---
# Points to one level above the folder containing server.py
PARENT_DIR = Path(__file__).resolve().parent.parent

# Set destinations inside that parent directory
DATA_DIR = PROJECT_ROOT / "whatsapp_data"
LOG_FILE = DATA_DIR / "messages.txt"
BASE_ATTACHMENT_DIR = DATA_DIR / "attachments"

# Ensure the directories exist
BASE_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)

print(f"[STORAGE] Saving logs and attachments to: {PARENT_DIR}")

# Common WhatsApp MIME types to file extensions
MIME_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "application/pdf": ".pdf",
}


def download_attachment(media_id, msg_type, sender, dt_obj, original_filename=None):
    """Fetches the media URL from Meta, downloads the binary file, and saves it."""
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    # Step 1: Retrieve the download URL and MIME type using the media_id
    meta_url = f"https://graph.facebook.com/v21.0/{media_id}"
    res = requests.get(meta_url, headers=headers)
    if res.status_code != 200:
        print(f"[ERROR] Failed to get media URL: {res.text}")
        return None

    media_info = res.json()
    download_url = media_info.get("url")
    # Clean MIME string (e.g., "audio/ogg; codecs=opus" -> "audio/ogg")
    mime_type = media_info.get("mime_type", "").split(";")[0].strip()

    # Step 2: Determine file extension
    ext = ""
    if original_filename and "." in original_filename:
        ext = os.path.splitext(original_filename)[1]
    if not ext:
        ext = MIME_MAP.get(mime_type) or mimetypes.guess_extension(mime_type) or ".bin"

    # Step 3: Create the subfolder in the parent directory (e.g., ../attachments/image/)
    type_folder = BASE_ATTACHMENT_DIR / msg_type
    type_folder.mkdir(parents=True, exist_ok=True)

    # Step 4: Build filename and path using your pathlib snippet
    date_str = dt_obj.strftime("%Y-%m-%d")
    time_str = dt_obj.strftime("%H-%M-%S")
    filename = f"{sender}_{date_str}_{time_str}{ext}"

    # Target path: one level above server.py inside attachments/<type>/
    file_path = type_folder / filename

    # (If you want the file directly in the parent directory without any subfolder, use:)
    # file_path = PARENT_DIR / filename

    # Step 5: Download and save the binary data
    file_res = requests.get(download_url, headers=headers, timeout=30)
    if file_res.status_code == 200:
        # pathlib allows writing bytes directly without 'with open(...)'
        file_path.write_bytes(file_res.content)
        print(f"[SUCCESS] Saved to: {file_path}")
        return str(file_path)
    else:
        print(f"[ERROR] Download failed: {file_res.status_code}")
        return None


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[SUCCESS] Webhook verified by Meta!")
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()

    try:
        entry = data["entry"][0]["changes"][0]["value"]

        if "messages" in entry:
            for message in entry["messages"]:
                sender = message["from"]
                msg_type = message["type"]

                # Convert WhatsApp Unix timestamp to local Date & Time
                raw_ts = int(message.get("timestamp", datetime.now().timestamp()))
                dt_obj = datetime.fromtimestamp(raw_ts)
                formatted_time = dt_obj.strftime("%Y-%m-%d %H:%M:%S")

                # Handle standard text messages
                if msg_type == "text":
                    body = message["text"]["body"]
                    log_entry = f"[{formatted_time}] From: {sender} | Type: text | Message: {body}\n"

                # Handle media/attachment types
                elif msg_type in ["image", "document", "audio", "video", "sticker"]:
                    media_Obj = message[msg_type]
                    media_id = media_Obj["id"]
                    caption = media_Obj.get("caption", "")
                    orig_name = media_Obj.get("filename")

                    saved_path = download_attachment(
                        media_id, msg_type, sender, dt_obj, orig_name
                    )

                    caption_str = f" | Caption: {caption}" if caption else ""
                    log_entry = f"[{formatted_time}] From: {sender} | Type: {msg_type} | Saved: {saved_path}{caption_str}\n"

                # Handle other types (locations, contacts, reactions, etc.)
                else:
                    log_entry = f"[{formatted_time}] From: {sender} | Type: {msg_type} | Raw: {message.get(msg_type)}\n"

                print(f"[INCOMING] {log_entry.strip()}")

                # Append to messages.txt
                # Open using the Path object (Python built-in open accepts Path objects directly)
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(log_entry)

    except (KeyError, IndexError) as e:
        pass

    return "EVENT_RECEIVED", 200


if __name__ == "__main__":
    app.run(port=5000)