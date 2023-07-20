import asyncio
import threading
import time
import logging
from lichess.bot import Bot
from pyvirtualdisplay.smartdisplay import Display

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

logging.getLogger("twitchAPI").setLevel(logging.INFO)
logging.getLogger("asyncio").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.INFO)
logging.getLogger("berserk").setLevel(logging.INFO)

LOGGER = logging.getLogger(__name__)

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080

with Display(visible=True) as display:
    chess_bot = Bot()

    chess_bot_thread = threading.Thread(target=lambda: asyncio.run(chess_bot.start()))
    chess_bot_thread.start()
    
    while True:
        try:
            time.sleep(1)
            asyncio.run(chess_bot.get_active_game())
        except KeyboardInterrupt:
            break
