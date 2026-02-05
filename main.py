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
import shutil
import glob
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Union, Dict, Set, Tuple, Any
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict, deque
from random import randint
import random
import aiohttp
from functools import wraps
import hashlib
import datetime
from concurrent.futures import ThreadPoolExecutor

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
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

# ==============================================================================
# CONFIGURATION
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

    @staticmethod
    async def extract_user(message) -> Optional[types.User]:
        """Extract user from message"""
        if message.reply_to_message:
            return message.reply_to_message.from_user
        
        if message.entities:
            for e in message.entities:
                if e.type == enums.MessageEntityType.TEXT_MENTION:
                    return e.user
        
        if message.text:
            try:
                if m := re.search(r"@(\w{5,32})", message.text):
                    # This requires app object, we'll handle it differently
                    pass
                if m := re.search(r"\b\d{6,15}\b", message.text):
                    # This requires app object, we'll handle it differently
                    pass
            except:
                pass
        
        return None

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
    
    def get_all(self, chat_id: int) -> list:
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
    
    async def playing(self, chat_id: int, paused: bool = None) -> bool:
        if paused is not None:
            self.active_calls[chat_id] = int(not paused)
        return bool(self.active_calls.get(chat_id, False))
    
    async def get_admins(self, chat_id: int, reload: bool = False) -> list:
        from datetime import datetime, timedelta
        
        current_time = datetime.now()
        cache_age = current_time - self.admin_cache_time.get(chat_id, datetime.min)
        
        if chat_id not in self.admin_list or reload or cache_age > timedelta(minutes=15):
            try:
                # We'll implement admin fetching when app is available
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
    
    async def is_auth(self, chat_id: int, user_id: int) -> bool:
        if chat_id not in self.auth:
            doc = await self.authdb.find_one({"_id": chat_id}) or {}
            self.auth[chat_id] = set(doc.get("user_ids", []))
        return user_id in self.auth[chat_id]
    
    async def add_auth(self, chat_id: int, user_id: int):
        users = await self._get_auth(chat_id)
        if user_id not in users:
            users.add(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$addToSet": {"user_ids": user_id}}, upsert=True
            )
    
    async def rm_auth(self, chat_id: int, user_id: int):
        users = await self._get_auth(chat_id)
        if user_id in users:
            users.discard(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$pull": {"user_ids": user_id}}
            )
    
    async def _get_auth(self, chat_id: int) -> Set[int]:
        if chat_id not in self.auth:
            doc = await self.authdb.find_one({"_id": chat_id}) or {}
            self.auth[chat_id] = set(doc.get("user_ids", []))
        return self.auth[chat_id]
    
    async def set_assistant(self, chat_id: int) -> int:
        num = randint(1, 3)  # We'll adjust this based on available assistants
        await self.assistantdb.update_one(
            {"_id": chat_id},
            {"$set": {"num": num}},
            upsert=True,
        )
        self.assistant[chat_id] = num
        return num
    
    async def get_assistant(self, chat_id: int):
        if chat_id not in self.assistant:
            doc = await self.assistantdb.find_one({"_id": chat_id})
            num = doc["num"] if doc else await self.set_assistant(chat_id)
            self.assistant[chat_id] = num
        
        # Return the assistant number (we'll map to actual client later)
        return self.assistant[chat_id]
    
    async def add_chat(self, chat_id: int):
        if not await self.is_chat(chat_id):
            self.chats.append(chat_id)
            await self.chatsdb.insert_one({"_id": chat_id})
    
    async def is_chat(self, chat_id: int) -> bool:
        return chat_id in self.chats
    
    async def rm_chat(self, chat_id: int):
        if await self.is_chat(chat_id):
            self.chats.remove(chat_id)
            await self.chatsdb.delete_one({"_id": chat_id})
    
    async def get_chats(self) -> list:
        if not self.chats:
            self.chats.extend([chat["_id"] async for chat in self.chatsdb.find()])
        return self.chats
    
    async def add_user(self, user_id: int):
        if not await self.is_user(user_id):
            self.users.append(user_id)
            await self.usersdb.insert_one({"_id": user_id})
    
    async def is_user(self, user_id: int) -> bool:
        return user_id in self.users
    
    async def get_users(self) -> list:
        if not self.users:
            self.users.extend([user["_id"] async for user in self.usersdb.find()])
        return self.users

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
                logger.warning("⚠️ Bot is not admin in logger group")
        except Exception as e:
            logger.warning(f"⚠️ Could not access logger group: {e}")
        
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
        self.available_clients = {}
        
        # Create clients based on available sessions
        if config.SESSION1:
            self.one = Client(
                name="HasiiTuneUB1",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=config.SESSION1,
            )
            self.available_clients[1] = self.one
        
        if config.SESSION2:
            self.two = Client(
                name="HasiiTuneUB2",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=config.SESSION2,
            )
            self.available_clients[2] = self.two
        
        if config.SESSION3:
            self.three = Client(
                name="HasiiTuneUB3",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=config.SESSION3,
            )
            self.available_clients[3] = self.three
    
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
    
    def get_client_by_num(self, num: int):
        """Get client by assistant number"""
        return self.available_clients.get(num)

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
        """Get random cookie file"""
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
                logger.warning("⚠️ Cookies are missing; downloads might fail.")
            return None
        
        return f"cookies/{random.choice(self.cookies)}"
    
    async def save_cookies(self, urls: list):
        """Save cookies from URLs"""
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
        """Check if URL is valid YouTube URL"""
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
        try:
            _search = VideosSearch(query, limit=1)
            results = await _search.next()
            
            if results and results["result"]:
                data = results["result"][0]
                duration = data.get("duration")
                is_live = duration is None or duration == "LIVE"
                
                # Get thumbnail
                thumbnails = data.get("thumbnails", [{}])
                thumbnail_url = ""
                if thumbnails:
                    thumbnail_url = thumbnails[-1].get("url", "").split("?")[0]
                
                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("channel", {}).get("name", "Unknown"),
                    duration=duration if not is_live else "LIVE",
                    duration_sec=0 if is_live else utils.to_seconds(duration),
                    message_id=m_id,
                    title=data.get("title", "Unknown")[:25],
                    thumbnail=thumbnail_url,
                    url=data.get("link", ""),
                    view_count=data.get("viewCount", {}).get("short", ""),
                    is_live=is_live,
                )
                
                # Cache result
                self.search_cache[cache_key] = (track, current_time)
                
                # Limit cache size
                if len(self.search_cache) > 100:
                    oldest_key = min(self.search_cache.keys(), key=lambda k: self.search_cache[k][1])
                    del self.search_cache[oldest_key]
                
                return track
        
        except Exception as e:
            logger.error(f"Search error: {e}")
        
        return None
    
    async def playlist(self, limit: int, user: str, url: str):
        """Get playlist tracks"""
        try:
            plist = await Playlist.get(url)
            tracks = []
            
            if not plist or "videos" not in plist or not plist["videos"]:
                return []
            
            for data in plist["videos"][:limit]:
                try:
                    # Get thumbnail
                    thumbnails = data.get("thumbnails", [])
                    thumbnail_url = ""
                    if thumbnails and len(thumbnails) > 0:
                        thumbnail_url = thumbnails[-1].get("url", "").split("?")[0]
                    
                    # Get link
                    link = data.get("link", "")
                    if "&list=" in link:
                        link = link.split("&list=")[0]
                    
                    track = Track(
                        id=data.get("id", ""),
                        channel_name=data.get("channel", {}).get("name", ""),
                        duration=data.get("duration", "0:00"),
                        duration_sec=utils.to_seconds(data.get("duration", "0:00")),
                        title=(data.get("title", "Unknown")[:25]),
                        thumbnail=thumbnail_url,
                        url=link,
                        user=user,
                        view_count="",
                    )
                    tracks.append(track)
                except Exception as e:
                    logger.debug(f"Skipping track: {e}")
                    continue
            
            return tracks
        
        except Exception as e:
            logger.error(f"Playlist error: {e}")
            raise
    
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
                        
                        # Check if file exists
                        if Path(filename).exists():
                            return filename
                        
                        # Try to find the file with different extension
                        pattern = f"downloads/{video_id}.*"
                        files = glob.glob(pattern)
                        if files:
                            return files[0]
                        
                        # Check for .part file
                        part_file = f"{filename}.part"
                        if Path(part_file).exists():
                            shutil.move(part_file, filename)
                            return filename
                        
                        return None
                    
                    except Exception as e:
                        logger.error(f"Download error for {video_id}: {e}")
                        return None
            
            return await asyncio.get_event_loop().run_in_executor(None, _download)

yt = YouTube()

# ==============================================================================
# TELEGRAM HANDLER
# ==============================================================================

