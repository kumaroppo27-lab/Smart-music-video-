# main.py - Complete Telegram Music Bot for Render
"""
HasiiMusicBot - Advanced Telegram Music Bot
Single file deployment for Render web server
"""

import asyncio
import time
import logging
import json
import os
import re
import sys
import traceback
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Union, Dict, Set, Tuple, Any
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict, deque
from random import randint
import random
import aiohttp
from functools import wraps
import importlib
from io import BytesIO

# Third-party imports
import yt_dlp
from pyrogram import Client, filters, enums, types, idle
from pyrogram.errors import (
    RPCError, FloodWait, MessageIdInvalid, ChatAdminRequired,
    UserNotParticipant, ChannelInvalid, PeerIdInvalid,
    ChatWriteForbidden, ChatSendPlainForbidden
)
from pymongo import AsyncMongoClient
from pytgcalls import PyTgCalls
from pytgcalls import types as pytgcalls_types
from pytgcalls import exceptions as pytgcalls_exceptions
from ntgcalls import ConnectionNotFound, TelegramServerError
from py_yt import Playlist, VideosSearch

# ==============================================================================
# CONFIGURATION (From your config.py)
# ==============================================================================

class Config:
    def __init__(self):
        # Telegram API Credentials
        self.API_ID: int = int(os.getenv("API_ID", "0"))
        self.API_HASH: str = os.getenv("API_HASH", "")
        
        # Bot Configuration
        self.BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
        self.LOGGER_ID: int = int(os.getenv("LOGGER_ID", "0"))
        self.OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
        
        # Database
        self.MONGO_URL: str = os.getenv("MONGO_DB_URI", "")
        
        # Music Bot Limits
        self.DURATION_LIMIT: int = int(os.getenv("DURATION_LIMIT", "300")) * 60
        self.QUEUE_LIMIT: int = int(os.getenv("QUEUE_LIMIT", "30"))
        self.PLAYLIST_LIMIT: int = int(os.getenv("PLAYLIST_LIMIT", "20"))
        
        # Assistant Sessions
        self.SESSION1: str = os.getenv("STRING_SESSION", "")
        self.SESSION2: str = os.getenv("STRING_SESSION2", "")
        self.SESSION3: str = os.getenv("STRING_SESSION3", "")
        
        # Support Links
        self.SUPPORT_CHANNEL: str = os.getenv("SUPPORT_CHANNEL", "https://t.me/hasiimusic")
        self.SUPPORT_CHAT: str = os.getenv("SUPPORT_CHAT", "https://t.me/lakzexe")
        
        # Excluded Chats
        excluded = os.getenv("EXCLUDED_CHATS", "")
        self.EXCLUDED_CHATS: List[int] = []
        if excluded:
            for chat_id in excluded.split(","):
                chat_id = chat_id.strip()
                if chat_id.lstrip('-').isdigit():
                    self.EXCLUDED_CHATS.append(int(chat_id))
        
        # Feature Flags
        def str_to_bool(value: str) -> bool:
            return value.lower() in ("true", "1", "yes", "y", "on")
        
        self.AUTO_END: bool = str_to_bool(os.getenv("AUTO_END", "False"))
        self.AUTO_LEAVE: bool = str_to_bool(os.getenv("AUTO_LEAVE", "False"))
        self.THUMB_GEN: bool = str_to_bool(os.getenv("THUMB_GEN", "True"))
        
        # YouTube Cookies
        cookie_str = os.getenv("COOKIE_URL", "")
        self.COOKIES_URL: List[str] = []
        if cookie_str:
            valid_sources = ["batbin.me", "pastebin.com", "paste.ee", "rentry.co"]
            for url in cookie_str.split():
                url = url.strip()
                if url and any(source in url for source in valid_sources):
                    self.COOKIES_URL.append(url)
        
        # Image URLs
        self.DEFAULT_THUMB: str = os.getenv("DEFAULT_THUMB", "https://files.catbox.moe/kgrs8f.png")
        self.PING_IMG: str = os.getenv("PING_IMG", "https://files.catbox.moe/2ronp6.jpeg")
        self.START_IMG: str = os.getenv("START_IMG", "https://files.catbox.moe/und0yt.jpg")
        self.RADIO_IMG: str = os.getenv("RADIO_IMG", "https://files.catbox.moe/t03fzk.png")
        
        # Validate
        self.check()
    
    def check(self):
        required_vars = {
            "API_ID": self.API_ID,
            "API_HASH": self.API_HASH,
            "BOT_TOKEN": self.BOT_TOKEN,
            "MONGO_DB_URI": self.MONGO_URL,
            "LOGGER_ID": self.LOGGER_ID,
            "OWNER_ID": self.OWNER_ID,
            "STRING_SESSION": self.SESSION1,
        }
        
        missing = [
            name for name, value in required_vars.items()
            if not value or (isinstance(value, int) and value == 0)
        ]
        
        if missing:
            raise SystemExit(
                f"❌ Missing required environment variables: {', '.join(missing)}\n"
                f"Please check your Render environment variables."
            )

