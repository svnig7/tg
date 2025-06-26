from flask import Flask, render_template, request, jsonify
import logging
from pyrogram import Client
from database.ia_filterdb import get_search_results
from utils import get_size
from info import CUSTOM_FILE_CAPTION
from database.connections_mdb import active_connection
import os

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Pyrogram client
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
bot_token = os.getenv("BOT_TOKEN")

bot = Client(
    "web_bot",
    api_id=api_id,
    api_hash=api_hash,
    bot_token=bot_token
)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['GET'])
async def search():
    try:
        query = request.args.get('q', '').strip()
        user_id = request.args.get('user_id', '')
        file_type = request.args.get('type', '').lower()
        
        if not query:
            return jsonify({"error": "Empty query"}), 400
        
        # Get active connection for user
        chat_id = await active_connection(user_id)
        
        # Get search results
        files, _, total = await get_search_results(
            chat_id, 
            query, 
            file_type=file_type if file_type else None,
            max_results=50
        )
        
        results = []
        for file in files:
            title = file['file_name']
            size = get_size(file['file_size'])
            f_caption = file['caption']
            
            if CUSTOM_FILE_CAPTION:
                try:
                    f_caption = CUSTOM_FILE_CAPTION.format(
                        file_name='' if title is None else title,
                        file_size='' if size is None else size,
                        file_caption='' if f_caption is None else f_caption
                    )
                except Exception as e:
                    logger.exception(e)
                    f_caption = f_caption
            
            if f_caption is None:
                f_caption = f"{file['file_name']}"
            
            results.append({
                'title': title,
                'file_id': file['file_id'],
                'caption': f_caption,
                'size': size,
                'description': f'Size: {size}'
            })
        
        return jsonify({
            'results': results,
            'total': total,
            'query': query
        })
        
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
