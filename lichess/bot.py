import os
import random
import threading
import time
from typing import Any, Optional

import berserk
import chess
from berserk.exceptions import ResponseError
from twitchAPI import Chat, Twitch, UserAuthenticator
from twitchAPI.chat import EventData, ChatCommand, ChatMessage
from twitchAPI.types import ChatEvent
import sqlite3
from dotenv import load_dotenv

from lichess import ACCEPT_CHALLENGE_WAIT_SECONDS, USER_SCOPE
import logging
import asyncio

load_dotenv()

API_TOKEN = os.environ['API_TOKEN']
USERNAME = os.environ['USERNAME']
TARGET_CHANNEL = os.environ['TARGET_CHANNEL']
APP_ID = os.environ['APP_ID']
APP_SECRET = os.environ['APP_SECRET']

LOGGER = logging.getLogger(__name__)


class ChatCommands:
    def __init__(self, bot: "Bot"):
        self.bot = bot

    async def challenge(self, cmd: ChatCommand):
        if not cmd.user.mod and "broadcaster" not in cmd.user.badges:
            return
        if len(cmd.parameter) == 0:
            return
        if " " not in cmd.parameter:
            return
        twitch_username, lichess_username = cmd.parameter.split(' ', 1)
        await self.bot.challenge_user(twitch_username, lichess_username)

    async def play(self, cmd: ChatCommand):
        if len(cmd.parameter) == 0:
            return
        await self.bot.challenge_user(cmd.user.name, cmd.parameter)

    async def when(self, cmd: ChatCommand):
        if not cmd.user.mod and "broadcaster" not in cmd.user.badges:
            return
        for idx, (twitch_username, lichess_username) in enumerate(self.bot.challenge_queue):
            if twitch_username == cmd.user.name:
                await cmd.reply(f"Your position in queue: {idx + 1}, Lichess account: {lichess_username}")
        await cmd.reply(f"You are not in queue")

    async def stats(self, cmd: ChatCommand):
        if not cmd.user.mod and "broadcaster" not in cmd.user.badges:
            return
        user_info = user_database.get_user(cmd.user.name)
        await cmd.reply(f"Played: {user_info['games']}, Won: {user_info['won']}, Rank: {user_info['rank']}")