# ==============================================================================
# LOGGING SETUP
# ==============================================================================

logging.basicConfig(
    format="[%(asctime)s - %(levelname)s] - %(name)s: %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("log.txt", maxBytes=10485760, backupCount=5),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
)

# Reduce noise
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("ntgcalls").setLevel(logging.CRITICAL)
logging.getLogger("pymongo").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("pytgcalls").setLevel(logging.ERROR)

logger = logging.getLogger("HasiiMusic")

# Initialize config
config = Config()
logger.info("✅ Configuration loaded successfully")

# ==============================================================================
# DATACLASSES
# ==============================================================================

@dataclass
class Media:
    id: str
    duration: str
    duration_sec: int
    file_path: str
    message_id: int
    title: str
    url: str
    time: int = 0
    user: str = None
    is_live: bool = False

@dataclass
class Track:
    id: str
    channel_name: str
    duration: str
    duration_sec: int
    title: str
    url: str
    file_path: str = None
    message_id: int = 0
    time: int = 0
    thumbnail: str = None
    user: str = None
    view_count: str = None
    is_live: bool = False

# ==============================================================================
# UTILITY CLASSES
# ==============================================================================

class Utilities:
    @staticmethod
    def format_eta(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}:{seconds % 60:02d} min"
        else:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return f"{h}:{m:02d}:{s:02d} h"

    @staticmethod
    def format_size(bytes: int) -> str:
        if bytes >= 1024**3:
            return f"{bytes / 1024 ** 3:.2f} GB"
        elif bytes >= 1024**2:
            return f"{bytes / 1024 ** 2:.2f} MB"
        else:
            return f"{bytes / 1024:.2f} KB"

    @staticmethod
    def to_seconds(time: str) -> int:
        parts = [int(p) for p in time.strip().split(":")]
        return sum(value * 60**i for i, value in enumerate(reversed(parts)))

    @staticmethod
    def format_duration(seconds: int) -> str:
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes:02d}:{secs:02d}"

utils = Utilities()

# ==============================================================================
# QUEUE MANAGER
# ==============================================================================

class QueueManager:
    def __init__(self):
        self.queues: Dict[int, deque] = defaultdict(deque)
    
    def add(self, chat_id: int, item) -> int:
        self.queues[chat_id].append(item)
        return len(self.queues[chat_id]) - 1
    
    def get_current(self, chat_id: int):
        return self.queues[chat_id][0] if self.queues[chat_id] else None
    
    def get_next(self, chat_id: int, check: bool = False):
        if not self.queues[chat_id]:
            return None
        if check:
            return self.queues[chat_id][1] if len(self.queues[chat_id]) > 1 else None
        self.queues[chat_id].popleft()
        return self.queues[chat_id][0] if self.queues[chat_id] else None
    
    def get_queue(self, chat_id: int) -> list:
        return list(self.queues[chat_id])
    
    def clear(self, chat_id: int):
        self.queues[chat_id].clear()
    
    def peek_next(self, chat_id: int, count: int = 2) -> list:
        if not self.queues[chat_id] or len(self.queues[chat_id]) <= 1:
            return []
        queue_list = list(self.queues[chat_id])
        return queue_list[1:min(len(queue_list), count + 1)]
    
    @staticmethod
    def is_downloaded(item) -> bool:
        return bool(getattr(item, 'file_path', None))

queue = QueueManager()

# ==============================================================================
# DATABASE MANAGER
# ==============================================================================

