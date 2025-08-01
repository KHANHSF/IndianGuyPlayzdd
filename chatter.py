import os
import platform
import datetime
import random
from collections import defaultdict

import psutil

from api import API
from botli_dataclasses import Chat_Message, Game_Information
from config import Config
from lichess_game import Lichess_Game

class Chatter:
    def __init__(
        self,
        api: API,
        config: Config,
        username: str,
        game_information: Game_Information,
        lichess_game: Lichess_Game
    ) -> None:
        self.api = api
        self.username = username
        self.game_info = game_information
        self.lichess_game = lichess_game
        self.cpu_message = self._get_cpu()
        self.draw_message = self._get_draw_message(config)
        self.name_message = self._get_name_message(getattr(config, "version", "unknown"))
        self.ram_message = self._get_ram()
        self.player_greeting = self._format_message(getattr(config.messages, 'greeting', None))
        self.player_goodbye = self._format_message(getattr(config.messages, 'goodbye', None))
        self.spectator_greeting = self._format_message(getattr(config.messages, 'greeting_spectators', None))
        self.spectator_goodbye = self._format_message(getattr(config.messages, 'goodbye_spectators', None))
        self.print_eval_rooms = set()
        self.start_time = datetime.datetime.utcnow()
        self.quotes = [
            "When you see a good move, look for a better one. – Emanuel Lasker",
            "Chess is the struggle against the error. – Johannes Zukertort",
            "The blunders are all there on the board, waiting to be made. – Savielly Tartakower",
            "Even a poor plan is better than no plan at all. – Mikhail Chigorin",
            "Chess is life in miniature. – Garry Kasparov",
            "In chess, as in life, opportunity strikes but once. – David Bronstein",
            "I have come to the personal conclusion that while all artists are not chess players, all chess players are artists. – Marcel Duchamp",
            "The hardest game to win is a won game. – Emanuel Lasker"
        ]

    async def handle_chat_message(self, chatLine_Event: dict) -> None:
        chat_message = Chat_Message.from_chatLine_event(chatLine_Event)
        if chat_message.username == 'lichess':
            if chat_message.room == 'player':
                print(chat_message.text)
            return

        if chat_message.username != self.username:
            prefix = f'{chat_message.username} ({chat_message.room}): '
            output = prefix + chat_message.text
            if len(output) > 128:
                output = f'{output[:128]}\n{len(prefix) * " "}{output[128:]}'
            print(output)
        if chat_message.text.startswith('!'):
            await self._handle_command(chat_message)

    async def print_eval(self) -> None:
        if not getattr(self.game_info, "increment_ms", False) and getattr(self.lichess_game, "own_time", 31.0) < 30.0:
            return
        for room in self.print_eval_rooms:
            await self._send_last_message(room)

    async def send_greetings(self) -> None:
        if self.player_greeting:
            await self.api.send_chat_message(self.game_info.id_, 'player', self.player_greeting)
        if self.spectator_greeting:
            await self.api.send_chat_message(self.game_info.id_, 'spectator', self.spectator_greeting)

    async def send_goodbyes(self) -> None:
        if getattr(self.lichess_game, "is_abortable", False):
            return
        if self.player_goodbye:
            await self.api.send_chat_message(self.game_info.id_, 'player', self.player_goodbye)
        if self.spectator_goodbye:
            await self.api.send_chat_message(self.game_info.id_, 'spectator', self.spectator_goodbye)

    async def send_abortion_message(self) -> None:
        await self.api.send_chat_message(
            self.game_info.id_, 'player',
            "Too bad you weren't there. Feel free to challenge me again, I will accept the challenge if possible."
        )

    async def _handle_command(self, chat_message: Chat_Message) -> None:
        command = chat_message.text[1:].lower()
        match command:
            case 'cpu':
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, self.cpu_message)
            case 'draw':
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, self.draw_message)
            case 'eval':
                await self._send_last_message(chat_message.room)
            case 'motor':
                engine_name = getattr(getattr(self.lichess_game, 'engine', None), 'name', 'Unknown')
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, engine_name)
            case 'name':
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, self.name_message)
            case 'printeval':
                if not getattr(self.game_info, 'increment_ms', False) and getattr(self.game_info, 'initial_time_ms', 180_001) < 180_000:
                    await self._send_last_message(chat_message.room)
                    return
                if chat_message.room in self.print_eval_rooms:
                    return
                self.print_eval_rooms.add(chat_message.room)
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, 'Type !quiet to stop eval printing.')
                await self._send_last_message(chat_message.room)
            case 'quiet':
                self.print_eval_rooms.discard(chat_message.room)
            case 'pv':
                if chat_message.room == 'player':
                    return
                message = self._append_pv() or 'No PV available.'
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, message)
            case 'ram':
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, self.ram_message)
            case 'fen':
                fen = getattr(getattr(self.lichess_game, "board", None), "fen", lambda: "Unavailable")()
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Current FEN: {fen}")
            case 'moves':
                moves = []
                board = getattr(self.lichess_game, "board", None)
                if board and hasattr(board, "move_stack"):
                    moves = [board.san(m) for m in board.move_stack]
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Moves so far: {' '.join(moves) if moves else 'No moves'}")
            case 'score':
                score = getattr(self.lichess_game, "last_message", None)
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Last eval/score: {score if score else 'N/A'}")
            case 'opponent':
                opponent = self.game_info.black_name if getattr(self.lichess_game, 'is_white', False) else self.game_info.white_name
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Your opponent: {opponent}")
            case 'time':
                now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Server time: {now}")
            case 'uptime':
                uptime = datetime.datetime.utcnow() - self.start_time
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Uptime: {str(uptime).split('.')[0]}")
            case 'about':
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, "I'm a Lichess chess bot, coded by treyop, running on open source code.")
            case 'version':
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, f"Bot version: {getattr(self.game_info, 'version', 'unknown')}")
            case 'joke':
                jokes = [
                    "Why did the chess player bring a suitcase to the game? He wanted to castle early!",
                    "How do you become a chess grandmaster? By moving in the right circles!",
                    "Why did the pawn get promoted? It worked hard and reached the end!"
                ]
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, random.choice(jokes))
            case 'quote':
                quote = random.choice(self.quotes)
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, quote)
            case 'help' | 'commands':
                help_message = (
                    "!cpu, !ram, !motor, !name, !draw, !eval, !printeval, !quiet, !pv, !fen, "
                    "!moves, !score, !opponent, !time, !uptime, !about, !version, !joke, !quote"
                )
                await self.api.send_chat_message(self.game_info.id_, chat_message.room, help_message)

    async def _send_last_message(self, room: str) -> None:
        last_message = getattr(self.lichess_game, "last_message", "No evaluation yet.").replace('Engine', 'Evaluation')
        last_message = ' '.join(last_message.split())
        if room == 'spectator':
            last_message = self._append_pv(last_message)
        await self.api.send_chat_message(self.game_info.id_, room, last_message)

    def _get_cpu(self) -> str:
        try:
            if os.path.exists('/proc/cpuinfo'):
                with open('/proc/cpuinfo', encoding='utf-8') as cpuinfo:
                    for line in cpuinfo:
                        if line.startswith('model name'):
                            cpu = line.split(': ')[1].replace('(R)', '').replace('(TM)', '').strip()
                            if cpu:
                                break
                    else:
                        cpu = platform.processor() or "Unknown CPU"
            else:
                cpu = platform.processor() or "Unknown CPU"
            cores = psutil.cpu_count(logical=False) or 1
            threads = psutil.cpu_count(logical=True) or 1
            freq = psutil.cpu_freq()
            cpu_freq = freq.max / 1000 if freq and freq.max else 0
            return f"{cpu} ({cores} cores/{threads} threads, {cpu_freq:.2f} GHz)"
        except Exception:
            return "Unknown CPU"

    def _get_ram(self) -> str:
        try:
            mem_bytes = psutil.virtual_memory().total
            mem_gib = mem_bytes / (1024.**3)
            return f"{mem_gib:.1f} GiB RAM detected"
        except Exception:
            return "RAM info unavailable"

    def _get_draw_message(self, config: Config) -> str:
        if not getattr(config.offer_draw, 'enabled', False):
            return 'This bot will neither accept nor offer draws.'
        max_score = getattr(config.offer_draw, 'score', 0) / 100
        return (
            f'The bot offers draw at move {config.offer_draw.min_game_length} or later '
            f'if the eval is within +{max_score:.2f} to -{max_score:.2f} for the last '
            f'{config.offer_draw.consecutive_moves} moves.'
        )

    def _get_name_message(self, version: str) -> str:
        return "Bud's runnin on treyop's PC"

    def _format_message(self, message: str = None) -> str | None:
        if not message:
            return None
        opponent_username = self.game_info.black_name if getattr(self.lichess_game, 'is_white', False) else self.game_info.white_name
        engine_name = getattr(getattr(self.lichess_game, 'engine', None), 'name', '')
        mapping = defaultdict(str, {
            'opponent': opponent_username, 'me': self.username,
            'engine': engine_name, 'cpu': self.cpu_message,
            'ram': self.ram_message
        })
        try:
            return message.format_map(mapping)
        except Exception:
            return message

    def _append_pv(self, initial_message: str = '') -> str:
        last_pv = getattr(self.lichess_game, 'last_pv', [])
        if len(last_pv) < 2:
            return initial_message
        if initial_message:
            initial_message += ' '
        board = self.lichess_game.board.copy(stack=1) if getattr(self.lichess_game, 'is_our_turn', False) else self.lichess_game.board.copy(stack=False)
        if getattr(board, "turn", True):
            initial_message += 'PV:'
        else:
            initial_message += f'PV: {board.fullmove_number}...'
        final_message = initial_message
        for move in last_pv[1:]:
            if getattr(board, "turn", True):
                initial_message += f' {board.fullmove_number}.'
            initial_message += f' {board.san(move)}'
            if len(initial_message) > 140:
                break
            board.push(move)
            final_message = initial_message
        return final_message