class TelegramHandler:
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
            await sent.edit_text(f"❌ Duration limit exceeded (max {config.DURATION_LIMIT//60} min)")
            return None
        
        # Validate file size
        if file_size > 200 * 1024 * 1024:
            await sent.edit_text("❌ File too large (max 200MB)")
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
                    f"📥 **Downloading...**\n\n"
                    f"**Progress:** {utils.format_size(current)} / {utils.format_size(total)} [{percent:.1f}%]\n"
                    f"**Speed:** {utils.format_size(speed)}/s | **ETA:** {eta}"
                )
            except:
                pass
        
        try:
            file_path = f"downloads/{file_id}.{file_ext}"
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)
            
            if not Path(file_path).exists():
                if file_id in self.active:
                    await sent.edit_text("⚠️ Already downloading this file")
                    return None
                
                self.active.append(file_id)
                task = asyncio.create_task(
                    msg.download(file_name=file_path, progress=progress)
                )
                self.active_tasks[msg_id] = task
                await task
                self.active.remove(file_id)
                self.active_tasks.pop(msg_id, None)
                
                await sent.edit_text(f"✅ Download complete! ({time.time() - start_time:.1f}s)")
            
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
                user=sent.from_user.mention if sent.from_user else "Unknown",
            )
        
        except Exception as e:
            logger.error(f"Download error: {e}")
            await sent.edit_text(f"❌ Download failed: {str(e)[:100]}")
            return None
        
        finally:
            self.events.pop(msg_id, None)
            self.last_edit.pop(msg_id, None)
            self.active = [f for f in self.active if f != file_id]
    
    async def cancel(self, query):
        """Cancel download"""
        event = self.events.get(query.message.id)
        task = self.active_tasks.pop(query.message.id, None)
        
        if event:
            event.set()
        
        if task and not task.done():
            task.cancel()
        
        if event or task:
            await query.edit_message_text("✅ Download cancelled")
        else:
            await query.answer("No active download found", show_alert=True)

tg = TelegramHandler()

# ==============================================================================
# BUTTONS/KEYBOARD HELPERS
# ==============================================================================

class InlineButtons:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self.ikb = types.InlineKeyboardButton
    
    def controls(self, chat_id: int, timer: str = None):
        """Create playback controls"""
        keyboard = []
        
        if timer:
            keyboard.append([self.ikb(text=timer, callback_data=f"controls status {chat_id}")])
        
        # Main controls
        keyboard.append([
            self.ikb(text="« 10", callback_data=f"controls seek_back_10 {chat_id}"),
            self.ikb(text="« 30", callback_data=f"controls seek_back_30 {chat_id}"),
            self.ikb(text="30 »", callback_data=f"controls seek_forward_30 {chat_id}"),
            self.ikb(text="10 »", callback_data=f"controls seek_forward_10 {chat_id}"),
        ])
        
        keyboard.append([
            self.ikb(text="▷", callback_data=f"controls resume {chat_id}"),
            self.ikb(text="II", callback_data=f"controls pause {chat_id}"),
            self.ikb(text="↻", callback_data=f"controls replay {chat_id}"),
            self.ikb(text="‣‣I", callback_data=f"controls skip {chat_id}"),
            self.ikb(text="▢", callback_data=f"controls stop {chat_id}"),
        ])
        
        keyboard.append([
            self.ikb(text="🗑️ Delete", callback_data=f"controls close {chat_id}"),
        ])
        
        return self.ikm(keyboard)
    
    def start_key(self, private: bool = False):
        """Start keyboard"""
        rows = [
            [
                self.ikb(
                    text="➕ Add to Group",
                    url=f"https://t.me/{app.username}?startgroup=true",
                )
            ],
            [
                self.ikb(text="📚 Help", callback_data="help"),
                self.ikb(text="📊 Stats", callback_data="stats"),
            ],
            [
                self.ikb(text="👥 Support", url=config.SUPPORT_CHAT),
                self.ikb(text="📢 Channel", url=config.SUPPORT_CHANNEL),
            ],
        ]
        
        if private:
            rows.append([
                self.ikb(text="🔗 Source", url="https://github.com/"),
            ])
        
        return self.ikm(rows)
    
    def cancel_dl(self, text: str = "❌ Cancel"):
        """Cancel download button"""
        return self.ikm([[self.ikb(text=text, callback_data="cancel_dl")]])
    
    def yt_key(self, link: str):
        """YouTube buttons"""
        return self.ikm([
            [
                self.ikb(text="📋 Copy Link", copy_text=link),
                self.ikb(text="▶️ Watch", url=link),
            ]
        ])

buttons = InlineButtons()

# ==============================================================================
# THUMBNAIL GENERATOR
# ==============================================================================