class MongoDB:
    def __init__(self):
        self.mongo = AsyncMongoClient(
            config.MONGO_URL,
            serverSelectionTimeoutMS=12500,
            maxPoolSize=50,
            minPoolSize=10,
        )
        self.db = self.mongo.HasiiTune
        
        # Collections
        self.cache = self.db.cache
        self.assistantdb = self.db.assistant
        self.authdb = self.db.auth
        self.chatsdb = self.db.chats
        self.langdb = self.db.lang
        self.playmodedb = self.db.play
        self.usersdb = self.db.users
        
        # Caches
        self.admin_list = {}
        self.admin_cache_time = {}
        self.active_calls = {}
        self.blacklisted = []
        self.assistant = {}
        self.auth = {}
        self.chats = []
        self.lang = {}
        self.play_mode = []
        self.users = []
        self.logger = False
    
    async def connect(self):
        """Connect to database"""
        try:
            await self.mongo.admin.command("ping")
            logger.info("✅ Database connected")
            await self.load_cache()
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            raise
    
    async def close(self):
        """Close database connection"""
        await self.mongo.close()
        logger.info("Database connection closed")
    
    async def load_cache(self):
        """Load cache data"""
        logger.info("📦 Loading database cache...")
        
        # Load chats
        self.chats.extend([chat["_id"] async for chat in self.chatsdb.find()])
        
        # Load users
        self.users.extend([user["_id"] async for user in self.usersdb.find()])
        
        # Load blacklisted chats
        doc = await self.cache.find_one({"_id": "bl_chats"})
        if doc:
            self.blacklisted.extend(doc.get("chat_ids", []))
        
        logger.info(f"✅ Cache loaded: {len(self.chats)} chats, {len(self.users)} users")
    
    async def get_call(self, chat_id: int) -> bool:
        return chat_id in self.active_calls
    
    async def add_call(self, chat_id: int):
        self.active_calls[chat_id] = 1
    
    async def remove_call(self, chat_id: int):
        self.active_calls.pop(chat_id, None)
    
    async def get_admins(self, chat_id: int, reload: bool = False) -> list:
        from datetime import datetime, timedelta
        
        current_time = datetime.now()
        cache_age = current_time - self.admin_cache_time.get(chat_id, datetime.min)
        
        if chat_id not in self.admin_list or reload or cache_age > timedelta(minutes=15):
            try:
                # This would need the app object, we'll handle it later
                self.admin_list[chat_id] = []
                self.admin_cache_time[chat_id] = current_time
            except:
                self.admin_list[chat_id] = []
        return self.admin_list[chat_id]
    
    async def get_sudoers(self) -> list:
        doc = await self.cache.find_one({"_id": "sudoers"})
        return doc.get("user_ids", []) if doc else []
    
    async def get_blacklisted(self, chat: bool = False) -> list:
        if chat:
            return self.blacklisted
        doc = await self.cache.find_one({"_id": "bl_users"})
        return doc.get("user_ids", []) if doc else []

db = MongoDB()

# ==============================================================================
# BOT CLIENT
# ==============================================================================

class Bot(Client):
    def __init__(self):
        super().__init__(
            name="HasiiMusic",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            parse_mode=enums.ParseMode.HTML,
            max_concurrent_transmissions=7,
        )
        
        self.owner: int = config.OWNER_ID
        self.logger: int = config.LOGGER_ID
        self.bl_users = filters.user()
        self.sudoers: set = {self.owner}
        self.sudo_filter = filters.user(self.owner)
        
        # Will be set after boot
        self.id: Optional[int] = None
        self.name: Optional[str] = None
        self.username: Optional[str] = None
        self.mention: Optional[str] = None
    
    async def boot(self):
        """Start the bot"""
        await super().start()
        
        # Set bot information
        self.id = self.me.id
        self.name = self.me.first_name
        self.username = self.me.username
        self.mention = self.me.mention
        
        # Verify logger group access
        try:
            await self.send_message(self.logger, "🤖 ʙᴏᴛ ꜱᴛᴀʀᴛᴇᴅ")
            member = await self.get_chat_member(self.logger, self.id)
            if member.status != enums.ChatMemberStatus.ADMINISTRATOR:
                raise SystemExit("❌ Bot is not admin in logger group")
        except Exception as e:
            raise SystemExit(f"❌ Failed to access logger group: {e}")
        
        logger.info(f"🤖 Bot started as @{self.username}")
    
    async def exit(self):
        """Stop the bot"""
        await super().stop()
        logger.info("🤖 Bot client stopped")

app = Bot()

# ==============================================================================
# USERBOT/ASSISTANT
# ==============================================================================

class Userbot:
    def __init__(self):
        self.clients = []
        self.one = None
        self.two = None
        self.three = None
        
        # Create clients based on available sessions
        if config.SESSION1:
            self.one = Client(
                name="HasiiTuneUB1",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=config.SESSION1,
            )
        
        if config.SESSION2:
            self.two = Client(
                name="HasiiTuneUB2",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=config.SESSION2,
            )
        
        if config.SESSION3:
            self.three = Client(
                name="HasiiTuneUB3",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=config.SESSION3,
            )
    
    async def boot_client(self, num: int, client: Client):
        """Boot a single assistant client"""
        if not client:
            return
        
        try:
            await client.start()
            
            # Set client attributes
            client.id = client.me.id if client.me else None
            client.name = client.me.first_name if client.me else f"Assistant{num}"
            client.username = client.me.username if client.me else None
            client.mention = client.me.mention if client.me else client.name
            
            self.clients.append(client)
            logger.info(f"👤 Assistant {num} started as @{client.username}")
            
            # Send startup message to logger
            try:
                await client.send_message(config.LOGGER_ID, f"Assistant {num} Started")
            except:
                pass
                
        except Exception as e:
            logger.error(f"❌ Assistant {num} failed to start: {e}")
    
    async def boot(self):
        """Start all assistants"""
        clients = [
            (1, self.one),
            (2, self.two),
            (3, self.three),
        ]
        
        for num, client in clients:
            if client:
                await self.boot_client(num, client)
    
    async def exit(self):
        """Stop all assistants"""
        for client in [self.one, self.two, self.three]:
            if client and hasattr(client, 'is_connected') and client.is_connected:
                try:
                    await client.stop()
                except:
                    pass
        
        logger.info("Assistants stopped")