class Bot:
    def __init__(self):
        self.session = berserk.TokenSession(API_TOKEN)
        self.client = berserk.Client(self.session)
        self._active_game: Optional[Game] = None
        self._active_game_thread: Optional[threading.Thread] = None
        self.challenge_queue = []
        self.chat: Optional[Chat] = None
        self.twitch: Optional[Twitch] = None
        self.start_chatbot()

    def start_chatbot(self):
        async def on_ready(ready_event: EventData):
            await ready_event.chat.join_room(TARGET_CHANNEL)
            LOGGER.info("Chatbot is ready")

        async def run():
            self.twitch = await Twitch(APP_ID, APP_SECRET)
            auth = UserAuthenticator(self.twitch, USER_SCOPE)
            token, refresh_token = await auth.authenticate(browser_name='chromedriver')
            await self.twitch.set_user_authentication(token, USER_SCOPE, refresh_token)

            self.chat = await Chat(self.twitch)

            self.chat.register_event(ChatEvent.READY, on_ready)

            chat_commands = ChatCommands(self)
            for k, v in chat_commands.__dict__.items():
                if k.startswith("__"):
                    continue
                self.chat.register_command(k, v)

            self.chat.start()

        asyncio.run(run())

    async def get_active_game(self):
        if self._active_game is not None:
            if self._active_game.state == GameState.FINISHED:
                await self.chat.send_message(TARGET_CHANNEL, f"{'Lost' if self._active_game.user_won else 'Won'} a game vs {self._active_game.opponent_twitch} ({self._active_game.opponent_lichess}), GG!")
                user_database.add_game(self._active_game.opponent_twitch, self._active_game.user_won)
                await self.set_active_game(None)
            elif self._active_game.state == GameState.CHALLENGE_SENT and time.time() - self._active_game.start_time > ACCEPT_CHALLENGE_WAIT_SECONDS:
                await self.chat.send_message(TARGET_CHANNEL, f"Challenge invitation for {self._active_game.opponent_twitch} ({self._active_game.opponent_lichess}) timed out")
                await self.set_active_game(None)
        return self._active_game

    async def set_active_game(self, value):
        self._active_game = value
        if self._active_game_thread is not None:
            self._active_game_thread.join(timeout=60)
            self._active_game_thread = None
        if value is None and len(self.challenge_queue) > 0:
            LOGGER.info(f"Active game became None, triggering next challenge")
            await self.send_challenge(*self.challenge_queue.pop(0))

    async def challenge_user(self, twitch_username: str, lichess_username: str):
        if len(self.challenge_queue) == 0 and await self.get_active_game() is None:
            LOGGER.info(f"Challenge queue is empty, challenging user {twitch_username} ({lichess_username})")
            await self.send_challenge(twitch_username, lichess_username)
        else:
            LOGGER.info(f"There is already an active game or there are other users in queue. Putting {twitch_username} ({lichess_username}) at the end of the queue.")
            self.challenge_queue.append((twitch_username, lichess_username))
            await self.chat.send_message(TARGET_CHANNEL, f"@{twitch_username} Your challenge will start soon. Your position in queue: {len(self.challenge_queue)}")

    async def send_challenge(self, twitch_username: str, lichess_username: str):
        LOGGER.info(f"Sending challenge request to {twitch_username} ({lichess_username})")
        try:
            await self.chat.send_message(TARGET_CHANNEL, f"Challenging user {twitch_username} ({lichess_username})")
            challenge = self.client.challenges.create(
                lichess_username,
                rated=False
            )
            await self.set_active_game(Game(
                bot=self,
                opponent_lichess=lichess_username,
                opponent_twitch=twitch_username,
                challenge_id=challenge['challenge']['id']
            ))
        except ResponseError as err:
            LOGGER.error(f"Failed to send challenge", exc_info=err)
            await self.chat.send_message(TARGET_CHANNEL, f"Failed to challenge user: {err.message}")
            await self.set_active_game(None)

    async def start(self):
        LOGGER.info(f"Starting listening to events")
        for event in self.client.bots.stream_incoming_events():
            LOGGER.info(f"Incoming event: {event}")
            if event['type'] == 'challenge':
                await self.handle_challenge_event(event)
            elif event['type'] == 'gameStart':
                await self.handle_game_start_event(event)
            elif event['type'] == 'challengeDeclined':
                await self.handle_challenge_declined_event(event)
            elif event['type'] == 'gameFinish':
                await self.handle_game_finish(event)

    async def handle_game_finish(self, event: dict[str, Any]):
        pass

    async def handle_challenge_event(self, event: dict[str, Any]):
        pass

    async def handle_game_start_event(self, event: dict[str, Any]):
        active_game = await self.get_active_game()
        if active_game is None:
            return
        LOGGER.info(f"Starting a game {event['game']['id']}")
        await self.chat.send_message(TARGET_CHANNEL, f"Starting a game vs {active_game.opponent_twitch} ({active_game.opponent_lichess})")
        self._active_game_thread = threading.Thread(target=asyncio.run, args=(active_game.start(event['game']['id'], event['game']['isMyTurn']),))
        self._active_game_thread.start()

    async def handle_challenge_declined_event(self, event: dict[str, Any]):
        active_game = await self.get_active_game()
        if active_game is not None and event["challenge"]["id"] == active_game.challenge_id:
            LOGGER.info(f"User {active_game.opponent_twitch} ({active_game.opponent_lichess}) declined the challenge")
            await self.chat.send_message(TARGET_CHANNEL, f"User {active_game.opponent_twitch} ({active_game.opponent_lichess}) declined the challenge")
            await self.set_active_game(None)


class GameState:
    CHALLENGE_SENT = 0
    IN_PROGRESS = 1
    FINISHED = 2