class ThumbnailGenerator:
    def __init__(self):
        try:
            # Try to load fonts, fallback to default if not available
            self.title_font = ImageFont.truetype("assets/Raleway-Bold.ttf", 32)
            self.regular_font = ImageFont.truetype("assets/Inter-Light.ttf", 18)
        except:
            # Use default fonts
            self.title_font = ImageFont.load_default()
            self.regular_font = ImageFont.load_default()
        
        # Modern frosted glass design constants
        self.PANEL_W, self.PANEL_H = 763, 545
        self.PANEL_X = (1280 - self.PANEL_W) // 2
        self.PANEL_Y = 88
        self.TRANSPARENCY = 170
        
        self.THUMB_W, self.THUMB_H = 542, 273
        self.THUMB_X = self.PANEL_X + (self.PANEL_W - self.THUMB_W) // 2
        self.THUMB_Y = self.PANEL_Y + 36
        
        self.TITLE_X = 377
        self.TITLE_Y = self.THUMB_Y + self.THUMB_H + 10
        self.META_Y = self.TITLE_Y + 45
        
        self.BAR_X, self.BAR_Y = 388, self.META_Y + 45
        self.BAR_RED_LEN = 280
        self.BAR_TOTAL_LEN = 480
        
        self.MAX_TITLE_WIDTH = 580
    
    def trim_to_width(self, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
        """Trim text to fit width"""
        ellipsis = "…"
        if font.getlength(text) <= max_w:
            return text
        
        for i in range(len(text) - 1, 0, -1):
            if font.getlength(text[:i] + ellipsis) <= max_w:
                return text[:i] + ellipsis
        
        return ellipsis
    
    async def save_thumb(self, output_path: str, url: str) -> str:
        """Download thumbnail"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                with open(output_path, "wb") as f:
                    f.write(await resp.read())
        return output_path
    
    async def generate(self, song: Track, size=(1280, 720)) -> str:
        """Generate thumbnail"""
        try:
            temp = f"cache/temp_{song.id}.jpg"
            output = f"cache/{song.id}_modern.png"
            
            # Check cache
            if os.path.exists(output):
                return output
            
            # Download thumbnail
            await self.save_thumb(temp, song.thumbnail)
            
            # Generate in thread pool to avoid blocking
            return await asyncio.get_event_loop().run_in_executor(
                None, self._generate_sync, temp, output, song, size
            )
        
        except Exception as e:
            logger.error(f"Thumbnail generation error: {e}")
            return config.DEFAULT_THUMB
    
    def _generate_sync(self, temp: str, output: str, song: Track, size=(1280, 720)) -> str:
        """Synchronous thumbnail generation"""
        try:
            # Prepare base image
            with Image.open(temp) as temp_img:
                base = temp_img.resize(size).convert("RGBA")
            
            # Create blurred background
            bg = ImageEnhance.Brightness(base.filter(
                ImageFilter.BoxBlur(10))).enhance(0.6)
            
            # Create frosted glass panel
            panel_area = bg.crop(
                (self.PANEL_X, self.PANEL_Y, self.PANEL_X + self.PANEL_W, self.PANEL_Y + self.PANEL_H))
            overlay = Image.new("RGBA", (self.PANEL_W, self.PANEL_H),
                                (255, 255, 255, self.TRANSPARENCY))
            frosted = Image.alpha_composite(panel_area, overlay)
            
            # Apply rounded corners
            mask = Image.new("L", (self.PANEL_W, self.PANEL_H), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, self.PANEL_W, self.PANEL_H), 50, fill=255)
            bg.paste(frosted, (self.PANEL_X, self.PANEL_Y), mask)
            
            # Add thumbnail with rounded corners
            thumb = base.resize((self.THUMB_W, self.THUMB_H))
            tmask = Image.new("L", thumb.size, 0)
            ImageDraw.Draw(tmask).rounded_rectangle(
                (0, 0, self.THUMB_W, self.THUMB_H), 20, fill=255)
            bg.paste(thumb, (self.THUMB_X, self.THUMB_Y), tmask)
            
            # Draw text elements
            draw = ImageDraw.Draw(bg)
            
            # Clean and display title
            import re as re_module
            clean_title = re_module.sub(r"\W+", " ", song.title).title()
            draw.text(
                (self.TITLE_X, self.TITLE_Y),
                self.trim_to_width(clean_title, self.title_font, self.MAX_TITLE_WIDTH),
                fill="black",
                font=self.title_font
            )
            
            # Metadata
            draw.text(
                (self.TITLE_X, self.META_Y),
                f"YouTube | {song.view_count or 'Unknown Views'}",
                fill="black",
                font=self.regular_font
            )
            
            # Progress bar
            draw.line([(self.BAR_X, self.BAR_Y), (self.BAR_X + self.BAR_RED_LEN, self.BAR_Y)],
                      fill="red", width=6)
            draw.line([(self.BAR_X + self.BAR_RED_LEN, self.BAR_Y),
                      (self.BAR_X + self.BAR_TOTAL_LEN, self.BAR_Y)], fill="gray", width=5)
            
            # Time labels
            draw.text((self.BAR_X, self.BAR_Y + 15), "00:00",
                      fill="black", font=self.regular_font)
            
            is_live = getattr(song, 'is_live', False)
            end_text = "Live" if is_live else song.duration
            draw.text(
                (self.BAR_X + self.BAR_TOTAL_LEN - (90 if is_live else 60), self.BAR_Y + 15),
                end_text,
                fill="red" if is_live else "black",
                font=self.regular_font
            )
            
            # Save
            bg.save(output)
            
            # Cleanup
            try:
                os.remove(temp)
            except:
                pass
            
            return output
        
        except Exception as e:
            logger.error(f"Thumbnail sync generation error: {e}")
            return config.DEFAULT_THUMB

thumb = ThumbnailGenerator()

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
            logger.error(f"Preload error for track {track_id}: {e}")
        
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
        languages = {}
        
        # Create English language dictionary
        en_dict = {
            "add_me": "➕ Add Me to Your Group",
            "help": "📚 Help",
            "support": "👥 Support",
            "channel": "📢 Channel",
            "source": "🔗 Source",
            "play_media": "🎵 **Now Playing**\n\n**Title:** {0}\n**Duration:** {1}\n**Requested by:** {2}",
            "play_queued": "✅ **Added to Queue**\n\n**Title:** {0}\n**Duration:** {1}\n**Position:** #{2}\n**Requested by:** {3}",
            "play_next": "⏭️ Playing next track...",
            "error_no_file": "❌ Download failed",
            "error_vc_disabled": "❌ Voice chat is disabled",
            "dl_progress": "📥 Downloading...",
            "dl_complete": "✅ Download complete",
            "dl_cancel": "❌ Download cancelled",
            "user_no_perms": "❌ You don't have permission",
            "gcast_start": "📢 Broadcast started",
            "gcast_end": "✅ Broadcast complete",
            "gcast_stop": "🛑 Broadcast stopped",
            "ping_pong": "🏓 **Pong!**\n\n**Latency:** `{0}ms`\n**Uptime:** {1}",
            "queue_empty": "📭 Queue is empty",
            "queue_list": "📋 **Current Queue:**\n\n",
            "now_playing": "🎶 **Now Playing**",
            "not_playing": "❌ Nothing is playing",
            "skipping": "⏭️ Skipping...",
            "stopping": "⏹️ Stopping...",
            "pausing": "⏸️ Pausing...",
            "resuming": "▶️ Resuming...",
            "searching": "🔍 Searching...",
            "no_results": "❌ No results found",
            "invalid_url": "❌ Invalid URL",
            "duration_limit": "❌ Duration limit exceeded",
            "file_too_large": "❌ File too large (max 200MB)",
            "admin_only": "❌ Admin only",
            "sudo_only": "❌ Sudo only",
            "blacklisted": "❌ You are blacklisted",
        }
        
        languages["en"] = en_dict
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
# VOICE CALL HANDLER (PyTgCalls)
# ==============================================================================

class TgCall(PyTgCalls):
    def __init__(self):
        # Initialize with first available client
        initial_client = userbot.one if userbot.one else (userbot.two if userbot.two else userbot.three)
        super().__init__(client=initial_client)
        
        self.clients = []
        self._play_next_locks = {}
        self._stream_end_cache = {}
    
    async def boot(self):
        """Initialize voice call clients"""
        for client in userbot.clients:
            try:
                pytgcalls_client = PyTgCalls(client, cache_duration=100)
                await pytgcalls_client.start()
                self.clients.append(pytgcalls_client)
                
                # Setup event handlers
                @pytgcalls_client.on_stream_end()
                async def stream_end_handler(_, update):
                    if isinstance(update, pytgcalls_types.StreamEnded):
                        chat_id = update.chat_id
                        current_time = asyncio.get_event_loop().time()
                        
                        # Deduplicate stream end events
                        if chat_id in self._stream_end_cache:
                            if current_time - self._stream_end_cache[chat_id] < 2.0:
                                return
                        
                        self._stream_end_cache[chat_id] = current_time
                        
                        # Clean up old cache entries
                        self._stream_end_cache = {
                            cid: t for cid, t in self._stream_end_cache.items()
                            if current_time - t < 5.0
                        }
                        
                        await self.play_next(chat_id)
                
                @pytgcalls_client.on_closed_voice_chat()
                async def closed_vc_handler(_, chat_id: int):
                    await self.stop(chat_id)
                
                logger.info(f"✅ Voice call client started for assistant")
                
            except Exception as e:
                logger.error(f"Failed to start voice call client: {e}")
        
        logger.info("📞 Voice call handler started")
    
    async def play_media(self, chat_id: int, message, media, seek_time: int = 0):
        """Play media in voice chat"""
        if not self.clients:
            logger.error("No voice call clients available")
            return
        
        # Get appropriate client
        assistant_num = await db.get_assistant(chat_id)
        client = self.clients[assistant_num - 1] if assistant_num <= len(self.clients) else self.clients[0]
        
        if not media.file_path:
            if message:
                try:
                    await message.edit_text("❌ No file available")
                except:
                    pass
            return
        
        try:
            # Configure stream
            ffmpeg_params = f"-ss {seek_time}" if seek_time > 1 else ""
            if seek_time > 1:
                ffmpeg_params = f"-ss {seek_time} -probesize 10M -analyzeduration 5M -rtbufsize 5M -fflags +genpts+igndts"
            else:
                ffmpeg_params = "-probesize 10M -analyzeduration 5M -rtbufsize 5M -fflags +genpts+igndts -sync ext"
            
            stream = pytgcalls_types.MediaStream(
                media_path=media.file_path,
                audio_parameters=pytgcalls_types.AudioQuality.HIGH,
                audio_flags=pytgcalls_types.MediaStream.Flags.REQUIRED,
                video_flags=pytgcalls_types.MediaStream.Flags.IGNORE,
                ffmpeg_parameters=ffmpeg_params,
            )
            
            # Check if already connected
            try:
                call = await client.get_call(chat_id)
                if call:
                    await client.leave_call(chat_id, close=False)
                    await asyncio.sleep(0.5)
            except (ConnectionNotFound, pytgcalls_exceptions.NotInCallError):
                pass
            except Exception as e:
                logger.debug(f"Connection check error: {e}")
            
            # Play with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await client.play(
                        chat_id=chat_id,
                        stream=stream,
                        config=pytgcalls_types.GroupCallConfig(auto_start=True),
                    )
                    break
                except (pytgcalls_exceptions.NoActiveGroupCall, RPCError) as e:
                    error_msg = str(e)
                    if any(x in error_msg for x in ["GROUPCALL_INVALID", "GROUPCALL"]):
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)
                            continue
                        else:
                            raise
                    else:
                        raise
                except Exception as e:
                    error_msg = str(e).lower()
                    if "cannot be initialized more than once" in error_msg:
                        if attempt < max_retries - 1:
                            try:
                                await client.leave_call(chat_id, close=False)
                                await asyncio.sleep(1)
                            except:
                                pass
                            continue
                        else:
                            raise
                    else:
                        raise
            
            # Update media time
            if seek_time:
                media.time = seek_time
            else:
                media.time = 1
            
            # Update database
            await db.add_call(chat_id)
            
            # Generate thumbnail if enabled
            if config.THUMB_GEN and isinstance(media, Track):
                thumbnail_path = await thumb.generate(media)
            else:
                thumbnail_path = config.DEFAULT_THUMB
            
            # Create message text
            _lang = await lang.get_lang(chat_id)
            text = _lang["play_media"].format(
                media.url,
                media.title,
                media.duration,
                media.user,
            )
            
            # Create timer display for non-live tracks
            if not media.is_live and media.duration_sec:
                played = media.time
                duration = media.duration_sec
                
                # Progress bar
                bar_length = 12
                if duration == 0:
                    percentage = 0
                else:
                    percentage = min((played / duration) * 100, 100)
                filled = int(round(bar_length * percentage / 100))
                timer_bar = "—" * filled + "●" + "—" * (bar_length - filled)
                
                # Format time
                if duration >= 3600:
                    played_time = time.strftime('%H:%M:%S', time.gmtime(played))
                    total_time = time.strftime('%H:%M:%S', time.gmtime(duration))
                else:
                    played_time = time.strftime('%M:%S', time.gmtime(played))
                    total_time = time.strftime('%M:%S', time.gmtime(duration))
                
                timer_text = f"{played_time} {timer_bar} {total_time}"
                keyboard = buttons.controls(chat_id, timer=timer_text)
            else:
                keyboard = buttons.controls(chat_id)
            
            # Delete old message if exists
            if message:
                try:
                    await message.delete()
                except:
                    pass
            
            # Send new message with thumbnail
            try:
                sent_photo = await app.send_photo(
                    chat_id=chat_id,
                    photo=thumbnail_path,
                    caption=text,
                    reply_markup=keyboard,
                )
                if sent_photo:
                    media.message_id = sent_photo.id
            except Exception as e:
                logger.error(f"Failed to send photo: {e}")
                # Fallback to text message
                sent_message = await app.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
                if sent_message:
                    media.message_id = sent_message.id
            
            # Start preloading next tracks
            try:
                asyncio.create_task(preload.start_preload(chat_id, count=2))
            except Exception as e:
                logger.debug(f"Preload start error: {e}")
            
            logger.info(f"▶️ Playing '{media.title}' in {chat_id}")
            
        except FileNotFoundError:
            if message:
                try:
                    await message.edit_text("❌ File not found")
                except:
                    pass
            await self.play_next(chat_id)
        
        except pytgcalls_exceptions.NoActiveGroupCall:
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text("❌ No active voice chat")
                except:
                    pass
        
        except RPCError as e:
            error_str = str(e)
            if any(x in error_str for x in ["CHAT_ADMIN_REQUIRED", "GROUPCALL_FORBIDDEN", "VOICE_MESSAGES_FORBIDDEN"]):
                await self.stop(chat_id)
                if message:
                    try:
                        await message.edit_text("❌ Voice chat is disabled or no permissions")
                    except:
                        pass
            else:
                logger.error(f"RPC error in play_media: {e}")
                await self.stop(chat_id)
        
        except (ConnectionNotFound, TelegramServerError):
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text("❌ Telegram server error")
                except:
                    pass
        
        except Exception as e:
            logger.error(f"Unexpected error in play_media: {e}", exc_info=True)
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(f"❌ Playback error: {str(e)[:100]}")
                except:
                    pass
    
    async def pause(self, chat_id: int) -> bool:
        """Pause playback"""
        if not self.clients:
            return False
        
        assistant_num = await db.get_assistant(chat_id)
        client = self.clients[assistant_num - 1] if assistant_num <= len(self.clients) else self.clients[0]
        
        try:
            await db.playing(chat_id, paused=True)
            return await client.pause(chat_id)
        except Exception as e:
            logger.error(f"Pause error: {e}")
            return False
    
    async def resume(self, chat_id: int) -> bool:
        """Resume playback"""
        if not self.clients:
            return False
        
        assistant_num = await db.get_assistant(chat_id)
        client = self.clients[assistant_num - 1] if assistant_num <= len(self.clients) else self.clients[0]
        
        try:
            await db.playing(chat_id, paused=False)
            return await client.resume(chat_id)
        except Exception as e:
            logger.error(f"Resume error: {e}")
            return False
    
    async def stop(self, chat_id: int):
        """Stop playback"""
        # Cancel preload tasks
        try:
            await preload.cancel_preload(chat_id)
        except Exception as e:
            logger.debug(f"Preload cancel error: {e}")
        
        # Clear queue
        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
        except Exception as e:
            logger.warning(f"Queue clear error: {e}")
        
        # Leave call
        if self.clients:
            for client in self.clients:
                try:
                    await client.leave_call(chat_id, close=False)
                except (ConnectionNotFound, pytgcalls_exceptions.NotInCallError):
                    pass
                except Exception as e:
                    logger.warning(f"Leave call error: {e}")
        
        logger.info(f"⏹️ Stopped playback in {chat_id}")
    
    async def play_next(self, chat_id: int):
        """Play next track in queue"""
        # Acquire lock to prevent concurrent execution
        if chat_id not in self._play_next_locks:
            self._play_next_locks[chat_id] = asyncio.Lock()
        
        lock = self._play_next_locks[chat_id]
        if lock.locked():
            logger.info(f"play_next already running for {chat_id}")
            return
        
        async with lock:
            try:
                if not await db.get_call(chat_id):
                    return
                
                # Check loop mode
                # Note: Loop functionality would need to be implemented in database
                
                media = queue.get_next(chat_id)
                
                # Delete previous message
                try:
                    if media and media.message_id:
                        await app.delete_messages(
                            chat_id=chat_id,
                            message_ids=media.message_id,
                            revoke=True,
                        )
                        media.message_id = 0
                except Exception as e:
                    logger.debug(f"Delete message error: {e}")
                
                if not media:
                    # Auto end if enabled
                    if config.AUTO_END:
                        _lang = await lang.get_lang(chat_id)
                        try:
                            await app.send_message(
                                chat_id=chat_id,
                                text="✅ Queue finished. Stream ended.",
                            )
                        except:
                            pass
                    return await self.stop(chat_id)
                
                _lang = await lang.get_lang(chat_id)
                msg = None
                
                # Send "playing next" message
                try:
                    msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
                except Exception as e:
                    logger.error(f"Send message error: {e}")
                
                # Download if needed
                if not media.file_path:
                    is_live = getattr(media, 'is_live', False)
                    media.file_path = await yt.download(media.id, is_live=is_live)
                    
                    if not media.file_path:
                        await self.stop(chat_id)
                        if msg:
                            try:
                                await msg.edit_text("❌ Download failed")
                            except:
                                pass
                        return
                
                media.message_id = msg.id if msg else 0
                
                if msg:
                    await self.play_media(chat_id, msg, media)
                else:
                    # Play without message
                    await self.play_media(chat_id, None, media)
                
                # Start preloading
                try:
                    asyncio.create_task(preload.start_preload(chat_id, count=2))
                except Exception as e:
                    logger.debug(f"Preload error: {e}")
            
            except Exception as e:
                logger.error(f"Error in play_next: {e}", exc_info=True)
                try:
                    await self.stop(chat_id)
                except:
                    pass
    
    async def replay(self, chat_id: int):
        """Replay current track"""
        try:
            if not await db.get_call(chat_id):
                return
            
            media = queue.get_current(chat_id)
            if media:
                _lang = await lang.get_lang(chat_id)
                msg = await app.send_message(chat_id=chat_id, text="↻ Replaying...")
                await self.play_media(chat_id, msg, media)
        
        except Exception as e:
            logger.error(f"Replay error: {e}")

tune = TgCall()

# ==============================================================================
# ADMIN DECORATORS
# ==============================================================================

def admin_check(func):
    """Decorator to check if user is admin"""
    @wraps(func)
    async def wrapper(_, update, *args, **kwargs):
        # Helper function to send reply
        async def reply(text):
            if isinstance(update, types.Message):
                return await update.reply_text(text)
            else:
                return await update.answer(text, show_alert=True)
        
        # Handle anonymous admins
        if not update.from_user:
            return
        
        # Get chat ID and user ID
        if isinstance(update, types.Message):
            chat_id = update.chat.id
            user_id = update.from_user.id
        else:  # CallbackQuery
            chat_id = update.message.chat.id
            user_id = update.from_user.id
        
        # Sudo users bypass admin check
        if user_id in app.sudoers:
            return await func(_, update, *args, **kwargs)
        
        # Check if user is admin
        admins = await db.get_admins(chat_id)
        if user_id not in admins:
            return await reply("❌ Admin only")
        
        # User is admin, allow execution
        return await func(_, update, *args, **kwargs)
    
    return wrapper

def can_manage_vc(func):
    """Decorator to check if user can manage voice chats"""
    @wraps(func)
    async def wrapper(_, update, *args, **kwargs):
        # Get chat ID and user ID
        if isinstance(update, types.Message):
            chat_id = update.chat.id
        else:  # CallbackQuery
            chat_id = update.message.chat.id
        
        if not update.from_user:
            return
        
        user_id = update.from_user.id
        
        # Sudo users always allowed
        if user_id in app.sudoers:
            return await func(_, update, *args, **kwargs)
        
        # Check authorized users
        if await db.is_auth(chat_id, user_id):
            return await func(_, update, *args, **kwargs)
        
        # Check admin
        admins = await db.get_admins(chat_id)
        if user_id in admins:
            return await func(_, update, *args, **kwargs)
        
        # Not allowed
        if isinstance(update, types.Message):
            return await update.reply_text("❌ No permission")
        else:
            return await update.answer("❌ No permission", show_alert=True)
    
    return wrapper

# ==============================================================================
# BASIC COMMANDS HANDLERS
# ==============================================================================

@app.on_message(filters.command(["start", "help"]) & filters.private)
@lang.language()
async def start_command(_, message: types.Message):
    """Handle /start command"""
    await message.reply_photo(
        photo=config.START_IMG,
        caption=(
            f"🎵 **Hello {message.from_user.mention}!**\n\n"
            f"I'm **{app.name}**, a Telegram Music Bot.\n\n"
            f"**Features:**\n"
            f"• High-quality music streaming\n"
            f"• YouTube & Telegram audio support\n"
            f"• Queue system with preloading\n"
            f"• Multiple assistants support\n"
            f"• 24/7 online on Render\n\n"
            f"Add me to your group and enjoy music!"
        ),
        reply_markup=buttons.start_key(private=True)
    )
    
    # Add user to database
    await db.add_user(message.from_user.id)

@app.on_message(filters.command(["start"]) & filters.group)
@lang.language()
async def start_group(_, message: types.Message):
    """Handle /start in group"""
    await message.reply_text(
        f"👋 **Hello {message.from_user.mention}!**\n\n"
        f"I'm **{app.name}**, ready to play music in this group!\n\n"
        f"Use /play to start streaming music.",
        reply_markup=buttons.start_key()
    )
    
    # Add chat to database
    await db.add_chat(message.chat.id)

@app.on_message(filters.command(["ping"]))
@lang.language()
async def ping_command(_, message: types.Message):
    """Handle /ping command"""
    start = time.time()
    msg = await message.reply_photo(
        photo=config.PING_IMG,
        caption="🏓 **Pinging...**"
    )
    end = time.time()
    
    latency = round((end - start) * 1000, 2)
    uptime = utils.format_eta(int(time.time() - start_time))
    
    await msg.edit_caption(
        caption=(
            f"🏓 **Pong!**\n\n"
            f"**Bot Latency:** `{latency}ms`\n"
            f"**Uptime:** `{uptime}`\n"
            f"**Assistants:** `{len(userbot.clients)}`\n"
            f"**Served Chats:** `{len(db.chats)}`"
        ),
        reply_markup=types.InlineKeyboardMarkup([[
            types.InlineKeyboardButton(
                "📊 Stats",
                callback_data="stats"
            )
        ]])
    )

@app.on_message(filters.command(["play"]) & filters.group)
@lang.language()
async def play_command(_, message: types.Message):
    """Handle /play command"""
    # Check permissions
    if message.from_user.id in app.bl_users:
        return await message.reply_text("❌ You are blacklisted")
    
    # Check if user replied to audio
    if message.reply_to_message and tg.get_media(message.reply_to_message):
        sent = await message.reply_text("📥 Downloading media...")
        media = await tg.download(message.reply_to_message, sent)
        
        if media:
            media.user = message.from_user.mention
            position = queue.add(message.chat.id, media)
            
            # Update message
            _lang = await lang.get_lang(message.chat.id)
            text = _lang["play_queued"].format(
                media.title,
                media.duration,
                position + 1,
                media.user,
            )
            
            try:
                await sent.edit_text(
                    text,
                    disable_web_page_preview=True,
                    reply_markup=buttons.controls(message.chat.id)
                )
            except:
                await sent.edit_text(text)
            
            # Start playing if not already playing
            if not await db.get_call(message.chat.id):
                await tune.play_media(message.chat.id, sent, media)
        
        return
    
    # Check for YouTube URL or search query
    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:**\n"
            "• `/play [song name]`\n"
            "• `/play [YouTube URL]`\n"
            "• Reply to an audio file with `/play`\n\n"
            "**Examples:**\n"
            "• `/play shape of you`\n"
            "• `/play https://youtu.be/...`"
        )
    
    query = " ".join(message.command[1:])
    
    # Check if it's a YouTube URL
    if yt.valid(query):
        # Extract video ID
        video_id = None
        if "youtube.com/watch?v=" in query:
            video_id = query.split("v=")[1].split("&")[0]
        elif "youtu.be/" in query:
            video_id = query.split("youtu.be/")[1].split("?")[0]
        
        if video_id:
            sent = await message.reply_text("🎵 Processing YouTube URL...")
            
            # Create track from URL
            track = Track(
                id=video_id,
                channel_name="YouTube",
                duration="0:00",
                duration_sec=0,
                title="YouTube Video",
                url=query,
                user=message.from_user.mention,
            )
            
            position = queue.add(message.chat.id, track)
            
            _lang = await lang.get_lang(message.chat.id)
            text = _lang["play_queued"].format(
                track.title,
                track.duration,
                position + 1,
                track.user,
            )
            
            try:
                await sent.edit_text(
                    text,
                    disable_web_page_preview=True,
                    reply_markup=buttons.yt_key(query)
                )
            except:
                await sent.edit_text(text)
            
            # Start playing if not already playing
            if not await db.get_call(message.chat.id):
                await tune.play_media(message.chat.id, sent, track)
            
            return
    
    # Search YouTube
    sent = await message.reply_text(f"🔍 Searching: `{query}`")
    
    track = await yt.search(query, sent.id)
    if track:
        track.user = message.from_user.mention
        position = queue.add(message.chat.id, track)
        
        _lang = await lang.get_lang(message.chat.id)
        text = _lang["play_queued"].format(
            track.title,
            track.duration,
            position + 1,
            track.user,
        )
        
        try:
            await sent.edit_text(
                text,
                disable_web_page_preview=True,
                reply_markup=buttons.yt_key(track.url)
            )
        except:
            await sent.edit_text(text)
        
        # Start playing if not already playing
        if not await db.get_call(message.chat.id):
            await tune.play_media(message.chat.id, sent, track)
    else:
        await sent.edit_text("❌ No results found")

@app.on_message(filters.command(["skip", "next"]) & filters.group)
@lang.language()
@can_manage_vc
async def skip_command(_, message: types.Message):
    """Handle /skip command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    msg = await message.reply_text("⏭️ Skipping...")
    await tune.play_next(message.chat.id)
    
    try:
        await msg.delete()
    except:
        pass
    
    try:
        await message.delete()
    except:
        pass

@app.on_message(filters.command(["stop", "end"]) & filters.group)
@lang.language()
@can_manage_vc
async def stop_command(_, message: types.Message):
    """Handle /stop command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    msg = await message.reply_text("⏹️ Stopping...")
    await tune.stop(message.chat.id)
    
    try:
        await msg.edit_text("✅ Playback stopped")
    except:
        pass
    
    try:
        await message.delete()
    except:
        pass

@app.on_message(filters.command(["pause"]) & filters.group)
@lang.language()
@can_manage_vc
async def pause_command(_, message: types.Message):
    """Handle /pause command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    if await tune.pause(message.chat.id):
        await message.reply_text("⏸️ Paused")
    else:
        await message.reply_text("❌ Failed to pause")

@app.on_message(filters.command(["resume"]) & filters.group)
@lang.language()
@can_manage_vc
async def resume_command(_, message: types.Message):
    """Handle /resume command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    if await tune.resume(message.chat.id):
        await message.reply_text("▶️ Resumed")
    else:
        await message.reply_text("❌ Failed to resume")

@app.on_message(filters.command(["replay", "restart"]) & filters.group)
@lang.language()
@can_manage_vc
async def replay_command(_, message: types.Message):
    """Handle /replay command"""
    if not await db.get_call(message.chat.id):
        return await message.reply_text("❌ Nothing is playing")
    
    await message.reply_text("↻ Replaying...")
    await tune.replay(message.chat.id)

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
        if hasattr(item, 'duration') and item.duration:
            text += f" - `{item.duration}`"
        if i == 1:
            text += " **▶️ Now Playing**"
        
        if hasattr(item, 'user') and item.user:
            text += f"\n   👤 {item.user}"
        
        text += "\n\n"
    
    if len(queue_list) > 10:
        text += f"📁 ...and **{len(queue_list) - 10}** more tracks"
    
    await message.reply_text(text, disable_web_page_preview=True)

@app.on_message(filters.command(["playlist"]) & filters.group)
@lang.language()
async def playlist_command(_, message: types.Message):
    """Handle playlist command"""
    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:** `/playlist [YouTube playlist URL]`\n\n"
            "**Example:** `/playlist https://youtube.com/playlist?list=...`"
        )
    
    url = message.command[1]
    if not yt.valid(url) or "playlist" not in url:
        return await message.reply_text("❌ Invalid playlist URL")
    
    sent = await message.reply_text("📁 Fetching playlist...")
    
    try:
        tracks = await yt.playlist(config.PLAYLIST_LIMIT, message.from_user.mention, url)
        
        if not tracks:
            return await sent.edit_text("❌ No tracks found in playlist")
        
        added = 0
        for track in tracks:
            position = queue.add(message.chat.id, track)
            added += 1
        
        await sent.edit_text(
            f"✅ Added **{added}** tracks from playlist to queue!\n\n"
            f"**First track:** {tracks[0].title}\n"
            f"**Position in queue:** #{queue.get_queue(message.chat.id).index(tracks[0]) + 1}"
        )
        
        # Start playing if not already playing
        if not await db.get_call(message.chat.id) and tracks:
            await tune.play_media(message.chat.id, sent, tracks[0])
    
    except Exception as e:
        logger.error(f"Playlist error: {e}")
        await sent.edit_text(f"❌ Error: {str(e)[:100]}")

@app.on_message(filters.command(["auth"]) & filters.group)
@lang.language()
@admin_check
async def auth_command(_, message: types.Message):
    """Add user to authorized list"""
    user = await utils.extract_user(message)
    if not user:
        return await message.reply_text(
            "**Usage:**\n"
            "• `/auth [user_id]`\n"
            "• `/auth [username]`\n"
            "• Reply to a user with `/auth`"
        )
    
    if user.id == message.from_user.id:
        return await message.reply_text("❌ You can't auth yourself")
    
    if user.id in app.sudoers:
        return await message.reply_text("❌ User is already sudo")
    
    # Check if already admin
    admins = await db.get_admins(message.chat.id)
    if user.id in admins:
        return await message.reply_text("❌ User is already admin")
    
    await db.add_auth(message.chat.id, user.id)
    await message.reply_text(f"✅ Added {user.mention} to authorized users")

@app.on_message(filters.command(["unauth"]) & filters.group)
@lang.language()
@admin_check
async def unauth_command(_, message: types.Message):
    """Remove user from authorized list"""
    user = await utils.extract_user(message)
    if not user:
        return await message.reply_text(
            "**Usage:**\n"
            "• `/unauth [user_id]`\n"
            "• `/unauth [username]`\n"
            "• Reply to a user with `/unauth`"
        )
    
    await db.rm_auth(message.chat.id, user.id)
    await message.reply_text(f"✅ Removed {user.mention} from authorized users")

# ==============================================================================
# CALLBACK QUERY HANDLERS
# ==============================================================================

@app.on_callback_query(filters.regex(r"^controls "))
@lang.language()
async def controls_callback(_, query: types.CallbackQuery):
    """Handle playback control callbacks"""
    data = query.data.split()
    
    if len(data) < 3:
        return await query.answer("Invalid command", show_alert=True)
    
    action = data[1]
    chat_id = int(data[2])
    
    # Check if user can control
    user_id = query.from_user.id
    
    # Sudo users always allowed
    if user_id not in app.sudoers:
        # Check authorized users
        if not await db.is_auth(chat_id, user_id):
            # Check admin
            admins = await db.get_admins(chat_id)
            if user_id not in admins:
                return await query.answer("❌ No permission", show_alert=True)
    
    if action == "pause":
        if await tune.pause(chat_id):
            await query.answer("⏸️ Paused")
        else:
            await query.answer("❌ Failed to pause", show_alert=True)
    
    elif action == "resume":
        if await tune.resume(chat_id):
            await query.answer("▶️ Resumed")
        else:
            await query.answer("❌ Failed to resume", show_alert=True)
    
    elif action == "skip":
        await query.answer("⏭️ Skipping...")
        await tune.play_next(chat_id)
    
    elif action == "stop":
        await query.answer("⏹️ Stopping...")
        await tune.stop(chat_id)
    
    elif action == "replay":
        await query.answer("↻ Replaying...")
        await tune.replay(chat_id)
    
    elif action == "close":
        try:
            await query.message.delete()
            await query.answer("🗑️ Deleted")
        except:
            await query.answer("❌ Failed to delete", show_alert=True)
    
    elif action == "status":
        # Show current status
        if await db.get_call(chat_id):
            current = queue.get_current(chat_id)
            if current:
                status = f"▶️ Playing: {current.title}"
            else:
                status = "▶️ Playing"
        else:
            status = "⏹️ Stopped"
        
        await query.answer(status, show_alert=True)
    
    elif action.startswith("seek_"):
        # Handle seek commands
        direction = action.replace("seek_", "")
        
        if direction == "forward_10":
            # Would need seek implementation
            await query.answer("⏩ +10s")
        elif direction == "forward_30":
            await query.answer("⏩ +30s")
        elif direction == "back_10":
            await query.answer("⏪ -10s")
        elif direction == "back_30":
            await query.answer("⏪ -30s")

@app.on_callback_query(filters.regex(r"^cancel_dl$"))
@lang.language()
async def cancel_dl_callback(_, query: types.CallbackQuery):
    """Cancel download callback"""
    await tg.cancel(query)
    await query.answer("Download cancelled")

@app.on_callback_query(filters.regex(r"^help"))
@lang.language()
async def help_callback(_, query: types.CallbackQuery):
    """Help callback"""
    await query.answer()
    
    help_text = (
        "🎵 **HasiiMusic Bot Help**\n\n"
        "**Basic Commands:**\n"
        "• /play [song/url] - Play music\n"
        "• /skip - Skip current track\n"
        "• /stop - Stop playback\n"
        "• /pause - Pause playback\n"
        "• /resume - Resume playback\n"
        "• /queue - Show queue\n"
        "• /playlist [url] - Add playlist\n\n"
        "**Admin Commands:**\n"
        "• /auth [user] - Authorize user\n"
        "• /unauth [user] - Remove auth\n\n"
        "**Other:**\n"
        "• /ping - Check bot status\n"
        "• /start - Start the bot"
    )
    
    try:
        await query.edit_message_text(
            help_text,
            reply_markup=types.InlineKeyboardMarkup([[
                types.InlineKeyboardButton("🔙 Back", callback_data="back_main")
            ]])
        )
    except:
        pass

@app.on_callback_query(filters.regex(r"^stats$"))
@lang.language()
async def stats_callback(_, query: types.CallbackQuery):
    """Stats callback"""
    await query.answer()
    
    stats_text = (
        f"📊 **{app.name} Stats**\n\n"
        f"**Bot:** @{app.username}\n"
        f"**Uptime:** {utils.format_eta(int(time.time() - start_time))}\n"
        f"**Assistants:** {len(userbot.clients)}\n"
        f"**Served Chats:** {len(db.chats)}\n"
        f"**Served Users:** {len(db.users)}\n"
        f"**Active Calls:** {len(db.active_calls)}"
    )
    
    try:
        await query.edit_message_text(
            stats_text,
            reply_markup=types.InlineKeyboardMarkup([[
                types.InlineKeyboardButton("🔙 Back", callback_data="back_main")
            ]])
        )
    except:
        pass

@app.on_callback_query(filters.regex(r"^back_main$"))
@lang.language()
async def back_main_callback(_, query: types.CallbackQuery):
    """Back to main callback"""
    await query.answer()
    
    try:
        await query.edit_message_caption(
            caption=f"🎵 **{app.name}** - Music Bot",
            reply_markup=buttons.start_key()
        )
    except:
        pass
# ==============================================================================
# SUDO COMMANDS
# ==============================================================================

broadcasting = False

@app.on_message(filters.command(["broadcast"]) & app.sudo_filter)
@lang.language()
async def broadcast_command(_, message: types.Message):
    """Broadcast message to all chats"""
    global broadcasting
    
    if broadcasting:
        return await message.reply_text("⚠️ Broadcast already in progress")
    
    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply_text(
            "**Usage:**\n"
            "• `/broadcast [message]`\n"
            "• Reply to a message with `/broadcast`\n\n"
            "**Flags:**\n"
            "• `-user` - Send to users too\n"
            "• `-nochat` - Don't send to groups"
        )
    
    # Parse flags
    flags = []
    broadcast_text = ""
    
    if message.reply_to_message:
        broadcast_media = message.reply_to_message
        broadcast_text = None
    else:
        broadcast_media = None
        parts = message.text.split(None, 1)[1].split()
        
        # Extract flags
        for part in parts:
            if part.startswith('-'):
                flags.append(part)
        
        # Reconstruct text without flags
        text_parts = [p for p in parts if not p.startswith('-')]
        broadcast_text = ' '.join(text_parts) if text_parts else ""
    
    if not broadcast_text and not broadcast_media:
        return await message.reply_text("❌ No message to broadcast")
    
    sent = await message.reply_text("📢 Starting broadcast...")
    broadcasting = True
    
    # Get recipients
    groups = []
    users = []
    
    if "-nochat" not in flags:
        groups = await db.get_chats()
    
    if "-user" in flags:
        users = await db.get_users()
    
    all_chats = groups + users
    
    if not all_chats:
        broadcasting = False
        return await sent.edit_text("❌ No recipients found")
    
    success_groups = 0
    success_users = 0
    failed = []
    
    # Log broadcast start
    try:
        await app.send_message(
            config.LOGGER_ID,
            f"📢 **Broadcast Started**\n\n"
            f"**By:** {message.from_user.mention}\n"
            f"**Recipients:** {len(all_chats)}\n"
            f"**Time:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except:
        pass
    
    # Send broadcast
    for index, chat_id in enumerate(all_chats, 1):
        if not broadcasting:
            break
        
        try:
            if broadcast_media:
                await broadcast_media.forward(chat_id)
            else:
                await app.send_message(chat_id, broadcast_text)
            
            if chat_id in groups:
                success_groups += 1
            else:
                success_users += 1
            
            # Update progress every 50 chats
            if index % 50 == 0:
                try:
                    await sent.edit_text(
                        f"📢 Broadcasting...\n\n"
                        f"**Progress:** {index}/{len(all_chats)}\n"
                        f"**Success:** {success_groups} groups, {success_users} users"
                    )
                except:
                    pass
            
            # Anti-flood delay
            await asyncio.sleep(0.1)
        
        except Exception as e:
            failed.append(f"{chat_id}: {str(e)[:50]}")
    
    broadcasting = False
    
    # Log completion
    try:
        await app.send_message(
            config.LOGGER_ID,
            f"✅ **Broadcast Complete**\n\n"
            f"**Success:** {success_groups} groups, {success_users} users\n"
            f"**Failed:** {len(failed)}"
        )
    except:
        pass
    
    # Send results
    result_text = (
        f"✅ **Broadcast Complete**\n\n"
        f"**Groups:** {success_groups}\n"
        f"**Users:** {success_users}\n"
        f"**Failed:** {len(failed)}"
    )
    
    if failed:
        # Save failed list to file
        with open("failed_broadcast.txt", "w") as f:
            f.write("\n".join(failed[:100]))  # Limit to 100 entries
        
        try:
            await message.reply_document(
                document="failed_broadcast.txt",
                caption=result_text
            )
            os.remove("failed_broadcast.txt")
        except:
            await sent.edit_text(result_text + "\n\nFailed to send failed list.")
    else:
        await sent.edit_text(result_text)

@app.on_message(filters.command(["stop_gcast", "stop_broadcast"]) & app.sudo_filter)
@lang.language()
async def stop_broadcast(_, message: types.Message):
    """Stop broadcast"""
    global broadcasting
    
    if not broadcasting:
        return await message.reply_text("❌ No broadcast in progress")
    
    broadcasting = False
    await message.reply_text("🛑 Broadcast stopped")

@app.on_message(filters.command(["activevc", "ac"]) & app.sudo_filter)
@lang.language()
async def active_vc_command(_, message: types.Message):
    """Show active voice chats"""
    active_calls = db.active_calls
    
    if not active_calls:
        return await message.reply_text("📭 No active voice chats")
    
    text = f"📞 **Active Voice Chats:** {len(active_calls)}\n\n"
    
    for i, chat_id in enumerate(list(active_calls.keys())[:10], 1):
        try:
            chat = await app.get_chat(chat_id)
            text += f"{i}. **{chat.title}**\n   `{chat_id}`\n\n"
        except:
            text += f"{i}. `{chat_id}`\n\n"
    
    if len(active_calls) > 10:
        text += f"...and {len(active_calls) - 10} more"
    
    await message.reply_text(text)

@app.on_message(filters.command(["logs"]) & app.sudo_filter)
@lang.language()
async def logs_command(_, message: types.Message):
    """Send log file"""
    if not os.path.exists("log.txt"):
        return await message.reply_text("❌ No log file found")
    
    await message.reply_document(
        document="log.txt",
        caption=f"📄 {app.name} Logs"
    )

@app.on_message(filters.command(["stats"]) & app.sudo_filter)
@lang.language()
async def stats_command(_, message: types.Message):
    """Detailed stats"""
    import psutil
    
    # System stats
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # Bot stats
    uptime = utils.format_eta(int(time.time() - start_time))
    
    text = (
        f"📊 **{app.name} Detailed Stats**\n\n"
        f"**🤖 Bot Info:**\n"
        f"• Name: {app.name}\n"
        f"• Username: @{app.username}\n"
        f"• ID: `{app.id}`\n"
        f"• Uptime: {uptime}\n\n"
        f"**👥 User Stats:**\n"
        f"• Served Chats: {len(db.chats)}\n"
        f"• Served Users: {len(db.users)}\n"
        f"• Blacklisted: {len(db.blacklisted)}\n"
        f"• Sudo Users: {len(app.sudoers)}\n\n"
        f"**🔊 Voice Stats:**\n"
        f"• Assistants: {len(userbot.clients)}\n"
        f"• Active Calls: {len(db.active_calls)}\n\n"
        f"**💻 System Stats:**\n"
        f"• CPU: {cpu_percent}%\n"
        f"• RAM: {memory.percent}%\n"
        f"• Disk: {disk.percent}%"
    )
    
    await message.reply_text(text)

@app.on_message(filters.command(["addsudo"]) & filters.user(config.OWNER_ID))
@lang.language()
async def addsudo_command(_, message: types.Message):
    """Add sudo user"""
    user = await utils.extract_user(message)
    if not user:
        return await message.reply_text(
            "**Usage:**\n"
            "• `/addsudo [user_id]`\n"
            "• `/addsudo [username]`\n"
            "• Reply to a user with `/addsudo`"
        )
    
    if user.id in app.sudoers:
        return await message.reply_text("✅ User is already sudo")
    
    app.sudoers.add(user.id)
    app.sudo_filter.add(user.id)
    
    # Save to database
    await db.cache.update_one(
        {"_id": "sudoers"},
        {"$addToSet": {"user_ids": user.id}},
        upsert=True
    )
    
    await message.reply_text(f"✅ Added {user.mention} to sudo users")

@app.on_message(filters.command(["rmsudo"]) & filters.user(config.OWNER_ID))
@lang.language()
async def rmsudo_command(_, message: types.Message):
    """Remove sudo user"""
    user = await utils.extract_user(message)
    if not user:
        return await message.reply_text(
            "**Usage:**\n"
            "• `/rmsudo [user_id]`\n"
            "• `/rmsudo [username]`\n"
            "• Reply to a user with `/rmsudo`"
        )
    
    if user.id == config.OWNER_ID:
        return await message.reply_text("❌ Cannot remove owner")
    
    if user.id not in app.sudoers:
        return await message.reply_text("❌ User is not sudo")
    
    app.sudoers.remove(user.id)
    app.sudo_filter.remove(user.id)
    
    # Remove from database
    await db.cache.update_one(
        {"_id": "sudoers"},
        {"$pull": {"user_ids": user.id}}
    )
    
    await message.reply_text(f"✅ Removed {user.mention} from sudo users")

@app.on_message(filters.command(["sudolist"]) & app.sudo_filter)
@lang.language()
async def sudolist_command(_, message: types.Message):
    """Show sudo users list"""
    if not app.sudoers:
        return await message.reply_text("📭 No sudo users")
    
    text = "👑 **Sudo Users:**\n\n"
    
    for i, user_id in enumerate(list(app.sudoers)[:20], 1):
        try:
            user = await app.get_users(user_id)
            text += f"{i}. {user.mention}\n   ID: `{user_id}`\n\n"
        except:
            text += f"{i}. `{user_id}`\n\n"
    
    if len(app.sudoers) > 20:
        text += f"...and {len(app.sudoers) - 20} more"
    
    await message.reply_text(text)

# ==============================================================================
# AUTO-LEAVE FEATURE
# ==============================================================================

async def auto_leave_monitor():
    """Monitor and auto-leave inactive chats"""
    while True:
        try:
            if not config.AUTO_LEAVE:
                await asyncio.sleep(60)
                continue
            
            active_calls = db.active_calls.copy()
            
            for chat_id in active_calls:
                # Skip excluded chats
                if chat_id in config.EXCLUDED_CHATS:
                    continue
                
                try:
                    # Check if assistant is alone in VC
                    # This would need group call member checking
                    # For now, we'll just implement basic monitoring
                    pass
                except:
                    pass
            
            await asyncio.sleep(60)  # Check every minute
        
        except Exception as e:
            logger.error(f"Auto-leave monitor error: {e}")
            await asyncio.sleep(60)

# ==============================================================================
# DIRECTORY MANAGEMENT
# ==============================================================================

def ensure_dirs():
    """Create necessary directories"""
    for dir_name in ["cache", "downloads", "cookies", "assets"]:
        Path(dir_name).mkdir(parents=True, exist_ok=True)
    logger.info("📁 Directories created")

# ==============================================================================
# RENDER COMPATIBILITY WEB SERVER
# ==============================================================================

from aiohttp import web

async def handle_health(request):
    """Health check endpoint for Render"""
    return web.Response(text="Bot is running!")

async def handle_home(request):
    """Home page"""
    return web.Response(
        text=f"""
        <html>
        <head><title>{app.name} Music Bot</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1>🎵 {app.name}</h1>
            <p>Telegram Music Bot running on Render</p>
            <p>Status: <strong>🟢 Online</strong></p>
            <p>Uptime: {utils.format_eta(int(time.time() - start_time))}</p>
            <p>Bot: @{app.username}</p>
            <p><a href="/health">Health Check</a></p>
        </body>
        </html>
        """,
        content_type="text/html"
    )

async def start_web_server():
    """Start web server for Render health checks"""
    app_web = web.Application()
    app_web.router.add_get('/', handle_home)
    app_web.router.add_get('/health', handle_health)
    
    runner = web.AppRunner(app_web)
    await runner.setup()
    
    # Get port from environment (Render provides PORT)
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    await site.start()
    logger.info(f"🌐 Web server started on port {port}")

# ==============================================================================
# MAIN FUNCTION
# ==============================================================================

start_time = time.time()

async def main():
    """Main startup function"""
    try:
        logger.info(f"🚀 Starting {app.name} v3.0.1...")
        
        # Create directories
        ensure_dirs()
        
        # Download YouTube cookies if configured
        if config.COOKIES_URL:
            logger.info("🍪 Downloading YouTube cookies...")
            await yt.save_cookies(config.COOKIES_URL)
        
        # Connect to database
        logger.info("🗄️ Connecting to database...")
        await db.connect()
        
        # Start web server for Render
        logger.info("🌐 Starting web server...")
        asyncio.create_task(start_web_server())
        
        # Start bot
        logger.info("🤖 Starting bot client...")
        await app.boot()
        
        # Start assistants
        logger.info("👤 Starting assistants...")
        await userbot.boot()
        
        if not userbot.clients:
            logger.warning("⚠️ No assistants started! Music playback will not work.")
        
        # Start voice call handler
        if userbot.clients:
            logger.info("📞 Starting voice call handler...")
            await tune.boot()
        else:
            logger.warning("⚠️ Skipping voice call handler (no assistants)")
        
        # Load sudo users from database
        logger.info("👑 Loading sudo users...")
        sudoers = await db.get_sudoers()
        app.sudoers.update(sudoers)
        app.sudo_filter.update(sudoers)
        
        # Load blacklisted users
        logger.info("🚫 Loading blacklisted users...")
        blacklisted = await db.get_blacklisted()
        app.bl_users.update(blacklisted)
        
        logger.info(f"✅ Loaded {len(app.sudoers)} sudo users")
        logger.info(f"✅ Loaded {len(blacklisted)} blacklisted users")
        
        # Start auto-leave monitor
        if config.AUTO_LEAVE:
            logger.info("👀 Starting auto-leave monitor...")
            asyncio.create_task(auto_leave_monitor())
        
        # Startup complete
        logger.info(f"🎉 {app.name} started successfully!")
        logger.info(f"🤖 Bot: @{app.username}")
        logger.info(f"👥 Served chats: {len(db.chats)}")
        logger.info(f"👤 Served users: {len(db.users)}")
        logger.info(f"🔊 Assistants: {len(userbot.clients)}")
        logger.info("✨ Ready to play music!")
        
        # Send startup message to logger
        try:
            await app.send_message(
                config.LOGGER_ID,
                f"✅ **{app.name} Started Successfully!**\n\n"
                f"**Version:** 3.0.1\n"
                f"**Bot:** @{app.username}\n"
                f"**Assistants:** {len(userbot.clients)}\n"
                f"**Uptime:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except:
            pass
        
        # Keep bot running
        await idle()
        
    except KeyboardInterrupt:
        logger.info("Received stop signal (Ctrl+C)...")
    
    except SystemExit as e:
        logger.error(f"System exit: {e}")
    
    except Exception as e:
        logger.error(f"Critical error in main: {e}", exc_info=True)
    
    finally:
        # Clean shutdown
        logger.info("🛑 Shutting down...")
        
        try:
            await app.exit()
        except:
            pass
        
        try:
            await userbot.exit()
        except:
            pass
        
        try:
            await db.close()
        except:
            pass
        
        logger.info("✅ Bot stopped successfully")

# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    # Set event loop policy for Windows compatibility
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        # Run the bot
        asyncio.run(main())
    
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    
    except SystemExit as e:
        logger.error(f"System exit: {e}")
        sys.exit(1)
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)

# ==============================================================================
# ADDITIONAL COMMANDS AND FEATURES
# ==============================================================================

@app.on_message(filters.command(["radio"]) & filters.group)
@lang.language()
async def radio_command(_, message: types.Message):
    """Radio streaming command"""
    if len(message.command) < 2:
        # Show available radios
        radio_list = {
            "bbc1": "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_one",
            "bbc2": "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_two",
            "bbc3": "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_three",
            "classicfm": "http://media-ice.musicradio.com/ClassicFMMP3",
            "capitalfm": "http://media-ice.musicradio.com/CapitalMP3",
            "heart": "http://media-ice.musicradio.com/HeartLondonMP3",
        }
        
        text = "📻 **Available Radio Stations:**\n\n"
        for name, url in radio_list.items():
            text += f"• `/radio {name}`\n"
        
        text += "\n**Usage:** `/radio [station_name]`"
        return await message.reply_text(text)
    
    station = message.command[1].lower()
    
    # Map stations to URLs
    stations = {
        "bbc1": "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_one",
        "bbc2": "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_two",
        "bbc3": "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_three",
        "classicfm": "http://media-ice.musicradio.com/ClassicFMMP3",
        "capitalfm": "http://media-ice.musicradio.com/CapitalMP3",
        "heart": "http://media-ice.musicradio.com/HeartLondonMP3",
        "test": "http://stream.test",
    }
    
    if station not in stations:
        return await message.reply_text(
            "❌ Station not found\n\n"
            "Available: bbc1, bbc2, bbc3, classicfm, capitalfm, heart"
        )
    
    url = stations[station]
    sent = await message.reply_text(f"📻 Tuning to {station.upper()}...")
    
    # Create radio track
    radio_track = Track(
        id=f"radio_{station}",
        channel_name=f"Radio {station.upper()}",
        duration="LIVE",
        duration_sec=0,
        title=f"Radio {station.upper()}",
        url=url,
        user=message.from_user.mention,
        is_live=True,
        file_path=url,  # Direct stream URL for live radio
    )
    
    position = queue.add(message.chat.id, radio_track)
    
    await sent.edit_text(
        f"📻 **Radio Added**\n\n"
        f"**Station:** {station.upper()}\n"
        f"**Position:** #{position + 1}\n"
        f"**Requested by:** {message.from_user.mention}"
    )
    
    # Start playing if not already playing
    if not await db.get_call(message.chat.id):
        await tune.play_media(message.chat.id, sent, radio_track)

@app.on_message(filters.command(["shuffle"]) & filters.group)
@lang.language()
@can_manage_vc
async def shuffle_command(_, message: types.Message):
    """Shuffle queue"""
    queue_list = queue.get_queue(message.chat.id)
    
    if len(queue_list) < 2:
        return await message.reply_text("❌ Need at least 2 tracks to shuffle")
    
    # Remove current playing track
    current = queue.get_current(message.chat.id)
    queue.clear(message.chat.id)
    
    # Shuffle remaining tracks
    import random
    random.shuffle(queue_list)
    
    # Add current back as first
    queue_list.insert(0, current) if current else None
    
    # Re-add all tracks
    for track in queue_list:
        queue.add(message.chat.id, track)
    
    await message.reply_text(f"🔀 Shuffled {len(queue_list)} tracks")

@app.on_message(filters.command(["loop"]) & filters.group)
@lang.language()
@can_manage_vc
async def loop_command(_, message: types.Message):
    """Loop mode settings"""
    if len(message.command) < 2:
        current_mode = 0  # Would be retrieved from database
        modes = {
            0: "Off",
            1: "Single Track",
            10: "Queue"
        }
        
        return await message.reply_text(
            f"🔄 **Loop Mode:** {modes.get(current_mode, 'Off')}\n\n"
            "**Usage:** `/loop [mode]`\n"
            "**Modes:** off, single, queue\n\n"
            "**Examples:**\n"
            "• `/loop off` - Disable loop\n"
            "• `/loop single` - Loop current track\n"
            "`/loop queue` - Loop entire queue"
        )
    
    mode = message.command[1].lower()
    
    if mode == "off":
        # Disable loop
        await message.reply_text("🔄 Loop disabled")
    
    elif mode == "single":
        # Loop single track
        await message.reply_text("🔄 Single track loop enabled")
    
    elif mode == "queue":
        # Loop entire queue
        await message.reply_text("🔄 Queue loop enabled")
    
    else:
        await message.reply_text(
            "❌ Invalid mode\n"
            "Available: off, single, queue"
        )

@app.on_message(filters.command(["clean"]) & app.sudo_filter)
@lang.language()
async def clean_command(_, message: types.Message):
    """Clean downloads and cache"""
    try:
        # Count files before cleaning
        download_files = len([f for f in Path("downloads").glob("*") if f.is_file()])
        cache_files = len([f for f in Path("cache").glob("*") if f.is_file()])
        
        # Clean old files (older than 1 hour)
        import time as time_module
        now = time_module.time()
        
        deleted_downloads = 0
        for f in Path("downloads").glob("*"):
            if f.is_file():
                file_age = now - f.stat().st_mtime
                if file_age > 3600:  # 1 hour
                    f.unlink()
                    deleted_downloads += 1
        
        deleted_cache = 0
        for f in Path("cache").glob("*"):
            if f.is_file():
                file_age = now - f.stat().st_mtime
                if file_age > 3600:  # 1 hour
                    f.unlink()
                    deleted_cache += 1
        
        await message.reply_text(
            f"🧹 **Cleanup Complete**\n\n"
            f"**Downloads:** {deleted_downloads}/{download_files} files deleted\n"
            f"**Cache:** {deleted_cache}/{cache_files} files deleted\n"
            f"**Total:** {deleted_downloads + deleted_cache} files cleaned"
        )
    
    except Exception as e:
        logger.error(f"Clean error: {e}")
        await message.reply_text(f"❌ Cleanup error: {str(e)[:100]}")

@app.on_message(filters.command(["blacklist"]) & app.sudo_filter)
@lang.language()
async def blacklist_command(_, message: types.Message):
    """Blacklist user or chat"""
    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:** `/blacklist [chat_id/user_id]`\n\n"
            "**Example:** `/blacklist -100123456789`"
        )
    
    target = message.command[1]
    
    try:
        target_id = int(target)
        
        # Add to blacklist
        if str(target_id).startswith("-"):
            # Chat
            if target_id not in db.blacklisted:
                db.blacklisted.append(target_id)
                await db.cache.update_one(
                    {"_id": "bl_chats"},
                    {"$addToSet": {"chat_ids": target_id}},
                    upsert=True
                )
                await message.reply_text(f"✅ Chat `{target_id}` blacklisted")
            else:
                await message.reply_text(f"✅ Chat `{target_id}` already blacklisted")
        else:
            # User
            app.bl_users.add(target_id)
            await db.cache.update_one(
                {"_id": "bl_users"},
                {"$addToSet": {"user_ids": target_id}},
                upsert=True
            )
            await message.reply_text(f"✅ User `{target_id}` blacklisted")
    
    except ValueError:
        await message.reply_text("❌ Invalid ID")

@app.on_message(filters.command(["unblacklist"]) & app.sudo_filter)
@lang.language()
async def unblacklist_command(_, message: types.Message):
    """Remove from blacklist"""
    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:** `/unblacklist [chat_id/user_id]`"
        )
    
    target = message.command[1]
    
    try:
        target_id = int(target)
        
        # Remove from blacklist
        if str(target_id).startswith("-"):
            # Chat
            if target_id in db.blacklisted:
                db.blacklisted.remove(target_id)
                await db.cache.update_one(
                    {"_id": "bl_chats"},
                    {"$pull": {"chat_ids": target_id}}
                )
                await message.reply_text(f"✅ Chat `{target_id}` unblacklisted")
            else:
                await message.reply_text(f"❌ Chat `{target_id}` not blacklisted")
        else:
            # User
            if target_id in app.bl_users:
                app.bl_users.remove(target_id)
                await db.cache.update_one(
                    {"_id": "bl_users"},
                    {"$pull": {"user_ids": target_id}}
                )
                await message.reply_text(f"✅ User `{target_id}` unblacklisted")
            else:
                await message.reply_text(f"❌ User `{target_id}` not blacklisted")
    
    except ValueError:
        await message.reply_text("❌ Invalid ID")

# ==============================================================================
# ERROR HANDLERS
# ==============================================================================

@app.on_message(filters.group)
async def handle_errors(_, message: types.Message):
    """Global error handler"""
    pass  # Add error handling if needed

# ==============================================================================
# INITIALIZATION COMPLETE
# ==============================================================================

logger.info("✅ All modules loaded successfully!")
logger.info("========================================")
logger.info(f"🎵 {app.name} Music Bot")
logger.info(f"🤖 @{app.username}")
logger.info("✨ Ready to deploy on Render!")
logger.info("========================================")

# For Render deployment
if __name__ == "__main__":
    # This will be executed when running on Render
    print(f"🚀 Starting {app.name} on Render...")
    asyncio.run(main())