userbot = Userbot()

# ==============================================================================
# YOUTUBE HANDLER
# ==============================================================================

class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.warned = False
        
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        
        self.search_cache = {}
        self._download_semaphore = asyncio.Semaphore(5)
    
    def get_cookies(self):
        if not self.checked:
            cookie_dir = Path("cookies")
            if cookie_dir.exists():
                for file in cookie_dir.iterdir():
                    if file.suffix == ".txt":
                        self.cookies.append(file.name)
            self.checked = True
        
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        
        return f"cookies/{random.choice(self.cookies)}"
    
    async def save_cookies(self, urls: list):
        logger.info("🍪 Saving cookies from URLs...")
        saved_count = 0
        
        cookie_dir = Path("cookies")
        cookie_dir.mkdir(exist_ok=True)
        
        for url in urls:
            try:
                path = f"cookies/cookie{random.randint(10000, 99999)}.txt"
                link = url.replace("me/", "me/raw/")
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(link) as resp:
                        if resp.status != 200:
                            continue
                        content = await resp.read()
                        
                        with open(path, "wb") as f:
                            f.write(content)
                        
                        cookie_filename = Path(path).name
                        if cookie_filename not in self.cookies:
                            self.cookies.append(cookie_filename)
                        saved_count += 1
                        
            except Exception as e:
                logger.error(f"Cookie download error: {e}")
        
        if saved_count > 0:
            logger.info(f"✅ Cookies saved ({saved_count} files)")
        else:
            logger.error("❌ No cookies saved!")
    
    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))
    
    def url(self, message):
        """Extract URL from message"""
        messages = [message]
        if message.reply_to_message:
            messages.append(message.reply_to_message)
        
        for msg in messages:
            text = msg.text or msg.caption or ""
            
            # Check entities
            if msg.entities:
                for entity in msg.entities:
                    if entity.type == enums.MessageEntityType.URL:
                        link = text[entity.offset: entity.offset + entity.length]
                        return link.split("&si")[0].split("?si")[0]
            
            # Check caption entities
            if msg.caption_entities:
                for entity in msg.caption_entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        return entity.url
        
        return None
    
    async def search(self, query: str, m_id: int):
        """Search YouTube for a track"""
        cache_key = query
        current_time = asyncio.get_event_loop().time()
        
        # Check cache
        if cache_key in self.search_cache:
            cached_result, cache_timestamp = self.search_cache[cache_key]
            if current_time - cache_timestamp < 600:  # 10 minutes
                cached_result.message_id = m_id
                return cached_result
        
        # Perform search
        _search = VideosSearch(query, limit=1)
        results = await _search.next()
        
        if results and results["result"]:
            data = results["result"][0]
            duration = data.get("duration")
            is_live = duration is None or duration == "LIVE"
            
            track = Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=duration if not is_live else "LIVE",
                duration_sec=0 if is_live else utils.to_seconds(duration),
                message_id=m_id,
                title=data.get("title")[:25],
                thumbnail=data.get("thumbnails", [{}])[-1].get("url").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                is_live=is_live,
            )
            
            # Cache result
            self.search_cache[cache_key] = (track, current_time)
            
            # Limit cache size
            if len(self.search_cache) > 100:
                oldest_key = min(self.search_cache.keys(), key=lambda k: self.search_cache[k][1])
                del self.search_cache[oldest_key]
            
            return track
        
        return None
    
    async def download(self, video_id: str, is_live: bool = False):
        """Download YouTube video/audio"""
        url = self.base + video_id
        
        # For live streams
        if is_live:
            cookie = self.get_cookies()
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie,
                "format": "bestaudio/best",
            }
            
            def _extract_url():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        info = ydl.extract_info(url, download=False)
                        return info.get("url") or info.get("manifest_url")
                    except:
                        return None
            
            stream_url = await asyncio.to_thread(_extract_url)
            return stream_url if stream_url else url
        
        # Download audio file
        filename = f"downloads/{video_id}.webm"
        downloads_dir = Path("downloads")
        downloads_dir.mkdir(exist_ok=True)
        
        if Path(filename).exists():
            return filename
        
        async with self._download_semaphore:
            cookie = self.get_cookies()
            ydl_opts = {
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "quiet": True,
                "noplaylist": True,
                "format": "bestaudio[ext=webm][acodec=opus]/bestaudio[acodec=opus]/bestaudio",
                "cookiefile": cookie,
                "concurrent_fragment_downloads": 4,
                "http_chunk_size": 524288,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "ignoreerrors": True,
            }
            
            def _download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        ydl.download([url])
                        if Path(filename).exists():
                            return filename
                        
                        # Try to find the file with different extension
                        import glob
                        pattern = f"downloads/{video_id}.*"
                        files = glob.glob(pattern)
                        if files:
                            return files[0]
                        return None
                    except Exception as e:
                        logger.error(f"Download error: {e}")
                        return None
            
            return await asyncio.get_event_loop().run_in_executor(None, _download)