class Game:
    def __init__(self, bot: Bot, opponent_twitch: str, opponent_lichess: str, challenge_id: str):
        self.bot = bot
        self.opponent_twitch = opponent_twitch
        self.opponent_lichess = opponent_lichess
        self.state = GameState.CHALLENGE_SENT
        self.start_time = time.time()
        self.board = chess.Board()
        self.game_id: Optional[str] = None
        self.color = None
        self.challenge_id = challenge_id
        self.user_won: Optional[bool] = None

    async def start(self, game_id: str, first_move: bool):
        self.state = GameState.IN_PROGRESS
        self.game_id = game_id

        self.color = chess.WHITE if first_move else chess.BLACK

        if first_move:
            await self.poll_for_legal_move()

        for event in self.bot.client.bots.stream_game_state(game_id):
            LOGGER.info(f"Received game event: {event}")
            if event['type'] == 'gameFull':
                await self.handle_state_change(event['state'])
            elif event['type'] == 'gameState':
                if event['status'] == 'aborted':
                    break
                elif event['status'] == 'mate':
                    if (event['winner'] == 'white' and self.color == chess.WHITE) or (event['winner'] == 'black' and self.color == chess.BLACK):
                        self.user_won = False
                    else:
                        self.user_won = True
                    break
                else:
                    await self.handle_state_change(event)

        self.state = GameState.FINISHED

    async def poll_for_legal_move(self):
        legal_moves = list([str(self.board.san(move)) for move in self.board.legal_moves])
        num_votes = dict()
        users = set()

        async def on_message(msg: ChatMessage):
            if msg.user.name not in users and msg.text in legal_moves:
                if msg.text not in num_votes:
                    num_votes[msg.text] = 0
                num_votes[msg.text] += 1
                users.add(msg.user.name)

        await self.bot.chat.send_message(TARGET_CHANNEL, "Vote for the next move. You have 30 seconds.")
        self.bot.chat.register_event(ChatEvent.MESSAGE, on_message)

        time.sleep(30)

        self.bot.chat.unregister_event(ChatEvent.MESSAGE, on_message)

        if len(num_votes.values()) == 0:
            polled_move = random.choice(legal_moves)
            await self.bot.chat.send_message(TARGET_CHANNEL, f"Voting closed. No one voted, selecting random move: {polled_move}")
        else:
            polled_move = max(num_votes, key=num_votes.get)
            await self.bot.chat.send_message(TARGET_CHANNEL, f"Voting closed. Selected move: {polled_move}")

        self.bot.client.bots.make_move(self.game_id, self.board.parse_san(polled_move))

    async def handle_state_change(self, event: dict[str, Any]):
        move_san = None
        if len(event['moves'].split()) > 0:
            move = chess.Move.from_uci(event['moves'].split()[-1])
            move_san = self.board.san(move)
            self.board.push(move)
        if self.board.turn == self.color:
            if move_san is not None:
                await self.bot.chat.send_message(TARGET_CHANNEL, f"Opponent moved: {move_san}")
            await self.poll_for_legal_move()


class UserDatabase:
    def __init__(self, db_name):
        self.connection = sqlite3.connect(db_name)
        self.cursor = self.connection.cursor()
        self.create_table()

    def create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                twitch_username TEXT PRIMARY KEY,
                games INTEGER,
                won INTEGER
            )
        """)
        self.connection.commit()

    def add_game(self, twitch_username: str, user_won: bool):
        self.cursor.execute("""
            INSERT OR IGNORE INTO users (twitch_username, games, won)
            VALUES (?, 0, 0)
        """, (twitch_username,))
        self.cursor.execute("""
            UPDATE users
            SET games = games + 1,
                won = won + ?
            WHERE twitch_username = ?
        """, (int(user_won), twitch_username))
        self.connection.commit()

    def get_user(self, twitch_username: str):
        self.cursor.execute("""
            SELECT games, won,
                (SELECT COUNT(*) + 1
                 FROM users AS u
                 WHERE u.won > users.won) AS rank
            FROM users
            WHERE twitch_username = ?
        """, (twitch_username,))
        user_data = self.cursor.fetchone()

        if not user_data:
            return dict(games=0, won=0, rank='1/1')

        games, won, rank = user_data
        total_users = self.get_total_users()
        rank = f"{rank}/{total_users}"

        return dict(games=games, won=won, rank=rank)

    def get_total_users(self):
        self.cursor.execute("""
            SELECT COUNT(*) FROM users
        """)
        return self.cursor.fetchone()[0]


user_database = UserDatabase("user_database.db")
