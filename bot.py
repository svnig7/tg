import os
import requests
import tempfile
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import logging
import math
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

# Maximum file size Telegram can handle (4GB in bytes)
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024

# Status update interval (in seconds)
STATUS_INTERVAL = 5

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "Hi! Send me a direct download link and I'll upload it to Telegram as a document.\n"
        "I can handle files larger than 4GB by splitting them into parts.\n\n"
        "I'll show you detailed progress during the transfer."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "Just send me a direct download link and I'll upload it to Telegram as a document.\n"
        "For large files (>4GB), I'll automatically split them into parts.\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message"
    )

async def send_progress_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              message: str, edit_message_id: int = None):
    """Send or edit a progress message."""
    if edit_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=edit_message_id,
                text=message
            )
            return edit_message_id
        except Exception as e:
            logger.warning(f"Couldn't edit message: {e}")
    
    # If editing failed or no message ID provided, send new message
    new_message = await update.message.reply_text(message)
    return new_message.message_id

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the direct link and upload the file with progress updates."""
    url = update.message.text.strip()
    
    # Basic URL validation
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("Please provide a valid HTTP/HTTPS URL.")
        return
    
    try:
        # Get file info first
        status_msg_id = await send_progress_message(update, context, "🔍 Checking the file...")
        
        head = requests.head(url, allow_redirects=True)
        file_size = int(head.headers.get('content-length', 0))
        file_name = os.path.basename(url.split('?')[0])  # Remove query params
        
        if not file_name:
            file_name = "downloaded_file"
        
        if file_size == 0:
            await send_progress_message(
                update, context,
                "❌ Couldn't determine file size. The link might be invalid.",
                status_msg_id
            )
            return
        
        # Human-readable file size
        if file_size > 1024*1024*1024:
            file_size_str = f"{file_size/1024/1024/1024:.2f} GB"
        else:
            file_size_str = f"{file_size/1024/1024:.2f} MB"
        
        # Calculate number of parts needed
        parts = math.ceil(file_size / MAX_FILE_SIZE)
        
        if parts > 1:
            message = (
                f"📁 File: {file_name}\n"
                f"📏 Size: {file_size_str} (larger than 4GB)\n"
                f"✂️ Will split into {parts} parts\n\n"
                f"🔄 Starting download..."
            )
        else:
            message = (
                f"📁 File: {file_name}\n"
                f"📏 Size: {file_size_str}\n\n"
                f"🔄 Starting download..."
            )
        
        status_msg_id = await send_progress_message(update, context, message, status_msg_id)
        
        # Download and upload in chunks if needed
        for part in range(parts):
            start_byte = part * MAX_FILE_SIZE
            end_byte = min((part + 1) * MAX_FILE_SIZE - 1, file_size - 1)
            part_size = end_byte - start_byte + 1
            
            headers = {'Range': f'bytes={start_byte}-{end_byte}'}
            
            # Prepare the filename for this part
            if parts > 1:
                part_file_name = f"{file_name}.part{part + 1:03d}"
                part_info = f" (Part {part + 1}/{parts})"
            else:
                part_file_name = file_name
                part_info = ""
            
            # Download with progress
            download_start_time = time.time()
            last_progress_update = 0
            downloaded_bytes = 0
            
            def download_progress(chunk, chunk_size, total_size):
                nonlocal last_progress_update, downloaded_bytes
                downloaded_bytes += chunk_size
                
                # Throttle progress updates
                if time.time() - last_progress_update > STATUS_INTERVAL:
                    elapsed = time.time() - download_start_time
                    percent = (downloaded_bytes / part_size) * 100
                    speed = downloaded_bytes / (1024 * 1024 * elapsed) if elapsed > 0 else 0
                    remaining = (part_size - downloaded_bytes) / (1024 * 1024 * speed) if speed > 0 else 0
                    
                    progress_msg = (
                        f"📥 Downloading{part_info}\n"
                        f"📁 {part_file_name}\n"
                        f"📊 {percent:.1f}% ({downloaded_bytes/1024/1024:.1f} MB / {part_size/1024/1024:.1f} MB)\n"
                        f"🚀 Speed: {speed:.1f} MB/s\n"
                        f"⏳ ETA: {remaining:.1f} seconds remaining"
                    )
                    
                    # Update progress message
                    try:
                        context.application.create_task(
                            send_progress_message(update, context, progress_msg, status_msg_id)
                        )
                    except Exception as e:
                        logger.warning(f"Progress update failed: {e}")
                    
                    last_progress_update = time.time()
            
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                # Download the file part
                await send_progress_message(
                    update, context,
                    f"📥 Starting download{part_info}...",
                    status_msg_id
                )
                
                try:
                    with requests.get(url, headers=headers, stream=True) as r:
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                temp_file.write(chunk)
                                download_progress(None, len(chunk), part_size)
                
                    temp_file_path = temp_file.name
                    
                    # Download complete
                    elapsed = time.time() - download_start_time
                    speed = part_size / (1024 * 1024 * elapsed) if elapsed > 0 else 0
                    
                    await send_progress_message(
                        update, context,
                        f"✅ Download complete{part_info}\n"
                        f"⏱️ Time: {elapsed:.1f} seconds\n"
                        f"🚀 Avg speed: {speed:.1f} MB/s\n\n"
                        f"📤 Starting upload to Telegram...",
                        status_msg_id
                    )
                    
                    # Upload with progress
                    upload_start_time = time.time()
                    last_upload_update = 0
                    uploaded_bytes = 0
                    
                    def upload_progress(current, total):
                        nonlocal last_upload_update, uploaded_bytes
                        uploaded_bytes = current
                        
                        # Throttle progress updates
                        if time.time() - last_upload_update > STATUS_INTERVAL:
                            elapsed = time.time() - upload_start_time
                            percent = (current / total) * 100
                            speed = current / (1024 * 1024 * elapsed) if elapsed > 0 else 0
                            remaining = (total - current) / (1024 * 1024 * speed) if speed > 0 else 0
                            
                            progress_msg = (
                                f"📤 Uploading{part_info}\n"
                                f"📁 {part_file_name}\n"
                                f"📊 {percent:.1f}% ({current/1024/1024:.1f} MB / {total/1024/1024:.1f} MB)\n"
                                f"🚀 Speed: {speed:.1f} MB/s\n"
                                f"⏳ ETA: {remaining:.1f} seconds remaining"
                            )
                            
                            # Update progress message
                            try:
                                context.application.create_task(
                                    send_progress_message(update, context, progress_msg, status_msg_id)
                                )
                            except Exception as e:
                                logger.warning(f"Progress update failed: {e}")
                            
                            last_upload_update = time.time()
                    
                    # Upload the file
                    with open(temp_file_path, 'rb') as file:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=InputFile(file, filename=part_file_name),
                            caption=f"Part {part + 1} of {parts}" if parts > 1 else None,
                            progress=upload_progress,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=300
                        )
                    
                    # Upload complete
                    elapsed = time.time() - upload_start_time
                    speed = part_size / (1024 * 1024 * elapsed) if elapsed > 0 else 0
                    
                    await send_progress_message(
                        update, context,
                        f"🎉 Upload complete{part_info}\n"
                        f"⏱️ Time: {elapsed:.1f} seconds\n"
                        f"🚀 Avg speed: {speed:.1f} MB/s\n\n"
                        f"🧹 Cleaning up temporary files...",
                        status_msg_id
                    )
                    
                finally:
                    # Clean up
                    try:
                        os.unlink(temp_file_path)
                    except Exception as e:
                        logger.warning(f"Couldn't delete temp file: {e}")
            
        # All parts complete
        total_time = time.time() - download_start_time
        await send_progress_message(
            update, context,
            f"🎉 All parts uploaded successfully!\n"
            f"⏱️ Total time: {timedelta(seconds=int(total_time))}\n"
            f"📦 Total size: {file_size_str}\n"
            f"🚀 Avg speed: {file_size/(1024*1024*total_time):.1f} MB/s",
            status_msg_id
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await send_progress_message(
            update, context,
            f"❌ An error occurred:\n{str(e)}",
            status_msg_id
        )

def main():
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # on non command i.e. message - handle the message
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