yt = YouTube()

# ==============================================================================
# TELEGRAM HANDLER
# ==============================================================================

class Telegram:
    def __init__(self):
        self.active = []
        self.events = {}
        self.last_edit = {}
        self.active_tasks = {}
        self.sleep = 5
    
    def get_media(self, msg):
        """Check if message contains downloadable media"""
        return any([msg.audio, msg.document, msg.voice, msg.video])
    
    async def download(self, msg, sent):
        """Download media from Telegram message"""
        msg_id = sent.id
        event = asyncio.Event()
        self.events[msg_id] = event
        self.last_edit[msg_id] = 0
        start_time = time.time()
        
        # Extract media info
        media = msg.audio or msg.voice or msg.video or msg.document
        file_id = getattr(media, "file_unique_id", None)
        file_ext = getattr(media, "file_name", "").split(".")[-1]
        file_size = getattr(media, "file_size", 0)
        file_title = getattr(media, "title", "Telegram File") or "Telegram File"
        duration = getattr(media, "duration", 0)
        
        # Validate duration limit
        if duration > config.DURATION_LIMIT:
            return None
        
        # Validate file size
        if file_size > 200 * 1024 * 1024:
            return None
        
        async def progress(current, total):
            if event.is_set():
                return
            
            now = time.time()
            if now - self.last_edit[msg_id] < self.sleep:
                return
            
            self.last_edit[msg_id] = now
            percent = current * 100 / total
            speed = current / (now - start_time or 1e-6)
            eta = utils.format_eta(int((total - current) / speed))
            
            # Update progress message
            try:
                await sent.edit_text(
                    f"Downloading... {percent:.1f}% | ETA: {eta}"
                )
            except:
                pass
        
        try:
            file_path = f"downloads/{file_id}.{file_ext}"
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)
            
            if not Path(file_path).exists():
                if file_id in self.active:
                    return None
                
                self.active.append(file_id)
                task = asyncio.create_task(
                    msg.download(file_name=file_path, progress=progress)
                )
                self.active_tasks[msg_id] = task
                await task
                self.active.remove(file_id)
                self.active_tasks.pop(msg_id, None)
            
            # Format duration
            if duration >= 3600:
                duration_str = time.strftime("%H:%M:%S", time.gmtime(duration))
            else:
                duration_str = time.strftime("%M:%S", time.gmtime(duration))
            
            return Media(
                id=file_id,
                duration=duration_str,
                duration_sec=duration,
                file_path=file_path,
                message_id=sent.id,
                url=msg.link,
                title=file_title[:25],
            )
        
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None
        
        finally:
            self.events.pop(msg_id, None)
            self.last_edit.pop(msg_id, None)
            self.active = [f for f in self.active if f != file_id]

tg = Telegram()

# ==============================================================================
# VOICE CALL HANDLER
# ==============================================================================

class TgCall(PyTgCalls):
    def __init__(self):
        super().__init__(client=userbot.one if userbot.one else None)
        self.clients = []
        self._play_next_locks = {}
        self._stream_end_cache = {}
    
    async def boot(self):
        """Initialize voice call clients"""
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            
            # Setup event handlers
            @client.on_stream_end()
            async def stream_end_handler(_, update):
                if isinstance(update, pytgcalls_types.StreamEnded):
                    chat_id = update.chat_id
                    await self.play_next(chat_id)
        
        logger.info("📞 Voice call handler started")
    
    async def play_media(self, chat_id: int, message, media, seek_time: int = 0):
        """Play media in voice chat"""
        if len(self.clients) == 0:
            return
        
        client = self.clients[0]  # Use first available client
        
        if not media.file_path:
            return
        
        try:
            # Configure stream
            ffmpeg_params = f"-ss {seek_time}" if seek_time > 1 else ""
            stream = pytgcalls_types.MediaStream(
                media_path=media.file_path,
                audio_parameters=pytgcalls_types.AudioQuality.HIGH,
                ffmpeg_parameters=ffmpeg_params,
            )
            
            # Play stream
            await client.play(
                chat_id=chat_id,
                stream=stream,
            )
            
            # Update database
            await db.add_call(chat_id)
            
            logger.info(f"▶️ Playing media in {chat_id}")
            
        except Exception as e:
            logger.error(f"Play media error: {e}")
            await self.stop(chat_id)
    
    async def stop(self, chat_id: int):
        """Stop playback in chat"""
        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
            
            for client in self.clients:
                try:
                    await client.leave_call(chat_id)
                except:
                    pass
            
            logger.info(f"⏹️ Stopped playback in {chat_id}")
            
        except Exception as e:
            logger.error(f"Stop error: {e}")
    
    async def play_next(self, chat_id: int):
        """Play next track in queue"""
        if chat_id not in self._play_next_locks:
            self._play_next_locks[chat_id] = asyncio.Lock()
        
        lock = self._play_next_locks[chat_id]
        if lock.locked():
            return
        
        async with lock:
            try:
                if not await db.get_call(chat_id):
                    return
                
                media = queue.get_next(chat_id)
                if not media:
                    if config.AUTO_END:
                        await self.stop(chat_id)
                    return
                
                # Download if needed
                if not media.file_path:
                    media.file_path = await yt.download(media.id, getattr(media, 'is_live', False))
                
                if media.file_path:
                    await self.play_media(chat_id, None, media)
                
            except Exception as e:
                logger.error(f"Play next error: {e}")
                await self.stop(chat_id)

tune = TgCall()

# ==============================================================================
# PRELOAD MANAGER
# ==============================================================================

class PreloadManager:
    def __init__(self):
        self._preload_tasks: Dict[int, Set[asyncio.Task]] = {}
        self._preloading: Dict[int, Set[str]] = {}
    
    async def start_preload(self, chat_id: int, count: int = 2):
        """Preload upcoming tracks"""
        upcoming_tracks = queue.peek_next(chat_id, count)
        
        if not upcoming_tracks:
            return
        
        if chat_id not in self._preload_tasks:
            self._preload_tasks[chat_id] = set()
        if chat_id not in self._preloading:
            self._preloading[chat_id] = set()
        
        for track in upcoming_tracks:
            if queue.is_downloaded(track):
                continue
            
            track_id = getattr(track, 'id', None)
            if not track_id or track_id in self._preloading[chat_id]:
                continue
            
            self._preloading[chat_id].add(track_id)
            
            task = asyncio.create_task(self._preload_track(chat_id, track))
            self._preload_tasks[chat_id].add(task)
            
            task.add_done_callback(lambda t, cid=chat_id: self._cleanup_task(cid, t))
    
    async def _preload_track(self, chat_id: int, track):
        """Preload a single track"""
        try:
            track_id = track.id
            is_live = getattr(track, 'is_live', False)
            
            file_path = await yt.download(track_id, is_live=is_live)
            if file_path:
                track.file_path = file_path
        
        except asyncio.CancelledError:
            raise
        
        except Exception as e:
            logger.error(f"Preload error: {e}")
        
        finally:
            if chat_id in self._preloading and track.id in self._preloading[chat_id]:
                self._preloading[chat_id].remove(track.id)
    
    async def cancel_preload(self, chat_id: int):
        """Cancel preload tasks for chat"""
        if chat_id not in self._preload_tasks:
            return
        
        tasks = self._preload_tasks[chat_id].copy()
        for task in tasks:
            if not task.done():
                task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        self._preload_tasks[chat_id].clear()
        if chat_id in self._preloading:
            self._preloading[chat_id].clear()
    
    def _cleanup_task(self, chat_id: int, task: asyncio.Task):
        """Clean up completed task"""
        if chat_id in self._preload_tasks:
            self._preload_tasks[chat_id].discard(task)

preload = PreloadManager()

# ==============================================================================
# LANGUAGE SYSTEM
# ==============================================================================

class Language:
    def __init__(self):
        self.lang_codes = {"en": "English"}
        self.languages = self.load_files()
    
    def load_files(self):
        """Load language files"""
        languages = {"en": {}}  # Default empty English dictionary
        
        # Create minimal language dictionary
        languages["en"] = {
            "play_media": "🎵 Now Playing",
            "play_queued": "✅ Added to queue",
            "play_next": "⏭️ Playing next track",
            "error_no_file": "❌ Download failed",
            "error_vc_disabled": "❌ Voice chat is disabled",
            "dl_progress": "📥 Downloading...",
            "dl_complete": "✅ Download complete",
            "user_no_perms": "❌ You don't have permission",
            "gcast_start": "📢 Broadcast started",
            "gcast_end": "✅ Broadcast complete",
            "ping_pong": "🏓 Pong!",
        }
        
        logger.info("🌐 Loaded English language")
        return languages
    
    async def get_lang(self, chat_id: int) -> dict:
        """Get language dictionary for chat"""
        return self.languages["en"]
    
    def language(self):
        """Language decorator"""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Find message object in args
                message = None
                for arg in args:
                    if isinstance(arg, types.Message) or isinstance(arg, types.CallbackQuery):
                        message = arg
                        break
                
                if message:
                    # Set language dictionary
                    if isinstance(message, types.Message):
                        message.lang = self.languages["en"]
                    elif isinstance(message, types.CallbackQuery):
                        message.message.lang = self.languages["en"]
                
                return await func(*args, **kwargs)
            return wrapper
        return decorator

lang = Language()

# ==============================================================================
# DIRECTORY MANAGEMENT
# ==============================================================================

def ensure_dirs():
    """Create necessary directories"""
    for dir_name in ["cache", "downloads", "cookies"]:
        Path(dir_name).mkdir(parents=True, exist_ok=True)
    logger.info("📁 Directories created")

# ==============================================================================
# BASIC COMMANDS
# ==============================================================================

@app.on_message(filters.command(["start", "help"]) & filters.private)
@lang.language()
async def start_command(_, message: types.Message):
    """Handle /start command"""
    await message.reply_text(
        f"🎵 Hello {message.from_user.mention}!\n\n"
        f"I'm {app.name}, a Telegram Music Bot.\n\n"
        f"Add me to your group and enjoy high-quality music streaming!",
        reply_markup=types.InlineKeyboardMarkup([
            [
                types.InlineKeyboardButton(
                    "Add to Group",
                    url=f"https://t.me/{app.username}?startgroup=true"
                )
            ],
            [
                types.InlineKeyboardButton("Support", url=config.SUPPORT_CHAT),
                types.InlineKeyboardButton("Channel", url=config.SUPPORT_CHANNEL),
            ]
        ])
    )

@app.on_message(filters.command(["ping"]))
@lang.language()
async def ping_command(_, message: types.Message):
    """Handle /ping command"""
    start = time.time()
    msg = await message.reply_text("🏓 Pinging...")
    end = time.time()
    
    latency = round((end - start) * 1000, 2)
    
    await msg.edit_text(
        f"🏓 **Pong!**\n\n"
        f"**Latency:** `{latency}ms`\n"
        f"**Uptime:** `{utils.format_eta(int(time.time() - start_time))}`"
    )

@app.on_message(filters.command(["play"]) & filters.group)
@lang.language()
async def play_command(_, message: types.Message):
    """Handle /play command"""
    if not message.from_user:
        return
    
    # Check if user replied to audio
    if message.reply_to_message and (
        message.reply_to_message.audio or
        message.reply_to_message.voice or
        message.reply_to_message.video
    ):
        # Handle Telegram media
        sent = await message.reply_text("📥 Downloading media...")
        media = await tg.download(message.reply_to_message, sent)
        
        if media:
            media.user = message.from_user.mention
            queue.add(message.chat.id, media)
            
            await sent.edit_text(
                f"✅ Added to queue!\n\n"
                f"**Title:** {media.title}\n"
                f"**Duration:** {media.duration}\n"
                f"**Requested by:** {media.user}",
            )
            
            # Start playing if not already playing
            if not await db.get_call(message.chat.id):
                await tune.play_media(message.chat.id, sent, media)
    
    elif len(message.command) > 1:
        # Handle YouTube search
        query = " ".join(message.command[1:])
        sent = await message.reply_text(f"🔍 Searching for: `{query}`")
        
        track = await yt.search(query, sent.id)
        if track:
            track.user = message.from_user.mention
            queue.add(message.chat.id, track)
            
            await sent.edit_text(
                f"✅ Added to queue!\n\n"
                f"**Title:** {track.title}\n"
                f"**Duration:** {track.duration}\n"
                f"**Channel:** {track.channel_name}\n"
                f"**Requested by:** {track.user}",
                reply_markup=types.InlineKeyboardMarkup([
                    [
                        types.InlineKeyboardButton("Watch on YouTube", url=track.url)
                    ]
                ])
            )
            
            # Start playing if not already playing
            if not await db.get_call(message.chat.id):
                await tune.play_media(message.chat.id, sent, track)
        else:
            await sent.edit_text("❌ No results found")
    
    else:
        await message.reply_text(
            "**Usage:**\n"
            "• /play [song name]\n"
            "• /play [YouTube URL]\n"
            "• Reply to an audio file with /play"
        )

@app.on_message(filters.command(["skip", "next"]) & filters.group)
@lang.language()
async def skip_command(_, message: types.Message):
    """Handle /skip command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    await message.reply_text("⏭️ Skipping...")
    await tune.play_next(message.chat.id)

@app.on_message(filters.command(["stop", "end"]) & filters.group)
@lang.language()
async def stop_command(_, message: types.Message):
    """Handle /stop command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    await message.reply_text("⏹️ Stopping...")
    await tune.stop(message.chat.id)

@app.on_message(filters.command(["pause"]) & filters.group)
@lang.language()
async def pause_command(_, message: types.Message):
    """Handle /pause command"""
    # Note: Pause functionality requires additional implementation
    await message.reply_text("⏸️ Pause feature coming soon!")

@app.on_message(filters.command(["resume"]) & filters.group)
@lang.language()
async def resume_command(_, message: types.Message):
    """Handle /resume command"""
    # Note: Resume functionality requires additional implementation
    await message.reply_text("▶️ Resume feature coming soon!")

@app.on_message(filters.command(["queue", "q"]) & filters.group)
@lang.language()
async def queue_command(_, message: types.Message):
    """Show current queue"""
    queue_list = queue.get_queue(message.chat.id)
    
    if not queue_list:
        return await message.reply_text("📭 Queue is empty")
    
    text = "📋 **Current Queue:**\n\n"
    for i, item in enumerate(queue_list[:10], 1):
        text += f"{i}. **{item.title}**"
        if hasattr(item, 'duration'):
            text += f" - {item.duration}"
        if i == 1:
            text += " **(Now Playing)**"
        text += "\n"
    
    if len(queue_list) > 10:
        text += f"\n...and {len(queue_list) - 10} more tracks"
    
    await message.reply_text(text)

# ==============================================================================
# SUDO COMMANDS
# ==============================================================================

@app.on_message(filters.command(["broadcast"]) & app.sudo_filter)
@lang.language()
async def broadcast_command(_, message: types.Message):
    """Broadcast message to all chats"""
    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply_text(
            "**Usage:**\n"
            "• /broadcast [message]\n"
            "• Reply to a message with /broadcast"
        )
    
    if message.reply_to_message:
        # Forward replied message
        broadcast_text = None
        broadcast_media = message.reply_to_message
    else:
        # Use text message
        broadcast_text = " ".join(message.command[1:])
        broadcast_media = None
    
    sent = await message.reply_text("📢 Starting broadcast...")
    
    success = 0
    failed = 0
    
    # Get all chats
    all_chats = await db.get_chats()
    
    for chat_id in all_chats:
        try:
            if broadcast_media:
                await broadcast_media.forward(chat_id)
            else:
                await app.send_message(chat_id, broadcast_text)
            success += 1
            await asyncio.sleep(0.1)  # Prevent flooding
        except Exception as e:
            logger.error(f"Broadcast failed for {chat_id}: {e}")
            failed += 1
    
    await sent.edit_text(
        f"✅ Broadcast complete!\n\n"
        f"**Success:** {success} chats\n"
        f"**Failed:** {failed} chats"
    )

# ==============================================================================
# MAIN FUNCTION
# ==============================================================================

start_time = time.time()

async def main():
    """Main startup function"""
    try:
        # Create directories
        ensure_dirs()
        
        # Download YouTube cookies if configured
        if config.COOKIES_URL:
            await yt.save_cookies(config.COOKIES_URL)
        
        # Connect to database
        await db.connect()
        
        # Start bot
        await app.boot()
        
        # Start assistants
        await userbot.boot()
        
        # Start voice call handler
        await tune.boot()
        
        # Load sudo users
        sudoers = await db.get_sudoers()
        app.sudoers.update(sudoers)
        app.sudo_filter.update(sudoers)
        
        # Load blacklisted users
        blacklisted = await db.get_blacklisted()
        app.bl_users.update(blacklisted)
        
        logger.info(f"👑 Loaded {len(app.sudoers)} sudo users")
        logger.info(f"🎉 Bot started successfully! (@{app.username})")
        
        # Keep bot running
        await idle()
        
    except KeyboardInterrupt:
        logger.info("Received stop signal...")
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
    finally:
        # Clean shutdown
        await app.exit()
        await userbot.exit()
        await db.close()
        logger.info("✅ Bot stopped successfully")

# ==============================================================================
# RENDER COMPATIBILITY
# ==============================================================================

# Render requires a web server, so we create a simple one
from aiohttp import web

async def handle_health(request):
    """Health check endpoint for Render"""
    return web.Response(text="Bot is running!")

async def start_web_server():
    """Start web server for Render health checks"""
    app_web = web.Application()
    app_web.router.add_get('/health', handle_health)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("🌐 Web server started on port 8080")

# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    try:
        # Start web server for Render
        asyncio.run(start_web_server())
        
        # Run the bot
        asyncio.run(main())
        
    except SystemExit as e:
        logger.error(f"System exit: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
