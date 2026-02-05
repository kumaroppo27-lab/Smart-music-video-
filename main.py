"""
HasiiMusicBot - Advanced Telegram Music Bot

This is the main initialization module that sets up logging, configuration,
and all core components required for the bot to function.
"""

import asyncio
import time
import logging
from logging.handlers import RotatingFileHandler
from typing import List

# Configure logging
logging.basicConfig(
    format="[%(asctime)s - %(levelname)s] - %(name)s: %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("log.txt", maxBytes=10485760, backupCount=5),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
)

# Reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("ntgcalls").setLevel(logging.CRITICAL)
logging.getLogger("pymongo").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("pytgcalls").setLevel(logging.ERROR)

logger = logging.getLogger("HasiiMusic")

# Version
__version__ = "3.0.1"

# Load configuration
from config import Config

config = Config()
config.check()

# Global task list for background tasks
tasks: List = []
boot: float = time.time()

# Initialize bot client
from HasiiMusic.core.bot import Bot
app = Bot()

# Ensure required directories exist
from HasiiMusic.core.dir import ensure_dirs
ensure_dirs()

# Initialize userbot/assistant clients
from HasiiMusic.core.userbot import Userbot
userbot = Userbot()

# Initialize database connection
from HasiiMusic.core.mongo import MongoDB
db = MongoDB()

# Initialize language system
from HasiiMusic.core.lang import Language
lang = Language()

# Initialize Telegram and YouTube utilities
from HasiiMusic.core.telegram import Telegram
from HasiiMusic.core.youtube import YouTube
tg = Telegram()
yt = YouTube()

# Initialize preload manager for background track downloading
from HasiiMusic.core.preload import PreloadManager
preload = PreloadManager()

# Initialize queue manager
from HasiiMusic.helpers import Queue
queue = Queue()

# Initialize preload manager for next-track downloading
from HasiiMusic.helpers._preload import PreloadManager
preload = PreloadManager()

# Initialize call handler
from HasiiMusic.core.calls import TgCall
tune = TgCall()


async def stop() -> None:
    """
    Gracefully shutdown the bot and all its components.
    
    This function:
    - Cancels all running background tasks
    - Closes bot and userbot connections
    - Closes database connection
    - Logs shutdown completion
    """
    logger.info("🛑 Stopping bot...")
    
    # Stop tournament timer monitor
    try:
        from HasiiMusic.helpers._tournament import stop_timer_monitor
        stop_timer_monitor()
    except Exception as e:
        logger.error(f"Error stopping tournament timer: {e}")
    
    # Cancel all background tasks
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected when cancelling tasks - suppress the error
            pass
        except Exception:
            pass
    
    # Close all connections
    await app.exit()
    await userbot.exit()
    await db.close()
    
    logger.info("✅ Bot stopped successfully.\n")
# ==============================================================================
# __main__.py - Main Entry Point for HasiiMusicBot
# ==============================================================================
# This is the main file that starts the bot. It performs the following:
# 1. Connects to the database
# 2. Starts the bot client
# 3. Starts assistant (userbot) clients
# 4. Loads all plugin modules
# 5. Initializes YouTube cookies if configured
# 6. Keeps the bot running until manually stopped
# ==============================================================================

import asyncio
import importlib

from pyrogram import idle

from HasiiMusic import (tune, app, config, db,
                   logger, stop, userbot, yt)
from HasiiMusic.plugins import all_modules


async def main():
    try:
        # Step 1: Connect to MongoDB database
        await db.connect()
        
        # Step 2: Start the main bot client
        await app.boot()
        
        # Step 3: Start assistant/userbot clients (for joining voice chats)
        await userbot.boot()
        
        # Step 4: Initialize voice call handler
        await tune.boot()

        # Step 5: Load all plugin modules (commands like /play, /pause, etc.)
        for module in all_modules:
            try:
                importlib.import_module(f"HasiiMusic.plugins.{module}")
            except Exception as e:
                logger.error(f"Failed to load plugin {module}: {e}", exc_info=True)
        logger.info(f"🔌 Loaded {len(all_modules)} plugin modules.")
        
        # Step 5.5: Start tournament timer monitor
        try:
            from HasiiMusic.helpers._tournament import start_timer_monitor
            await start_timer_monitor()
        except Exception as e:
            logger.error(f"Failed to start tournament timer: {e}")

        # Step 6: Download YouTube cookies if URLs are provided (for age-restricted videos)
        if config.COOKIES_URL:
            try:
                await yt.save_cookies(config.COOKIES_URL)
            except Exception as e:
                logger.error(f"Failed to download cookies: {e}")

        # Step 7: Load sudo users and blacklisted users from database
        sudoers = await db.get_sudoers()
        app.sudoers.update(sudoers)  # Add sudo users to set
        app.sudo_filter.update(sudoers)  # Add sudo users to filter
        app.bl_users.update(await db.get_blacklisted())  # Add blacklisted users to filter
        logger.info(f"👑 Loaded {len(app.sudoers)} sudo users.")
        logger.info("\n🎉 Bot started successfully! Ready to play music! 🎵\n")

        # Step 8: Keep the bot running (press Ctrl+C to stop)
        try:
            await idle()
        except KeyboardInterrupt:
            logger.info("Received stop signal...")
        except Exception as e:
            logger.error(f"Error during idle: {e}", exc_info=True)
        
        # Step 9: Cleanup and shutdown when bot is stopped
        await stop()
    except Exception as e:
        logger.error(f"Critical error in main: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except SystemExit as e:
        logger.error(f"Bot exited with system error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error caused bot to stop: {e}", exc_info=True)
        # Don't raise - allow clean shutdown
    finally:
        # Ensure cleanup happens
        try:
            if loop.is_running():
                loop.stop()
        except:
            pass
"""
# ==============================================================================
# bot.py - Main Bot Client Manager
# ==============================================================================
# This file defines the main Bot class that handles the Telegram bot client.
# Features:
# - Extends Pyrogram Client with custom bot functionality
# - Manages bot authentication and connection
# - Handles bot startup and shutdown procedures
# - Provides owner, logger, and sudo user filters
# - Stores bot information (ID, name, username, mention)
# ==============================================================================
"""

import pyrogram
from typing import Optional

from HasiiMusic import config, logger


class Bot(pyrogram.Client):
    """
    Main bot client class extending Pyrogram's Client.

    This class initializes the Telegram bot with proper configuration
    and provides methods for starting and stopping the bot.

    Attributes:
        owner (int): Owner's user ID
        logger (int): Logger group/channel ID
        bl_users (Filter): Filter for blacklisted users
        sudoers (set): Set of sudo user IDs
        sudo_filter (Filter): Filter for sudo users
        id (int): Bot's user ID (set after boot)
        name (str): Bot's first name (set after boot)
        username (str): Bot's username (set after boot)
        mention (str): Bot's mention tag (set after boot)
    """

    def __init__(self):
        """Initialize the bot client with configuration settings."""
        super().__init__(
            name="HasiiMusic",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            parse_mode=pyrogram.enums.ParseMode.HTML,
            max_concurrent_transmissions=7,
            link_preview_options=pyrogram.types.LinkPreviewOptions(
                is_disabled=True),
        )

        self.owner: int = config.OWNER_ID
        self.logger: int = config.LOGGER_ID
        self.bl_users: pyrogram.filters.Filter = pyrogram.filters.user()
        self.sudoers: set = {self.owner}  # Set of sudo user IDs
        self.sudo_filter: pyrogram.filters.Filter = pyrogram.filters.user(
            self.owner)

        # These will be set after boot()
        self.id: Optional[int] = None
        self.name: Optional[str] = None
        self.username: Optional[str] = None
        self.mention: Optional[str] = None

    async def boot(self) -> None:
        """
        Start the bot and perform initial setup.

        This method:
        - Starts the Pyrogram client
        - Retrieves bot information
        - Verifies access to logger group
        - Checks bot admin status in logger group

        Raises:
            SystemExit: If bot cannot access logger group or is not an admin.
        """
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
        except Exception as ex:
            raise SystemExit(
                f"❌ ʙᴏᴛ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴀᴄᴄᴇꜱꜱ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ: {self.logger}\n"
                f"ʀᴇᴀꜱᴏɴ: {ex}\n"
                f"ᴘʟᴇᴀꜱᴇ ᴇɴꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴅᴇᴅ ᴛᴏ ᴛʜᴇ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ."
            )

        # Verify admin status
        if member.status != pyrogram.enums.ChatMemberStatus.ADMINISTRATOR:
            raise SystemExit(
                f"❌ ʙᴏᴛ ɪꜱ ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ ɪɴ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ: {self.logger}\n"
                f"ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴍᴏᴛᴇ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ ᴡɪᴛʜ ɴᴇᴄᴇꜱꜱᴀʀʏ ᴘᴇʀᴍɪꜱꜱɪᴏɴꜱ."
            )

        logger.info(f"🤖 Bot started successfully as @{self.username}")

    async def exit(self) -> None:
        """
        Gracefully stop the bot client.

        This method stops the Pyrogram client and logs the shutdown.
        """
        await super().stop()
        logger.info("🤖 Bot client stopped.")
# ==============================================================================
# calls.py - Voice Call Handler (PyTgCalls Integration)
# ==============================================================================
# This file manages voice/video chat functionality using PyTgCalls.
# Features:
# - Stream audio/video to Telegram voice chats
# - Playback controls (play, pause, resume, stop, seek)
# - Queue management (play next track automatically)
# - Multi-assistant support (load balancing)
# - Live stream support
# - Thumbnail updates during playback
# ==============================================================================

import asyncio
import logging
from ntgcalls import ConnectionNotFound, TelegramServerError
from pyrogram import enums, errors
from pyrogram.errors import MessageIdInvalid
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

from HasiiMusic import app, config, db, lang, logger, preload, queue, userbot, yt
from HasiiMusic.helpers import Media, Track, buttons, thumb

# Suppress pytgcalls UpdateGroupCall errors (library bug - harmless)
class UpdateGroupCallFilter(logging.Filter):
    def filter(self, record):
        return 'UpdateGroupCall' not in record.getMessage()

logging.getLogger('pyrogram.dispatcher').addFilter(UpdateGroupCallFilter())


class TgCall(PyTgCalls):
    def __init__(self):
        self.clients = []
        self._play_next_locks = {}  # Lock to prevent concurrent play_next calls per chat
        self._stream_end_cache = {}  # Cache to prevent duplicate stream end processing

    async def _edit_media_with_retry(self, message: Message, media_obj: InputMediaPhoto, reply_markup):
        """Edit media with basic FloodWait handling."""
        try:
            return await message.edit_media(media=media_obj, reply_markup=reply_markup)
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try:
                return await message.edit_media(media=media_obj, reply_markup=reply_markup)
            except Exception:
                return None
        except errors.MessageNotModified:
            return None
        except Exception:
            return None

    async def _send_photo_with_retry(self, chat_id: int, photo, caption: str, reply_markup):
        """Send photo with FloodWait handling."""
        try:
            return await app.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
            )
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try:
                return await app.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            except Exception:
                return None
        except Exception:
            return None

    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=True)
        return await client.pause(chat_id)

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=False)
        return await client.resume(chat_id)

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)
        
        # Cancel any active preload tasks when stopping
        try:
            await preload.cancel_preload(chat_id)
        except Exception as e:
            logger.debug(f"Error cancelling preload for {chat_id}: {e}")
        
        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
        except Exception as e:
            logger.warning(f"Error clearing queue/call for {chat_id}: {e}")

        try:
            await client.leave_call(chat_id, close=False)
            # Small delay to let group call state stabilize after leaving
            await asyncio.sleep(0.5)
        except (ConnectionNotFound, exceptions.NotInCallError):
            # Expected: userbot is not in a call
            pass
        except Exception as e:
            # Only log unexpected errors
            error_msg = str(e).lower()
            if not any(ignore in error_msg for ignore in [
                "not in a call",
                "not in the group call",
                "groupcall_forbidden",
                "no active group call",
                "call was already stopped",
                "call already disconnected"
            ]):
                logger.warning(f"Error leaving call for {chat_id}: {e}")

    async def play_media(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)
        # Generate thumbnail only if THUMB_GEN is enabled, otherwise use default
        if config.THUMB_GEN and isinstance(media, Track):
            _thumb = await thumb.generate(media)
        else:
            _thumb = config.DEFAULT_THUMB

        if not media.file_path:
            if message:
                return await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            else:
                logger.error(f"No file path for media in {chat_id}")
                return
        
        # Validate chat_id - check if it's a valid channel/group
        try:
            chat = await app.get_chat(chat_id)
            if chat.type not in [enums.ChatType.SUPERGROUP, enums.ChatType.GROUP, enums.ChatType.CHANNEL]:
                logger.error(f"Invalid chat type for {chat_id}: {chat.type}")
                if message:
                    await message.edit_text("❌ ᴄᴀɴ ᴏɴʟʏ ᴘʟᴀʏ ɪɴ ɢʀᴏᴜᴘꜱ/ᴄʜᴀɴɴᴇʟꜱ.")
                return
            # For channels, verify assistant is member
            if chat.type == enums.ChatType.CHANNEL:
                # Get the userbot (Pyrogram client) to access .me attribute
                userbot_client = await db.get_client(chat_id)
                if not userbot_client:
                    logger.error(f"No userbot client available for {chat_id}")
                    if message:
                        await message.edit_text("❌ ɴᴏ ᴀꜱꜱɪꜱᴛᴀɴᴛ ᴀᴠᴀɪʟᴀʙʟᴇ.")
                    return
                
                try:
                    assistant_member = await app.get_chat_member(chat_id, userbot_client.me.id)
                    if assistant_member.status == enums.ChatMemberStatus.BANNED:
                        logger.error(f"Assistant banned in channel {chat_id}")
                        if message:
                            await message.edit_text("❌ ᴀꜱꜱɪꜱᴛᴀɴᴛ ɪꜱ ʙᴀɴɴᴇᴅ ɪɴ ᴛʜɪꜱ ᴄʜᴀɴɴᴇʟ.")
                        await db.set_cmode(chat_id, None)  # Disable channel play
                        return
                except errors.RPCError as e:
                    if "CHANNEL_INVALID" in str(e) or "USER_NOT_PARTICIPANT" in str(e):
                        logger.error(f"Assistant not in channel {chat_id}: {e}")
                        if message:
                            await message.edit_text(
                                "❌ <b>ᴀꜱꜱɪꜱᴛᴀɴᴛ ɴᴏᴛ ɪɴ ᴄʜᴀɴɴᴇʟ!</b>\n\n"
                                f"<blockquote>ᴘʟᴇᴀꜱᴇ ᴀᴅᴅ @{userbot_client.me.username} ᴛᴏ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴀꜱ ᴀᴅᴍɪɴ ᴡɪᴛʜ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴘᴇʀᴍɪꜱꜱɪᴏɴꜱ.</blockquote>"
                            )
                        await db.set_cmode(chat_id, None)  # Disable channel play
                        return
        except errors.RPCError as e:
            if "CHANNEL_INVALID" in str(e):
                logger.error(f"Invalid channel {chat_id}: {e}")
                if message:
                    await message.edit_text("❌ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ. ᴅɪꜱᴀʙʟɪɴɢ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ.")
                await db.set_cmode(chat_id, None)  # Disable channel play
                return
            raise

        # Configure audio stream with optimized buffering for lag-free playback
        # PERFORMANCE FIX: Increased buffers prevent stuttering/lagging during playback
        if seek_time > 1:
            # Seeking: Still need buffers but skip to position first
            ffmpeg_params = f"-ss {seek_time} -probesize 10M -analyzeduration 5M -rtbufsize 5M -fflags +genpts+igndts"
        else:
            # Normal playback with aggressive buffering:
            # - probesize 10M: Large input buffer (prevents underruns)
            # - analyzeduration 5M: Analyze more data (better format detection)
            # - rtbufsize 5M: Real-time buffer (crucial for network streams)
            # - fflags +genpts+igndts: Generate PTS, ignore DTS (smooth playback)
            # - sync ext: External sync (reduces A/V desync)
            ffmpeg_params = "-probesize 10M -analyzeduration 5M -rtbufsize 5M -fflags +genpts+igndts -sync ext"
        
        stream = types.MediaStream(
            media_path=media.file_path,
            # PERFORMANCE FIX: Reduced from STUDIO to HIGH quality
            # HIGH = 192kbps (vs STUDIO 320kbps) - better network stability, imperceptible quality difference
            audio_parameters=types.AudioQuality.HIGH,
            audio_flags=types.MediaStream.Flags.REQUIRED,
            video_flags=types.MediaStream.Flags.IGNORE,
            ffmpeg_parameters=ffmpeg_params,
        )
        
        # Check if already connected, if so leave first to avoid "Connection cannot be initialized more than once"
        try:
            # Check current call status
            call = await client.get_call(chat_id)
            if call:
                # Already connected, need to leave first
                logger.debug(f"Already connected to {chat_id}, leaving before reconnecting...")
                await client.leave_call(chat_id, close=False)
                await asyncio.sleep(0.5)  # Let connection fully close
        except (ConnectionNotFound, exceptions.NotInCallError):
            # Not connected, which is what we want
            pass
        except Exception as e:
            # Log but continue - might not be critical
            logger.debug(f"Error checking connection state for {chat_id}: {e}")
        
        # Retry logic for race conditions when stopping/starting quickly
        max_retries = 3
        retry_delay = 1  # seconds
        
        try:
            for attempt in range(max_retries):
                try:
                    await client.play(
                        chat_id=chat_id,
                        stream=stream,
                        config=types.GroupCallConfig(auto_start=True),
                    )
                    # Success - break retry loop
                    break
                except (exceptions.NoActiveGroupCall, errors.RPCError) as e:
                    error_msg = str(e)
                    # Check if it's a group call state error
                    if "GROUPCALL_INVALID" in error_msg or "GROUPCALL" in error_msg or isinstance(e, exceptions.NoActiveGroupCall):
                        if attempt < max_retries - 1:
                            # Wait for group call state to stabilize
                            logger.debug(f"Group call transitioning for {chat_id}, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            # Final attempt failed
                            raise
                    else:
                        # Different error, don't retry
                        raise
                except Exception as e:
                    error_msg = str(e).lower()
                    # Handle "Connection cannot be initialized more than once" error
                    if "cannot be initialized more than once" in error_msg or "connection" in error_msg:
                        if attempt < max_retries - 1:
                            logger.debug(f"Connection error for {chat_id}, leaving and retrying... (attempt {attempt + 1}/{max_retries})")
                            try:
                                await client.leave_call(chat_id, close=False)
                                await asyncio.sleep(retry_delay)
                            except Exception:
                                pass
                            continue
                        else:
                            raise
                    else:
                        # Different error, don't retry
                        raise
                
            # Initialize media.time based on seek position
            if seek_time:
                media.time = seek_time
            else:
                media.time = 1

            if not seek_time:
                await db.add_call(chat_id)
                text = _lang["play_media"].format(
                    media.url,
                    media.title,
                    media.duration,
                    media.user,
                )
                # Create initial timer display
                if not media.is_live and media.duration_sec:
                    import time as time_module
                    played = media.time  # Use actual media.time value
                    duration = media.duration_sec
                    # Build progress bar with original style
                    bar_length = 12
                    if duration == 0:
                        percentage = 0
                    else:
                        percentage = min((played / duration) * 100, 100)
                    filled = int(round(bar_length * percentage / 100))
                    timer_bar = "—" * filled + "●" + "—" * (bar_length - filled)
                    # Format time properly with hours support
                    if duration >= 3600:
                        played_time = time_module.strftime(
                            '%H:%M:%S', time_module.gmtime(played))
                        total_time = time_module.strftime(
                            '%H:%M:%S', time_module.gmtime(duration))
                    else:
                        played_time = time_module.strftime(
                            '%M:%S', time_module.gmtime(played))
                        total_time = time_module.strftime(
                            '%M:%S', time_module.gmtime(duration))
                    timer_text = f"{played_time} {timer_bar} {total_time}"
                    keyboard = buttons.controls(chat_id, timer=timer_text)
                else:
                    keyboard = buttons.controls(chat_id)
                
                if message:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                
                # Send new photo message
                sent_photo = await self._send_photo_with_retry(
                    chat_id=chat_id,
                    photo=_thumb,
                    caption=text,
                    reply_markup=keyboard,
                )
                if sent_photo:
                    media.message_id = sent_photo.id
                
                # ✨ NEW: Start preloading next tracks in background for seamless transitions
                try:
                    asyncio.create_task(preload.start_preload(chat_id, count=2))
                except Exception as e:
                    logger.debug(f"Error starting preload for {chat_id}: {e}")
        except FileNotFoundError:
            if message:
                try:
                    await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
                except Exception:
                    pass
            await self.play_next(chat_id)
        except exceptions.NoActiveGroupCall:
            # Voice chat is NOT active/enabled in the group
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_vc_disabled"])
                except Exception:
                    pass
        except errors.RPCError as e:
            # Handle Telegram API errors
            error_str = str(e)
            
            # When trying to play in a voice chat, CHAT_ADMIN_REQUIRED usually means VC is disabled
            # (not that permissions are missing). This is because:
            # - If VC is disabled, Telegram returns CHAT_ADMIN_REQUIRED when trying to start it
            # - If VC is enabled but assistant lacks permissions, error happens earlier (during join)
            if any(x in error_str for x in ["CHAT_ADMIN_REQUIRED", "phone.CreateGroupCall", "GROUPCALL_FORBIDDEN", "GROUPCALL_CREATE_FORBIDDEN", "VOICE_MESSAGES_FORBIDDEN"]):
                await self.stop(chat_id)
                if message:
                    try:
                        await message.edit_text(_lang["error_vc_disabled"])
                    except Exception:
                        pass
            elif "GROUPCALL_INVALID" in error_str or "GROUPCALL" in error_str:
                await self.stop(chat_id)
                if message:
                    try:
                        await message.edit_text(_lang["error_no_call"])
                    except Exception:
                        pass
            else:
                # Log but don't crash for other RPC errors
                logger.error(f"RPC error in play_media for {chat_id}: {e}")
                await self.stop(chat_id)
        except exceptions.NoAudioSourceFound:
            if message:
                try:
                    await message.edit_text(_lang["error_no_audio"])
                except Exception:
                    pass
            await self.play_next(chat_id)
        except (ConnectionNotFound, TelegramServerError):
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_tg_server"])
                except Exception:
                    pass
        except Exception as e:
            # Catch all other exceptions to prevent bot crash
            logger.error(f"Unexpected error in play_media for {chat_id}: {e}", exc_info=True)
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(f"❌ Playback error: {str(e)[:100]}")
                except Exception:
                    pass

    async def replay(self, chat_id: int) -> None:
        try:
            if not await db.get_call(chat_id):
                return

            media = queue.get_current(chat_id)
            _lang = await lang.get_lang(chat_id)
            msg = await app.send_message(chat_id=chat_id, text=_lang["play_again"])
            await self.play_media(chat_id, msg, media)
        except Exception as e:
            logger.error(f"Error in replay for {chat_id}: {e}", exc_info=True)

    async def seek_stream(self, chat_id: int, seconds: int) -> bool:
        """Seek to a specific position in the current stream."""
        try:
            if not await db.get_call(chat_id):
                return False

            media = queue.get_current(chat_id)
            if not media or media.is_live:
                return False

            client = await db.get_assistant(chat_id)
            _lang = await lang.get_lang(chat_id)
            
            # Update media time
            media.time = seconds
            
            # Get message to update
            try:
                msg = await app.get_messages(chat_id, media.message_id)
            except Exception:
                # Message deleted or doesn't exist, create new one
                msg = None
            
            if not msg:
                _lang = await lang.get_lang(chat_id)
                msg = await app.send_message(chat_id=chat_id, text=_lang["seeking"])
            
            # Replay from new position
            await self.play_media(chat_id, msg, media, seek_time=seconds)
            return True
        except Exception as e:
            logger.warning(f"Seek stream failed for {chat_id}: {e}")
            return False

    async def play_next(self, chat_id: int) -> None:
        # Acquire lock for this chat to prevent concurrent execution
        if chat_id not in self._play_next_locks:
            self._play_next_locks[chat_id] = asyncio.Lock()
        
        lock = self._play_next_locks[chat_id]
        
        # If already processing play_next for this chat, return immediately
        if lock.locked():
            logger.info(f"play_next already running for {chat_id}, skipping duplicate call")
            return
        
        async with lock:
            try:
                if not await db.get_call(chat_id):
                    return

                # Check loop mode
                loop_mode = await db.get_loop(chat_id)
                
                if loop_mode == 1:
                    # Single track loop - replay current track
                    media = queue.get_current(chat_id)
                    if media:
                        _lang = await lang.get_lang(chat_id)
                        try:
                            msg = await app.send_message(chat_id=chat_id, text=_lang["play_again"])
                            await self.play_media(chat_id, msg, media)
                        except errors.ChannelPrivate:
                            logger.warning(f"Bot removed from {chat_id}, cleaning up")
                            try:
                                await self.leave_call(chat_id)
                            except (AttributeError, Exception) as leave_ex:
                                logger.debug(f"Could not leave call for {chat_id}: {leave_ex}")
                            await db.rm_chat(chat_id)
                        return
                
                media = queue.get_next(chat_id)
                
                # If queue loop and no more tracks, start from beginning
                if not media and loop_mode == 10:
                    all_items = queue.get_all(chat_id)
                    if all_items:
                        # Reset queue to beginning
                        first_track = all_items[0]
                        _lang = await lang.get_lang(chat_id)
                        try:
                            msg = await app.send_message(chat_id=chat_id, text="🔁 Looping queue...")
                            if not first_track.file_path:
                                is_live = getattr(first_track, 'is_live', False)
                                first_track.file_path = await yt.download(first_track.id, is_live=is_live)
                            first_track.message_id = msg.id
                            await self.play_media(chat_id, msg, first_track)
                        except errors.ChannelPrivate:
                            logger.warning(f"Bot removed from {chat_id}, cleaning up")
                            await self.leave_call(chat_id)
                            await db.rm_chat(chat_id)
                        return
                
                try:
                    if media and media.message_id:
                        await app.delete_messages(
                            chat_id=chat_id,
                            message_ids=media.message_id,
                            revoke=True,
                        )
                        media.message_id = 0
                except Exception as e:
                    logger.debug(f"Could not delete previous message in {chat_id}: {e}")

                if not media:
                    # Check if AUTO_END is enabled
                    if config.AUTO_END:
                        _lang = await lang.get_lang(chat_id)
                        try:
                            # Send auto-end notification
                            await app.send_message(
                                chat_id=chat_id,
                                text=_lang.get("auto_end", "✅ Queue finished. Stream ended automatically.")
                            )
                        except Exception as e:
                            logger.debug(f"Could not send auto_end message in {chat_id}: {e}")
                    return await self.stop(chat_id)

                _lang = await lang.get_lang(chat_id)
                # Send message with FloodWait handling
                try:
                    msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
                except errors.FloodWait as fw:
                    logger.warning(f"FloodWait in play_next for {chat_id}: waiting {fw.value}s")
                    await asyncio.sleep(fw.value + 1)
                    try:
                        msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
                    except errors.ChannelPrivate:
                        logger.warning(f"Bot removed from {chat_id}, cleaning up")
                        await self.leave_call(chat_id)
                        await db.rm_chat(chat_id)
                        return
                    except Exception as e:
                        logger.error(f"Failed to send play_next message after FloodWait for {chat_id}: {e}")
                        # Continue without message - don't let this stop playback
                        msg = None
                except errors.ChannelPrivate:
                    logger.warning(f"Bot removed from {chat_id}, cleaning up")
                    await self.leave_call(chat_id)
                    await db.rm_chat(chat_id)
                    return
                except Exception as e:
                    logger.error(f"Failed to send play_next message for {chat_id}: {e}")
                    msg = None
                
                if not media.file_path:
                    is_live = getattr(media, 'is_live', False)
                    media.file_path = await yt.download(media.id, is_live=is_live)
                    if not media.file_path:
                        await self.stop(chat_id)
                        if msg:
                            try:
                                await msg.edit_text(
                                    _lang["error_no_file"].format(config.SUPPORT_CHAT)
                                )
                            except Exception:
                                pass
                        return

                media.message_id = msg.id if msg else 0
                if msg:
                    await self.play_media(chat_id, msg, media)
                else:
                    # No message object due to errors, but continue playback
                    # Create a temporary message or handle without UI update
                    logger.info(f"Playing next track for {chat_id} without message update")
                    await self.play_media(chat_id, None, media)
                
                # ✨ NEW: After playing next track, start preloading upcoming tracks
                try:
                    asyncio.create_task(preload.start_preload(chat_id, count=2))
                except Exception as e:
                    logger.debug(f"Error starting preload after play_next for {chat_id}: {e}")
            except Exception as e:
                logger.error(f"Error in play_next for {chat_id}: {e}", exc_info=True)
                # Try to stop the call gracefully
                try:
                    await self.stop(chat_id)
                except Exception:
                    pass

    async def ping(self) -> float:
        pings = [client.ping for client in self.clients]
        return round(sum(pings) / len(pings), 2)

    async def decorators(self, client: PyTgCalls) -> None:
        for client in self.clients:
            @client.on_update()
            async def update_handler(_, update: types.Update) -> None:
                if isinstance(update, types.StreamEnded):
                    if update.stream_type == types.StreamEnded.Type.AUDIO:
                        # Deduplicate stream end events from multiple assistants
                        chat_id = update.chat_id
                        current_time = asyncio.get_event_loop().time()
                        
                        # Check if we recently processed a stream end for this chat (within 2 seconds)
                        if chat_id in self._stream_end_cache:
                            if current_time - self._stream_end_cache[chat_id] < 2.0:
                                # Duplicate event from another assistant, skip it
                                return
                        
                        # Mark this stream end as processed
                        self._stream_end_cache[chat_id] = current_time
                        
                        # Clean up old cache entries (older than 5 seconds)
                        self._stream_end_cache = {
                            cid: t for cid, t in self._stream_end_cache.items()
                            if current_time - t < 5.0
                        }
                        
                        await self.play_next(chat_id)
                elif isinstance(update, types.ChatUpdate):
                    if update.status in [
                        types.ChatUpdate.Status.KICKED,
                        types.ChatUpdate.Status.LEFT_GROUP,
                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                    ]:
                        await self.stop(update.chat_id)

    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True
        for ub in userbot.clients:
            # Enhanced cache_duration=100 reduces API calls and improves performance
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self.decorators(client)
        logger.info("📞 PyTgCalls client(s) started.")
# ==============================================================================
# dir.py - Directory Management
# ==============================================================================
# This file ensures that required directories exist for the bot to store:
# - cache: Temporary cache files
# - downloads: Downloaded audio/video files from Telegram or YouTube
# These directories are created automatically on startup if they don't exist.
# ==============================================================================

from pathlib import Path

from HasiiMusic import logger


def ensure_dirs():
    """
    Create necessary directories if they don't exist.

    Creates:
    - cache/: For temporary cache files
    - downloads/: For downloaded media files
    """
    # List of required directories
    for dir in ["cache", "downloads"]:
        # Create directory (and parents if needed)
        Path(dir).mkdir(parents=True, exist_ok=True)
    logger.info("📁 Cache directories updated.")
# ==============================================================================
# lang.py - Multi-Language Support System
# ==============================================================================
# This file manages translations for the bot in multiple languages.
# - Translation files are stored in HasiiMusic/locales/ as JSON files (en.json, si.json)
# - Each chat can have its own language preference stored in the database
# - The @language() decorator automatically injects translations into message handlers
# ==============================================================================

import json
from functools import wraps
from pathlib import Path

from HasiiMusic import db, logger

# Supported language codes and their display names
lang_codes = {
    "en": "English",  # English language
}


class Language:
    """
    Language class for managing multilingual support using JSON language files.
    """

    def __init__(self):
        """Initialize the language system and load all translation files."""
        self.lang_codes = lang_codes
        # Directory containing translation files
        self.lang_dir = Path("HasiiMusic/locales")
        self.languages = self.load_files()  # Load all language files into memory

    def load_files(self):
        """Load all language JSON files from the locales directory."""
        languages = {}
        for lang_code in self.lang_codes.keys():
            lang_file = self.lang_dir / \
                f"{lang_code}.json"  # Path to language file
            if lang_file.exists():
                with open(lang_file, "r", encoding="utf-8") as file:
                    languages[lang_code] = json.load(
                        file)  # Load translations into dict
        logger.info(f"🌐 Loaded languages: {', '.join(languages.keys())}")
        return languages

    async def get_lang(self, chat_id: int) -> dict:
        """Get the translation dictionary for a specific chat."""
        return self.languages["en"]  # Return the translation dictionary

    def language(self):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                fallen = next(
                    (
                        arg
                        for arg in args
                        if hasattr(arg, "chat") or hasattr(arg, "message")
                    ),
                    None,
                )

                if hasattr(fallen, "chat"):
                    chat = fallen.chat
                elif hasattr(fallen, "message"):
                    chat = fallen.message.chat

                if chat.id in db.blacklisted:
                    return await chat.leave()

                lang_code = "en"
                lang_dict = self.languages[lang_code]

                setattr(fallen, "lang", lang_dict)
                return await func(*args, **kwargs)

            return wrapper

        return decorator
# ==============================================================================
# mongo.py - MongoDB Database Manager
# ==============================================================================
# This file handles all database operations using MongoDB.
# Collections:
# - users: User data (sudo users)
# - chats: Group/chat data (language, channel play mode, authorized users)
# - blacklist: Blacklisted users/chats
# - calls: Active voice call sessions
# - cache: Admin list cache
#
# Features:
# - Async MongoDB operations for better performance
# - Connection pooling for efficiency
# - Admin list caching to reduce database queries
# - Random assistant selection for load balancing
# ==============================================================================

from random import randint
from time import time
import asyncio
import logging

from pymongo import AsyncMongoClient

from HasiiMusic import config, logger, userbot


# Suppress non-critical MongoDB background task errors
class MongoBackgroundFilter(logging.Filter):
    def filter(self, record):
        # Suppress AutoReconnect and _OperationCancelled background errors (these are handled internally)
        msg = record.getMessage()
        return not (
            'MongoClient background task encountered an error' in msg or
            ('AutoReconnect' in msg and 'background task' in msg) or
            ('_OperationCancelled' in msg and 'background task' in msg)
        )

logging.getLogger('pymongo.client').addFilter(MongoBackgroundFilter())


class MongoDB:
    def __init__(self):
        """
        Initialize the MongoDB connection.
        """
        self.mongo = AsyncMongoClient(
            config.MONGO_URL,
            serverSelectionTimeoutMS=12500,
            connectTimeoutMS=20000,
            socketTimeoutMS=20000,
            maxPoolSize=50,
            minPoolSize=10,
            maxIdleTimeMS=45000,
            waitQueueTimeoutMS=10000,
            retryWrites=True,
            retryReads=True
        )
        self.db = self.mongo.HasiiTune

        self.admin_list = {}  # Cache admin lists
        self.admin_cache_time = {}  # Track cache freshness
        self.active_calls = {}
        self.blacklisted = []
        self.notified = []
        self.cache = self.db.cache
        self.logger = False

        self.assistant = {}
        self.assistantdb = self.db.assistant

        self.auth = {}
        self.authdb = self.db.auth

        self.chats = []
        self.chatsdb = self.db.chats

        self.lang = {}
        self.langdb = self.db.lang

        self.play_mode = []
        self.playmodedb = self.db.play

        self.users = []
        self.usersdb = self.db.users

    async def connect(self) -> None:
        """Check if we can connect to the database with exponential backoff retry logic.

        Raises:
            SystemExit: If the connection to the database fails after retries.
        """
        max_retries = 3
        retry_delay = 5  # Initial delay in seconds
        
        for attempt in range(1, max_retries + 1):
            try:
                start = time()
                await self.mongo.admin.command("ping")
                logger.info(
                    f"✅ Database connection successful. ({time() - start:.2f}s)")

                # Create indexes for faster queries
                await self.authdb.create_index("_id")
                await self.langdb.create_index("_id")
                await self.cache.create_index("_id")

                await self.load_cache()
                return  # Success, exit the function
            except Exception as e:
                if attempt < max_retries:
                    # Exponential backoff: 5s, 10s, 20s
                    wait_time = retry_delay * (2 ** (attempt - 1))
                    logger.warning(f"Database connection attempt {attempt}/{max_retries} failed: {type(e).__name__}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise SystemExit(
                        f"Database connection failed after {max_retries} attempts: {type(e).__name__}") from e

    async def close(self) -> None:
        """Close the connection to the database."""
        await self.mongo.close()
        logger.info("Database connection closed.")

    # CACHE
    async def get_call(self, chat_id: int) -> bool:
        return chat_id in self.active_calls

    async def add_call(self, chat_id: int) -> None:
        self.active_calls[chat_id] = 1

    async def remove_call(self, chat_id: int) -> None:
        self.active_calls.pop(chat_id, None)

    async def playing(self, chat_id: int, paused: bool = None) -> bool | None:
        if paused is not None:
            self.active_calls[chat_id] = int(not paused)
        return bool(self.active_calls[chat_id])

    async def get_admins(self, chat_id: int, reload: bool = False) -> list[int]:
        from HasiiMusic.helpers._admins import reload_admins

        # **PERFORMANCE FIX**: Increased cache from 5 to 15 minutes
        # Reduces MongoDB queries during peak load (15-20 concurrent streams)
        current_time = time()
        cache_age = current_time - self.admin_cache_time.get(chat_id, 0)

        if chat_id not in self.admin_list or reload or cache_age > 900:  # 15 minutes
            self.admin_list[chat_id] = await reload_admins(chat_id)
            self.admin_cache_time[chat_id] = current_time
        return self.admin_list[chat_id]

    # AUTH METHODS
    async def _get_auth(self, chat_id: int) -> set[int]:
        if chat_id not in self.auth:
            doc = await self.authdb.find_one({"_id": chat_id}) or {}
            self.auth[chat_id] = set(doc.get("user_ids", []))
        return self.auth[chat_id]

    async def is_auth(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self._get_auth(chat_id)

    async def add_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id not in users:
            users.add(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$addToSet": {"user_ids": user_id}}, upsert=True
            )

    async def rm_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id in users:
            users.discard(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$pull": {"user_ids": user_id}}
            )

    # ASSISTANT METHODS
    async def set_assistant(self, chat_id: int) -> int:
        num = randint(1, len(userbot.clients))
        await self.assistantdb.update_one(
            {"_id": chat_id},
            {"$set": {"num": num}},
            upsert=True,
        )
        self.assistant[chat_id] = num
        return num

    async def get_assistant(self, chat_id: int):
        from HasiiMusic import tune

        if chat_id not in self.assistant:
            doc = await self.assistantdb.find_one({"_id": chat_id})
            num = doc["num"] if doc else await self.set_assistant(chat_id)
            self.assistant[chat_id] = num

        # Check if assigned assistant is out of range (e.g., assistant was removed)
        if self.assistant[chat_id] > len(userbot.clients):
            # Reassign to a valid assistant
            num = await self.set_assistant(chat_id)
            self.assistant[chat_id] = num

        return tune.clients[self.assistant[chat_id] - 1]

    async def get_client(self, chat_id: int):
        if chat_id not in self.assistant:
            await self.get_assistant(chat_id)
        
        # Check if assigned assistant is out of range
        if self.assistant[chat_id] > len(userbot.clients):
            # Reassign to a valid assistant
            await self.set_assistant(chat_id)
        
        # Get available clients dynamically based on what's actually running
        available_clients = {}
        if hasattr(userbot, 'one') and userbot.one in userbot.clients:
            available_clients[1] = userbot.one
        if hasattr(userbot, 'two') and userbot.two in userbot.clients:
            available_clients[2] = userbot.two
        if hasattr(userbot, 'three') and userbot.three in userbot.clients:
            available_clients[3] = userbot.three
        
        return available_clients.get(self.assistant[chat_id])

    # BLACKLIST METHODS
    async def add_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.append(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"}, {"$addToSet": {"chat_ids": chat_id}}, upsert=True
            )
        await self.cache.update_one(
            {"_id": "bl_users"}, {"$addToSet": {"user_ids": chat_id}}, upsert=True
        )

    async def del_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.remove(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"},
                {"$pull": {"chat_ids": chat_id}},
            )
        await self.cache.update_one(
            {"_id": "bl_users"},
            {"$pull": {"user_ids": chat_id}},
        )

    async def get_blacklisted(self, chat: bool = False) -> list[int]:
        if chat:
            if not self.blacklisted:
                doc = await self.cache.find_one({"_id": "bl_chats"})
                self.blacklisted.extend(doc.get("chat_ids", []) if doc else [])
            return self.blacklisted
        doc = await self.cache.find_one({"_id": "bl_users"})
        return doc.get("user_ids", []) if doc else []

    # CHAT METHODS
    async def is_chat(self, chat_id: int) -> bool:
        return chat_id in self.chats

    async def add_chat(self, chat_id: int) -> None:
        if not await self.is_chat(chat_id):
            self.chats.append(chat_id)
            await self.chatsdb.insert_one({"_id": chat_id})

    async def rm_chat(self, chat_id: int) -> None:
        if await self.is_chat(chat_id):
            self.chats.remove(chat_id)
            await self.chatsdb.delete_one({"_id": chat_id})

    async def get_chats(self) -> list:
        if not self.chats:
            self.chats.extend([chat["_id"] async for chat in self.chatsdb.find()])
        return self.chats

    # LANGUAGE METHODS
    async def set_lang(self, chat_id: int, lang_code: str):
        await self.langdb.update_one(
            {"_id": chat_id},
            {"$set": {"lang": lang_code}},
            upsert=True,
        )
        self.lang[chat_id] = lang_code

    async def get_lang(self, chat_id: int) -> str:
        if chat_id not in self.lang:
            doc = await self.langdb.find_one({"_id": chat_id})
            self.lang[chat_id] = doc["lang"] if doc else "en"
        return self.lang[chat_id]

    # LOGGER METHODS
    async def is_logger(self) -> bool:
        return self.logger

    async def get_logger(self) -> bool:
        doc = await self.cache.find_one({"_id": "logger"})
        if doc:
            self.logger = doc["status"]
        return self.logger

    async def set_logger(self, status: bool) -> None:
        self.logger = status
        await self.cache.update_one(
            {"_id": "logger"},
            {"$set": {"status": status}},
            upsert=True,
        )

    # CHANNEL PLAY METHODS
    async def get_cmode(self, chat_id: int) -> int | None:
        """Get channel play mode for a chat."""
        doc = await self.cache.find_one({"_id": f"cplay_{chat_id}"})
        return doc.get("channel_id") if doc else None

    async def set_cmode(self, chat_id: int, channel_id: int | None) -> None:
        """Set or remove channel play mode for a chat."""
        if channel_id is None:
            await self.cache.delete_one({"_id": f"cplay_{chat_id}"})
        else:
            await self.cache.update_one(
                {"_id": f"cplay_{chat_id}"},
                {"$set": {"channel_id": channel_id}},
                upsert=True,
            )

    # AUTO LEAVE METHODS
    async def get_autoleave(self, chat_id: int) -> bool:
        """Get auto-leave status for a chat. Default is False."""
        doc = await self.cache.find_one({"_id": f"autoleave_{chat_id}"})
        return doc.get("enabled", False) if doc else False

    async def set_autoleave(self, chat_id: int, enabled: bool) -> None:
        """Enable or disable auto-leave for a chat."""
        await self.cache.update_one(
            {"_id": f"autoleave_{chat_id}"},
            {"$set": {"enabled": enabled}},
            upsert=True,
        )

    # LOOP MODE METHODS
    async def get_loop(self, chat_id: int) -> int:
        """Get loop mode for a chat. 0=off, 1=single, 10=queue"""
        doc = await self.cache.find_one({"_id": f"loop_{chat_id}"})
        return doc.get("mode", 0) if doc else 0

    async def set_loop(self, chat_id: int, mode: int) -> None:
        """Set loop mode for a chat."""
        if mode == 0:
            await self.cache.delete_one({"_id": f"loop_{chat_id}"})
        else:
            await self.cache.update_one(
                {"_id": f"loop_{chat_id}"},
                {"$set": {"mode": mode}},
                upsert=True,
            )

    # PLAY MODE METHODS
    async def get_play_mode(self, chat_id: int) -> bool:
        if chat_id not in self.play_mode:
            doc = await self.playmodedb.find_one({"_id": chat_id})
            if doc:
                self.play_mode.append(chat_id)
        return chat_id in self.play_mode

    async def set_play_mode(self, chat_id: int, remove: bool = False) -> None:
        if remove:
            self.play_mode.remove(chat_id)
            await self.playmodedb.delete_one({"_id": chat_id})
        else:
            self.play_mode.append(chat_id)
            await self.playmodedb.insert_one({"_id": chat_id})

    # SUDO METHODS
    async def add_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$addToSet": {"user_ids": user_id}}, upsert=True
        )

    async def del_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$pull": {"user_ids": user_id}}
        )

    async def get_sudoers(self) -> list[int]:
        doc = await self.cache.find_one({"_id": "sudoers"})
        return doc.get("user_ids", []) if doc else []

    # USER METHODS
    async def is_user(self, user_id: int) -> bool:
        return user_id in self.users

    async def add_user(self, user_id: int) -> None:
        if not await self.is_user(user_id):
            self.users.append(user_id)
            await self.usersdb.insert_one({"_id": user_id})

    async def rm_user(self, user_id: int) -> None:
        if await self.is_user(user_id):
            self.users.remove(user_id)
            await self.usersdb.delete_one({"_id": user_id})

    async def get_users(self) -> list:
        if not self.users:
            self.users.extend([user["_id"] async for user in self.usersdb.find()])
        return self.users

    async def migrate_coll(self) -> None:
        """Migrate old collection structure (ObjectId) to new structure (int)."""
        from bson import ObjectId
        logger.info("🔄 Migrating users and chats from old collections...")

        musers, mchats, done = [], [], []
        
        # Collect all users from both old and new collections
        try:
            ulist = [user async for user in self.db.tgusersdb.find()]
        except Exception:
            ulist = []
        
        try:
            ulist.extend([user async for user in self.usersdb.find()])
        except Exception:
            pass

        # Process users
        for user in ulist:
            try:
                if isinstance(user.get("_id"), ObjectId):
                    user_id = int(user.get("user_id", 0))
                    if user_id and user_id not in done:
                        done.append(user_id)
                        musers.append({"_id": user_id})
                else:
                    user_id = int(user["_id"])
                    if user_id not in done:
                        done.append(user_id)
                        musers.append({"_id": user_id})
            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping invalid user entry: {e}")
                continue
        
        # Drop old collections and insert migrated users
        try:
            await self.usersdb.drop()
        except Exception:
            pass
        try:
            await self.db.tgusersdb.drop()
        except Exception:
            pass
        if musers:
            try:
                await self.usersdb.insert_many(musers, ordered=False)
            except Exception as e:
                logger.debug(f"User migration bulk insert error (may be duplicate keys): {e}")

        # Process chats
        done.clear()
        try:
            async for chat in self.chatsdb.find():
                try:
                    if isinstance(chat.get("_id"), ObjectId):
                        chat_id = int(chat.get("chat_id", 0))
                        if chat_id and chat_id not in done:
                            done.append(chat_id)
                            mchats.append({"_id": chat_id})
                    else:
                        chat_id = int(chat["_id"])
                        if chat_id not in done:
                            done.append(chat_id)
                            mchats.append({"_id": chat_id})
                except (ValueError, KeyError) as e:
                    logger.debug(f"Skipping invalid chat entry: {e}")
                    continue
        except Exception as e:
            logger.debug(f"Error reading chats collection: {e}")
        
        # Drop old collection and insert migrated chats
        try:
            await self.chatsdb.drop()
        except Exception:
            pass
        if mchats:
            try:
                await self.chatsdb.insert_many(mchats, ordered=False)
            except Exception as e:
                logger.debug(f"Chat migration bulk insert error (may be duplicate keys): {e}")

        # Mark migration as complete
        await self.cache.update_one(
            {"_id": "migrated"},
            {"$set": {"status": True, "timestamp": time()}},
            upsert=True
        )
        logger.info("✅ Migration completed successfully.")

    async def load_cache(self) -> None:
        """Preload cache data from database for faster access."""
        # Check if migration needed
        doc = await self.cache.find_one({"_id": "migrated"})
        if not doc:
            await self.migrate_coll()

        # Preload all cache data
        logger.info("📦 Loading database cache...")
        
        # Load chats, users, blacklists, and logger status
        await self.get_chats()
        await self.get_users()
        await self.get_blacklisted(chat=True)  # Load blacklisted chats
        await self.get_logger()
        
        # Preload sudoers list
        await self.get_sudoers()
        
        logger.info(f"✅ Cache loaded: {len(self.chats)} chats, {len(self.users)} users, {len(self.blacklisted)} blacklisted.")

# ==============================================================================
# preload.py - Background Track Preload Manager
# ==============================================================================
# This module handles intelligent background downloading of upcoming tracks
# to eliminate gaps between songs during playback.
#
# Features:
# - Downloads next 2-3 tracks in background while current track plays
# - Respects existing download semaphore limits (max 5 concurrent)
# - Automatically cancels preload tasks when queue changes
# - Prevents duplicate downloads
# - Smart prioritization (next track = highest priority)
# ==============================================================================

import asyncio
from pathlib import Path
from typing import Dict, Set

from HasiiMusic import logger


class PreloadManager:
    """
    Manages background preloading of upcoming tracks in queue.
    
    This class ensures seamless transitions between songs by downloading
    upcoming tracks while the current track is still playing.
    """
    
    def __init__(self):
        """Initialize the preload manager."""
        # Track active preload tasks per chat: {chat_id: set of asyncio.Task}
        self._preload_tasks: Dict[int, Set[asyncio.Task]] = {}
        
        # Track which items are currently being preloaded to prevent duplicates
        self._preloading: Dict[int, Set[str]] = {}  # {chat_id: set of track IDs}
    
    async def start_preload(self, chat_id: int, count: int = 2) -> None:
        """
        Start preloading upcoming tracks for a chat.
        
        Args:
            chat_id: The chat ID to preload tracks for
            count: Number of upcoming tracks to preload (default: 2)
        """
        from HasiiMusic import queue, yt
        
        # Get upcoming tracks from queue
        upcoming_tracks = queue.peek_next(chat_id, count)
        
        if not upcoming_tracks:
            return
        
        # Initialize tracking sets if needed
        if chat_id not in self._preload_tasks:
            self._preload_tasks[chat_id] = set()
        if chat_id not in self._preloading:
            self._preloading[chat_id] = set()
        
        # Start preload task for each track that needs downloading
        for track in upcoming_tracks:
            # Skip if already downloaded or currently being preloaded
            if queue.is_downloaded(track):
                continue
            
            track_id = getattr(track, 'id', None)
            if not track_id or track_id in self._preloading[chat_id]:
                continue
            
            # Mark as being preloaded
            self._preloading[chat_id].add(track_id)
            
            # Create background task for this track
            task = asyncio.create_task(
                self._preload_track(chat_id, track)
            )
            self._preload_tasks[chat_id].add(task)
            
            # Add callback to clean up task when done
            task.add_done_callback(
                lambda t, cid=chat_id: self._cleanup_task(cid, t)
            )
    
    async def _preload_track(self, chat_id: int, track) -> None:
        """
        Preload a single track in the background.
        
        Args:
            chat_id: The chat ID this track belongs to
            track: Track object to preload
        """
        from HasiiMusic import yt
        
        try:
            track_id = track.id
            is_live = getattr(track, 'is_live', False)
            
            # Download the track (uses existing semaphore for rate limiting)
            file_path = await yt.download(track_id, is_live=is_live)
            
            if file_path:
                # Update track with downloaded file path
                track.file_path = file_path
            else:
                # Silent failure - track will download normally when needed
                pass
        
        except asyncio.CancelledError:
            # Task was cancelled (queue changed, playback stopped, etc.)
            raise
        
        except Exception as e:
            # Log error but don't crash - track will be downloaded when it's time to play
            logger.error(f"❌ Error preloading track {track.id} for chat {chat_id}: {e}")
        
        finally:
            # Remove from preloading set
            if chat_id in self._preloading and track.id in self._preloading[chat_id]:
                self._preloading[chat_id].remove(track.id)
    
    async def cancel_preload(self, chat_id: int) -> None:
        """
        Cancel all active preload tasks for a chat.
        
        Called when:
        - Queue is cleared
        - Playback is stopped
        - Track is skipped (may need to re-prioritize)
        
        Args:
            chat_id: The chat ID to cancel preloading for
        """
        if chat_id not in self._preload_tasks:
            return
        
        tasks = self._preload_tasks[chat_id].copy()
        
        # Cancel all tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait for all tasks to finish cancellation
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Clean up tracking
        self._preload_tasks[chat_id].clear()
        if chat_id in self._preloading:
            self._preloading[chat_id].clear()
    
    def _cleanup_task(self, chat_id: int, task: asyncio.Task) -> None:
        """
        Clean up completed task from tracking.
        
        Args:
            chat_id: The chat ID this task belongs to
            task: The completed task to clean up
        """
        if chat_id in self._preload_tasks:
            self._preload_tasks[chat_id].discard(task)
# ==============================================================================
# telegram.py - Telegram Media Download Handler
# ==============================================================================
# This file handles downloading media files from Telegram messages.
# Features:
# - Progress tracking during download
# - Cancel download functionality
# - File size and duration validation
# - Prevents duplicate downloads of the same file
# ==============================================================================

import asyncio
import os
import time

from pyrogram import types

from HasiiMusic import config
from HasiiMusic.helpers import Media, buttons, utils


class Telegram:
    def __init__(self):
        """Initialize the Telegram download handler."""
        self.active = [
        ]  # List of currently downloading file IDs (prevent duplicates)
        self.events = {}  # Dictionary of download events for cancellation
        # Track last progress update time (for rate limiting)
        self.last_edit = {}
        self.active_tasks = {}  # Active download tasks for cancellation
        self.sleep = 5  # Minimum seconds between progress updates

    def get_media(self, msg: types.Message) -> bool:
        """Check if message contains downloadable media."""
        return any([msg.audio, msg.document, msg.voice, msg.video])

    async def download(self, msg: types.Message, sent: types.Message) -> Media | None:
        """
        Download media from a Telegram message with progress tracking.

        Args:
            msg: The message containing the media
            sent: The status message to update with progress

        Returns:
            Media object if successful, None if failed or cancelled
        """
        msg_id = sent.id
        event = asyncio.Event()  # Event for cancellation
        self.events[msg_id] = event
        self.last_edit[msg_id] = 0  # Initialize last edit time
        start_time = time.time()  # Track download start time

        # Extract media information from message
        media = msg.audio or msg.voice or msg.video or msg.document
        # Detect if this is a video file
        is_video = bool(msg.video) or (msg.document and getattr(msg.document, "mime_type", "").startswith("video/"))
        # Unique file identifier
        file_id = getattr(media, "file_unique_id", None)
        file_ext = getattr(media, "file_name", "").split(
            ".")[-1]  # File extension
        file_size = getattr(media, "file_size", 0)  # File size in bytes
        file_title = getattr(
            media, "title", "Telegram File") or "Telegram File"  # Media title
        duration = getattr(media, "duration", 0)  # Duration in seconds

        # Validate duration limit (configured in config.py)
        if duration > config.DURATION_LIMIT:
            await sent.edit_text(sent.lang["play_duration_limit"].format(config.DURATION_LIMIT // 60))
            return await sent.stop_propagation()

        # Validate file size (max 200 MB)
        if file_size > 200 * 1024 * 1024:
            await sent.edit_text(sent.lang["dl_limit"])
            return await sent.stop_propagation()

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
            text = sent.lang["dl_progress"].format(
                utils.format_size(current),
                utils.format_size(total),
                percent,
                utils.format_size(speed),
                eta,
            )

            await sent.edit_text(
                text, reply_markup=buttons.cancel_dl(sent.lang["cancel"])
            )

        try:
            file_path = f"downloads/{file_id}.{file_ext}"
            if not os.path.exists(file_path):
                if file_id in self.active:
                    await sent.edit_text(sent.lang["dl_active"])
                    return await sent.stop_propagation()

                self.active.append(file_id)
                task = asyncio.create_task(
                    msg.download(file_name=file_path, progress=progress)
                )
                self.active_tasks[msg_id] = task
                await task
                self.active.remove(file_id)
                self.active_tasks.pop(msg_id, None)
                await sent.edit_text(
                    sent.lang["dl_complete"].format(
                        round(time.time() - start_time, 2))
                )

            # Format duration with hours support
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
        except asyncio.CancelledError:
            return await sent.stop_propagation()
        finally:
            self.events.pop(msg_id, None)
            self.last_edit.pop(msg_id, None)
            self.active = [f for f in self.active if f != file_id]

    async def cancel(self, query: types.CallbackQuery):
        event = self.events.get(query.message.id)
        task = self.active_tasks.pop(query.message.id, None)
        if event:
            event.set()

        if task and not task.done():
            task.cancel()
        if event or task:
            await query.edit_message_text(
                query.lang["dl_cancel"].format(query.from_user.mention)
            )
        else:
            await query.answer(query.lang["dl_not_found"], show_alert=True)
# ==============================================================================
# userbot.py - Assistant/Userbot Client Manager
# ==============================================================================
# This file manages assistant accounts (userbots) that join voice chats to play music.
# Assistants are user accounts (not bots) that can join and stream audio/video.
# You can configure up to 3 assistants using SESSION1, SESSION2, SESSION3 variables.
# ==============================================================================

from pyrogram import Client

from HasiiMusic import config, logger


class Userbot(Client):
    def __init__(self):
        """
        Initialize userbot with multiple assistant clients.

        Creates up to 3 assistant clients based on available session strings.
        Each assistant can independently join voice chats and stream music.
        More assistants = ability to serve more groups simultaneously.
        """
        self.clients = []  # List to store all active assistant clients

        # Map of client names to their session string config keys
        clients = {"one": "SESSION1", "two": "SESSION2", "three": "SESSION3"}

        # Create a Pyrogram client for each configured session
        for key, string_key in clients.items():
            # Unique name: HasiiTuneUB1, HasiiTuneUB2, etc.
            name = f"HasiiTuneUB{key[-1]}"
            # Get session string from config
            session = getattr(config, string_key)

            # Create and attach the client as an attribute (self.one, self.two, self.three)
            setattr(
                self,
                key,
                Client(
                    name=name,
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=session,  # Pyrogram session string
                ),
            )

    async def boot_client(self, num: int, ub: Client):
        """
        Boot a client and perform initial setup.
        Args:
            num (int): The client number to boot (1, 2, or 3).
            ub (Client): The userbot client instance.
        Raises:
            SystemExit: If the client fails to send a message in the log group.
        """
        clients = {
            1: self.one,
            2: self.two,
            3: self.three,
        }
        client = clients[num]
        try:
            await client.start()
        except Exception as e:
            logger.error(f"❌ Assistant {num} failed to start: {e}")
            logger.error(f"   This could be due to:")
            logger.error(f"   • Invalid session string (STRING_SESSION{num})")
            logger.error(f"   • Session logged out from another device")
            logger.error(f"   • Network/connectivity issues")
            return  # Don't raise SystemExit, just skip this assistant

        try:
            await client.send_message(config.LOGGER_ID, f"Assistant {num} Started")
        except Exception as e:
            logger.warning(
                f"⚠️ Assistant {num} couldn't send message to logger: {e}")
            # Continue anyway - this is not critical

        client.id = client.me.id if hasattr(
            client, 'me') and client.me else None
        client.name = client.me.first_name if hasattr(
            client, 'me') and client.me else f"Assistant{num}"
        client.username = client.me.username if hasattr(
            client, 'me') and client.me else None
        client.mention = client.me.mention if hasattr(
            client, 'me') and client.me else client.name
        self.clients.append(client)
        logger.info(f"👤 Assistant {num} started as @{client.username}")

    async def boot(self):
        """
        Asynchronously starts the assistants.
        """
        if config.SESSION1:
            await self.boot_client(1, self.one)
        if config.SESSION2:
            await self.boot_client(2, self.two)
        if config.SESSION3:
            await self.boot_client(3, self.three)

    async def exit(self):
        """
        Asynchronously stops the assistants.
        """
        try:
            if config.SESSION1 and hasattr(self.one, 'is_connected') and self.one.is_connected:
                await self.one.stop()
        except Exception as e:
            logger.warning(f"Error stopping assistant 1: {e}")
        
        try:
            if config.SESSION2 and hasattr(self.two, 'is_connected') and self.two.is_connected:
                await self.two.stop()
        except Exception as e:
            logger.warning(f"Error stopping assistant 2: {e}")
        
        try:
            if config.SESSION3 and hasattr(self.three, 'is_connected') and self.three.is_connected:
                await self.three.stop()
        except Exception as e:
            logger.warning(f"Error stopping assistant 3: {e}")
        
        logger.info("Assistants stopped.")
# ==============================================================================
# youtube.py - YouTube Download & Search Handler
# ==============================================================================
# This file handles all YouTube-related operations:
# - Searching for videos/audio
# - Downloading YouTube content using yt-dlp
# - Managing YouTube cookies for age-restricted content
# - Caching search results for better performance
# - Validating YouTube URLs
# ==============================================================================

import os
import re
import yt_dlp
import random
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional, Union

from pyrogram import enums, types
from py_yt import Playlist, VideosSearch
from HasiiMusic import logger
from HasiiMusic.helpers import Track, utils


class YouTube:
    def __init__(self):
        """Initialize YouTube handler with configuration and caching."""
        self.base = "https://www.youtube.com/watch?v="  # Base YouTube URL
        self.cookies = []  # List of available cookie files
        self.checked = False  # Whether cookies directory has been checked
        self.warned = False  # Whether missing cookies warning has been shown

        # Regular expression to match YouTube URLs (videos, shorts, playlists)
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )

        # Cache search results to reduce API calls (10 minute TTL)
        self.search_cache = {}  # {"query_video": (result, timestamp)}
        self.cache_time = {}  # Deprecated, using tuple in search_cache instead

        # **PERFORMANCE FIX**: Limit concurrent downloads to prevent bandwidth saturation
        # With 15-20 groups, unlimited concurrent downloads cause 320+ connections
        self._download_semaphore = asyncio.Semaphore(5)  # Max 5 simultaneous downloads

    def get_cookies(self):
        if not self.checked:
            for file in os.listdir("HasiiMusic/cookies"):
                if file.endswith(".txt"):
                    self.cookies.append(file)
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return f"HasiiMusic/cookies/{random.choice(self.cookies)}"

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("🍪 Saving cookies from urls...")
        saved_count = 0
        for url in urls:
            try:
                path = f"HasiiMusic/cookies/cookie{random.randint(10000, 99999)}.txt"
                link = url.replace("me/", "me/raw/")
                async with aiohttp.ClientSession() as session:
                    async with session.get(link) as resp:
                        if resp.status != 200:
                            logger.error(f"❌ Cookie download failed: HTTP {resp.status} from {url}")
                            continue
                        content = await resp.read()
                        if not content or len(content) < 50:
                            logger.error(f"❌ Cookie file empty or invalid from {url}")
                            continue
                        with open(path, "wb") as fw:
                            fw.write(content)
                        if os.path.exists(path) and os.path.getsize(path) > 0:
                            saved_count += 1
                            # Add the new cookie file to the list immediately
                            cookie_filename = os.path.basename(path)
                            if cookie_filename not in self.cookies:
                                self.cookies.append(cookie_filename)
                            logger.info(f"✅ Saved: {cookie_filename} ({len(content)} bytes)")
            except Exception as e:
                logger.error(f"❌ Cookie download error from {url}: {e}")
        
        # Force refresh of cookie list after download
        self.checked = True
        
        if saved_count > 0:
            logger.info(f"✅ Cookies saved. ({saved_count} file(s))")
        else:
            logger.error("❌ No cookies saved! Check COOKIE_URL in .env. YouTube downloads will fail!")

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def url(self, message_1: types.Message) -> Union[str, None]:
        messages = [message_1]
        link = None
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)

        for message in messages:
            text = message.text or message.caption or ""

            if message.entities:
                for entity in message.entities:
                    if entity.type == enums.MessageEntityType.URL:
                        link = text[entity.offset: entity.offset +
                                    entity.length]
                        break

            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        link = entity.url
                        break

        if link:
            return link.split("&si")[0].split("?si")[0]
        return None

    async def search(self, query: str, m_id: int) -> Track | None:
        # Check cache first (10-minute TTL)
        cache_key = query
        current_time = asyncio.get_event_loop().time()

        if cache_key in self.search_cache:
            cached_result, cache_timestamp = self.search_cache[cache_key]
            if current_time - cache_timestamp < 600:  # 10 minutes
                # Return cached result with new message_id
                cached_result.message_id = m_id
                return cached_result

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
                thumbnail=data.get(
                    "thumbnails", [{}])[-1].get("url").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                is_live=is_live,
            )

            # Cache the result
            self.search_cache[cache_key] = (track, current_time)
            # Limit cache size to 100 entries
            if len(self.search_cache) > 100:
                oldest_key = min(self.search_cache.keys(),
                                 key=lambda k: self.search_cache[k][1])
                del self.search_cache[oldest_key]

            return track
        return None

    async def playlist(self, limit: int, user: str, url: str) -> list[Track]:
        try:
            plist = await Playlist.get(url)
            tracks = []

            # Check if plist has videos
            if not plist or "videos" not in plist or not plist["videos"]:
                return []

            for data in plist["videos"][:limit]:
                try:
                    # Get thumbnail safely
                    thumbnails = data.get("thumbnails", [])
                    thumbnail_url = ""
                    if thumbnails and len(thumbnails) > 0:
                        thumbnail_url = thumbnails[-1].get(
                            "url", "").split("?")[0]

                    # Get link safely
                    link = data.get("link", "")
                    if "&list=" in link:
                        link = link.split("&list=")[0]

                    track = Track(
                        id=data.get("id", ""),
                        channel_name=data.get("channel", {}).get("name", ""),
                        duration=data.get("duration", "0:00"),
                        duration_sec=utils.to_seconds(
                            data.get("duration", "0:00")),
                        title=(data.get("title", "Unknown")[:25]),
                        thumbnail=thumbnail_url,
                        url=link,
                        user=user,
                        view_count="",
                    )
                    tracks.append(track)
                except Exception as e:
                    # Skip individual track errors
                    continue

            return tracks
        except KeyError as e:
            # Handle YouTube API structure changes
            raise Exception(
                f"Failed to parse playlist. YouTube may have changed their structure.")
        except Exception as e:
            # Re-raise other exceptions
            raise

    async def download(self, video_id: str, is_live: bool = False) -> Optional[str]:
        url = self.base + video_id

        # For live streams, extract the direct stream URL using yt-dlp with cookies
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
                    except yt_dlp.utils.ExtractorError as ex:
                        error_msg = str(ex)
                        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                            logger.error(
                                "YouTube bot detection triggered. Please update cookies.")
                        elif "not available" in error_msg.lower():
                            logger.error(
                                "Video format not available or region-blocked.")
                        else:
                            logger.error(
                                "Live stream URL extraction failed: %s", ex)
                        return None
                    except yt_dlp.utils.DownloadError as ex:
                        error_msg = str(ex)
                        if "failed to load cookies" in error_msg.lower() or "netscape format" in error_msg.lower():
                            logger.error(
                                "❌ Corrupted cookie file detected for live stream, removing: %s", cookie)
                            # Remove corrupted cookie
                            if cookie and cookie in self.cookies:
                                self.cookies.remove(cookie)
                            try:
                                os.remove(f"HasiiMusic/cookies/{cookie}")
                            except:
                                pass
                        else:
                            logger.error(
                                "Unexpected error during live stream extraction: %s", ex)
                        return None
                    except Exception as ex:
                        logger.error(
                            "Unexpected error during live stream extraction: %s", ex)
                        return None

            stream_url = await asyncio.to_thread(_extract_url)
            return stream_url if stream_url else url

        # Download audio file
        ext = "webm"
        filename = f"downloads/{video_id}.{ext}"
        
        # Ensure downloads directory exists with write permissions
        downloads_dir = Path("downloads")
        if not downloads_dir.exists():
            try:
                downloads_dir.mkdir(parents=True, exist_ok=True)
                logger.info("📁 Created downloads directory")
            except Exception as e:
                logger.error(f"❌ Cannot create downloads directory: {e}")
                return None

        if Path(filename).exists():
            return filename

        # **PERFORMANCE FIX**: Use semaphore to limit concurrent downloads
        # Prevents bandwidth saturation when 15-20 groups download simultaneously
        async with self._download_semaphore:
            cookie = self.get_cookies()
            base_opts = {
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "quiet": True,
                "noplaylist": True,
                "geo_bypass": True,
                "no_warnings": True,
                "overwrites": False,
                "nocheckcertificate": True,
                "cookiefile": cookie,
                "continuedl": True,
                "noprogress": True,
                # **PERFORMANCE FIX**: Reduced to 4 fragments for maximum stability
                # 4 fragments × 5 concurrent downloads = 20 total connections (prevents bandwidth saturation)
                # Lower = more stable but slightly slower downloads (trade-off for zero lag)
                "concurrent_fragment_downloads": 4,
                "http_chunk_size": 524288,  # 512KB chunks (smaller = more stable streaming)
                "socket_timeout": 30,  # Increased from 15s (prevents timeout on slow networks)
                "retries": 2,  # Increased from 1 (better reliability)
                "fragment_retries": 2,  # Increased from 1 (handle network hiccups)
                "ignoreerrors": True,
            }

            # High-quality audio: Opus codec in WebM container for best quality
            ydl_opts = {
                **base_opts,
                "format": "bestaudio[ext=webm][acodec=opus]/bestaudio[acodec=opus]/bestaudio",
                "postprocessors": [],  # No post-processing to preserve original quality
            }

            def _download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        ydl.download([url])
                        # Check if file was actually downloaded (handle .part rename issues)
                        if not Path(filename).exists():
                            # Wait for filesystem operations to complete
                            import time
                            import glob
                            time.sleep(3.0)  # Longer wait for slower filesystems
                            if Path(filename).exists():
                                return filename
                            
                            # Try to find .part file and rename it
                            part_file = Path(f"{filename}.part")
                            if part_file.exists():
                                try:
                                    import shutil
                                    shutil.move(str(part_file), filename)
                                    logger.info(f"✅ Renamed {part_file} to {filename}")
                                    return filename
                                except Exception as rename_ex:
                                    logger.error(f"❌ Failed to rename .part file: {rename_ex}")
                                    return None
                            
                            # Try to find any variant of the file (different extension)
                            video_id_pattern = str(Path(filename).stem)
                            possible_files = glob.glob(f"downloads/{video_id_pattern}.*")
                            if possible_files:
                                # Use the first match
                                found_file = possible_files[0]
                                logger.info(f"✅ Found alternative file: {found_file}")
                                return found_file
                            
                            logger.warning(f"⚠️ Download completed but file not found: {filename}")
                            return None
                        return filename
                    except yt_dlp.utils.ExtractorError as ex:
                        error_msg = str(ex)
                        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                            logger.warning(
                                f"⚠️ YouTube bot detection for {video_id}. This is temporary.")
                        elif "not available" in error_msg.lower():
                            logger.error(
                                "❌ Video not available: May be region-blocked or private.")
                        elif "age" in error_msg.lower():
                            logger.error(
                                "❌ Age-restricted video: Cookies required.")
                        else:
                            logger.error("❌ YouTube extraction failed: %s", ex)
                        return None
                    except yt_dlp.utils.DownloadError as ex:
                        error_msg = str(ex)
                        if "416" in error_msg or "Requested range not satisfiable" in error_msg:
                            # HTTP 416 - file partially downloaded, delete and retry won't help
                            logger.warning(f"⚠️ Range error for {video_id}, skipping")
                        elif "failed to load cookies" in error_msg.lower() or "netscape format" in error_msg.lower():
                            logger.error(
                                "❌ Corrupted cookie file detected, removing: %s", cookie)
                            # Remove corrupted cookie from list and filesystem
                            if cookie and cookie in self.cookies:
                                self.cookies.remove(cookie)
                            try:
                                os.remove(f"HasiiMusic/cookies/{cookie}")
                            except:
                                pass
                        else:
                            logger.warning(f"⚠️ Download error for {video_id}: {ex}")
                        return None
                    except Exception as ex:
                        logger.warning(f"⚠️ Unexpected download error for {video_id}: {ex}")
                        return None

            # Run blocking download in thread pool to avoid blocking event loop
            return await asyncio.get_event_loop().run_in_executor(None, _download)
# ==============================================================================
# __init__.py - Helper Functions Export Module
# ==============================================================================
# This file exports all helper functions and classes for easy importing.
# Instead of importing from individual files, plugins can simply:
#   from HasiiMusic.helpers import buttons, thumb, utils, Queue, Track
#
# This makes imports cleaner and provides a single entry point for all helpers.
# ==============================================================================

from ._admins import admin_check, can_manage_vc, is_admin, reload_admins
from ._dataclass import Media, Track
from ._exec import format_exception, meval
from ._inline import Inline
from ._queue import Queue
from ._thumbnails import Thumbnail
from ._utilities import Utilities

buttons = Inline()
thumb = Thumbnail()
utils = Utilities()
# ==============================================================================
# _admins.py - Admin Permission Decorators
# ==============================================================================
# This file contains decorator functions that check user permissions.
# These decorators are used to protect commands that require admin rights.
#
# Available decorators:
# - @admin_check: Requires user to be group admin or sudo user
# - @can_manage_vc: Requires permission to manage voice chats (or be authorized)
# ==============================================================================

from functools import wraps

from pyrogram import StopPropagation, enums, types

from HasiiMusic import app, db


def admin_check(func):
    """
    Decorator to check if user is an admin in the chat.

    - Allows sudo users (owner) to bypass admin check
    - Checks if user is in the admin list for the chat
    - Returns error message if user is not admin

    Usage:
        @admin_check
        async def my_admin_command(_, message):
            # Only admins can execute this
            pass
    """
    @wraps(func)
    async def wrapper(_, update: types.Message | types.CallbackQuery, *args, **kwargs):
        # Helper function to send reply (works for messages and callbacks)
        async def reply(text):
            if isinstance(update, types.Message):
                return await update.reply_text(text)
            else:
                return await update.answer(text, show_alert=True)

        # Handle anonymous admins (from_user is None)
        if not update.from_user:
            return

        # Get chat ID and user ID from update
        chat_id = (
            update.chat.id
            if isinstance(update, types.Message)
            else update.message.chat.id
        )
        user_id = update.from_user.id

        # Get list of admins from database (cached)
        admins = await db.get_admins(chat_id)

        # Sudo users (bot owner) can bypass admin check
        if user_id in app.sudoers:
            return await func(_, update, *args, **kwargs)

        # Check if user is admin
        if user_id not in admins:
            return await reply(update.lang["user_no_perms"])

        # User is admin, allow execution
        return await func(_, update, *args, **kwargs)

    return wrapper


def can_manage_vc(func):
    """
    Decorator to check if user can manage voice chats.

    Allows:
    - Sudo users (bot owner)
    - Authorized users (added via /auth command)
    - Group admins with voice chat management permission

    Usage:
        @can_manage_vc
        async def my_vc_command(_, message):
            # Only users with VC permissions can execute this
            pass
    """
    @wraps(func)
    async def wrapper(_, update: types.Message | types.CallbackQuery, *args, **kwargs):
        # Get chat ID and user ID
        chat_id = (
            update.chat.id
            if isinstance(update, types.Message)
            else update.message.chat.id
        )

        # Skip if no user (channel post or anonymous admin)
        if not update.from_user:
            return

        user_id = update.from_user.id

        # Sudo users can always manage VC
        if user_id in app.sudoers:
            return await func(_, update, *args, **kwargs)

        # Check if user is in authorized users list
        if await db.is_auth(chat_id, user_id):
            return await func(_, update, *args, **kwargs)

        admins = await db.get_admins(chat_id)
        if user_id in admins:
            return await func(_, update, *args, **kwargs)

        if isinstance(update, types.Message):
            return await update.reply_text(update.lang["user_no_perms"])
        else:
            return await update.answer(update.lang["user_no_perms"], show_alert=True)

    return wrapper


async def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id in await db.get_admins(chat_id):
        return True
    try:
        member = await app.get_chat_member(chat_id, user_id)
        return member.status in [
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        ]
    except:
        raise StopPropagation


async def reload_admins(chat_id: int) -> list[int]:
    try:
        admins = [
            admin
            async for admin in app.get_chat_members(
                chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS
            )
            if not admin.user.is_bot
        ]
        return [admin.user.id for admin in admins]
    except:
        return []


async def is_admin_callback(query: types.CallbackQuery) -> bool:
    """Check if callback query sender is admin"""
    if not query.from_user:
        return False
    
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    
    # Sudo users are always admin
    if user_id in app.sudoers:
        return True
    
    # Check admin list
    admins = await db.get_admins(chat_id)
    return user_id in admins
# ==============================================================================
# _dataclass.py - Data Classes for Media and Tracks
# ==============================================================================
# This file defines data structures used throughout the bot:
# - Media: Represents Telegram audio/video files
# - Track: Represents YouTube tracks
#
# These dataclasses make it easy to pass media information between functions
# while maintaining type safety and clear structure.
# ==============================================================================

from dataclasses import dataclass


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
# _exec.py - Code Execution Helper for Eval Command
# ==============================================================================
# This file provides safe code execution functionality for the /eval command.
# Features:
# - Async code evaluation (supports await)
# - Expression and statement execution
# - Exception handling and formatting
# - Sandbox environment for code execution
#
# Used by: admin-controles/eval.py
# ==============================================================================

import os
import ast
import traceback
from typing import Optional


async def meval(code: str, globs: dict, **kwargs):
    """
    Asynchronously evaluate a code string in a controlled environment.
    """

    # Copy globals to avoid mutation
    globs = globs.copy()

    # Special globals (for relative imports)
    _global_arg = "_globs"
    while _global_arg in globs:
        _global_arg = "_" + _global_arg

    kwargs[_global_arg] = {k: globs[k]
                           for k in ("__name__", "__package__") if k in globs}

    root = ast.parse(code, mode="exec")
    if not root.body:
        return None

    ret_name = "_ret"
    while any(isinstance(n, ast.Name) and n.id == ret_name for n in ast.walk(root)) or ret_name in globs:
        ret_name = "_" + ret_name

    body = []
    body.append(ast.Expr(ast.Call(
        func=ast.Attribute(
            value=ast.Call(func=ast.Name(
                id="globals", ctx=ast.Load()), args=[], keywords=[]),
            attr="update", ctx=ast.Load()
        ),
        args=[], keywords=[ast.keyword(
            arg=None, value=ast.Name(id=_global_arg, ctx=ast.Load()))]
    )))
    body.append(ast.Assign(
        targets=[ast.Name(id=ret_name, ctx=ast.Store())],
        value=ast.List(elts=[], ctx=ast.Load())
    ))

    for node in root.body:
        if isinstance(node, ast.Expr):
            new_node = ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=ast.Name(
                        id=ret_name, ctx=ast.Load()), attr="append", ctx=ast.Load()),
                    args=[node.value], keywords=[]
                )
            )
            ast.copy_location(new_node, node)
            body.append(new_node)
        else:
            body.append(node)
    body.append(ast.Return(value=ast.Name(id=ret_name, ctx=ast.Load())))

    func_def = ast.AsyncFunctionDef(
        name="tmp",
        args=ast.arguments(
            posonlyargs=[], args=[], vararg=None,
            kwonlyargs=[ast.arg(arg=k) for k in kwargs.keys()],
            kw_defaults=[None] * len(kwargs),
            kwarg=None, defaults=[]
        ),
        body=body, decorator_list=[]
    )
    ast.fix_missing_locations(func_def)

    # Compile & execute
    locs = {}
    exec(compile(ast.Module([func_def], type_ignores=[]),
         "<meval>", "exec"), {}, locs)

    result = await locs["tmp"](**kwargs)
    if not result:
        return None
    result = [await r if hasattr(r, "__await__") else r for r in result]
    result = [r for r in result if r is not None]

    return result[0] if len(result) == 1 else (result or None)


def format_exception(exc: BaseException, tb: Optional[list[traceback.FrameSummary]] = None) -> str:
    """Format exception traceback into a readable string."""
    if tb is None:
        tb = traceback.extract_tb(exc.__traceback__)

    cwd = os.getcwd()
    for frame in tb:
        if cwd in frame.filename:
            frame.filename = os.path.relpath(frame.filename)

    return (
        "Traceback (most recent call last):\n"
        f"{''.join(traceback.format_list(tb))}"
        f"{type(exc).__name__}{': ' + str(exc) if str(exc) else ''}"
    )
# ==============================================================================
# _inline.py - Inline Keyboard Button Builder
# ==============================================================================
# This file provides helper functions to create inline keyboard buttons.
# Used to build:
# - Playback control buttons (play, pause, skip, stop, etc.)
# - Language selection menus
# - Help menus and navigation
# - Download cancel buttons
# - Settings buttons
# ==============================================================================

from pyrogram import types

from HasiiMusic import app, config, lang



class Inline:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self.ikb = types.InlineKeyboardButton

    def cancel_dl(self, text) -> types.InlineKeyboardMarkup:
        return self.ikm([[self.ikb(text=text, callback_data=f"cancel_dl")]])

    def controls(
        self,
        chat_id: int,
        status: str = None,
        timer: str = None,
        remove: bool = False,
    ) -> types.InlineKeyboardMarkup:
        keyboard = []
        if status:
            keyboard.append(
                [self.ikb(
                    text=status, callback_data=f"controls status {chat_id}")]
            )
        elif timer:
            keyboard.append(
                [self.ikb(
                    text=timer, callback_data=f"controls status {chat_id}")]
            )

        if not remove:
            # Seek buttons row
            keyboard.append(
                [
                    self.ikb(
                        text="« 10", callback_data=f"controls seek_back_10 {chat_id}"),
                    self.ikb(
                        text="« 30", callback_data=f"controls seek_back_30 {chat_id}"),
                    self.ikb(
                        text="30 »", callback_data=f"controls seek_forward_30 {chat_id}"),
                    self.ikb(
                        text="10 »", callback_data=f"controls seek_forward_10 {chat_id}"),
                ]
            )
            # Main control buttons row
            keyboard.append(
                [
                    self.ikb(
                        text="▷", callback_data=f"controls resume {chat_id}"),
                    self.ikb(
                        text="II", callback_data=f"controls pause {chat_id}"),
                    self.ikb(
                        text="↻", callback_data=f"controls replay {chat_id}"),
                    self.ikb(
                        text="‣‣I", callback_data=f"controls skip {chat_id}"),
                    self.ikb(
                        text="▢", callback_data=f"controls stop {chat_id}"),
                ]
            )
            # Delete button as full-width button at bottom
            keyboard.append(
                [
                    self.ikb(
                        text="ᴅᴇʟᴇᴛᴇ", callback_data=f"controls close {chat_id}"),
                ]
            )
        return self.ikm(keyboard)

    def help_markup(
        self, _lang: dict, back: bool = False
    ) -> types.InlineKeyboardMarkup:
        if back:
            rows = [
                [
                    self.ikb(text=_lang["back"], callback_data="help back"),
                    self.ikb(text=_lang["close"], callback_data="help close"),
                ]
            ]
        else:
            cbs = ["admins", "auth", "blist", "sudo",
                   "ping", "play", "queue", "stats", "games"]
            buttons = [
                self.ikb(text=_lang[f"help_btn_{cb}"], callback_data=f"help {cb}")
                for cb in cbs
            ]
            rows = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]

        return self.ikm(rows)


    def ping_markup(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm([[self.ikb(text=text, url=config.SUPPORT_CHAT)]])

    def play_queued(
        self, chat_id: int, item_id: str, _text: str
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text="▷", callback_data=f"controls resume {chat_id}"),
                    self.ikb(
                        text="∣ ∣", callback_data=f"controls pause {chat_id}"),
                    self.ikb(
                        text=">>", callback_data=f"controls skip {chat_id}"),
                    self.ikb(
                        text="▣", callback_data=f"controls stop {chat_id}"),
                ],
                [
                    self.ikb(
                        text="ᴅᴇʟᴇᴛᴇ", callback_data=f"controls close {chat_id}"),
                ]
            ]
        )

    def queue_markup(
        self, chat_id: int, _text: str, playing: bool
    ) -> types.InlineKeyboardMarkup:
        _action = "pause" if playing else "resume"
        return self.ikm(
            [[self.ikb(
                text=_text, callback_data=f"controls {_action} {chat_id} q")]]
        )

    def settings_markup(
        self, lang: dict, admin_only: bool, language: str, chat_id: int
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text=lang["play_mode"] + " ➜",
                        callback_data=f"controls status {chat_id}",
                    ),
                    self.ikb(text=admin_only, callback_data="playmode"),
                ],
            ]
        )

    def start_key(
        self, lang: dict, private: bool = False
    ) -> types.InlineKeyboardMarkup:
        rows = [
            [
                self.ikb(
                    text=lang["add_me"],
                    url=f"https://t.me/{app.username}?startgroup=true",
                )
            ],
            [self.ikb(text=lang["help"], callback_data="help")],
            [
                self.ikb(text=lang["support"], url=config.SUPPORT_CHAT),
                self.ikb(text=lang["channel"], url=config.SUPPORT_CHANNEL),
            ],
        ]
        if private:
            rows += [
                [
                    self.ikb(
                        text=lang["source"],
                        url="https://hasiimusic.hasindunagolla.live/",
                    )
                ]
            ]
        return self.ikm(rows)

    def yt_key(self, link: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(text="ᴄᴏᴘʏ ʟɪɴᴋ", copy_text=link),
                    self.ikb(text="ᴏᴘᴇɴ ɪɴ ʏᴏᴜᴛᴜʙᴇ", url=link),
                ],
            ]
        )
# ==============================================================================
# _queue.py - Music Queue Manager
# ==============================================================================
# This file manages the music queue for each chat.
# - Each chat has its own separate queue
# - Queues are stored in memory (lost on restart)
# - Supports adding, removing, and retrieving songs from the queue
# - Uses deque (double-ended queue) for efficient operations
# ==============================================================================

from collections import defaultdict, deque
from typing import Union

from ._dataclass import Media, Track

# MediaItem can be either a Media or Track object
MediaItem = Union[Media, Track]


class Queue:
    def __init__(self):
        """Initialize the queue manager with empty queues for all chats."""
        # Dictionary mapping chat_id to its queue (deque of Media/Track items)
        # defaultdict automatically creates a new deque for new chat_ids
        self.queues: dict[int, deque[MediaItem]] = defaultdict(deque)

    def add(self, chat_id: int, item: MediaItem) -> int:
        """Add a song to the end of the queue and return its position."""
        self.queues[chat_id].append(item)  # Add to end of queue
        return len(self.queues[chat_id]) - 1  # Return position (0-based index)

    def check_item(self, chat_id: int, item_id: str) -> tuple[int, MediaItem | None]:
        """Check if an item with the given ID exists in the queue."""
        pos, track = next(
            (
                (i, track)
                for i, track in enumerate(list(self.queues[chat_id]))
                if track.id == item_id
            ),
            (-1, None),
        )
        return pos, track

    def force_add(
        self, chat_id: int, item: MediaItem, remove: int | bool = False
    ) -> None:
        """Replace the currently playing item with a new one."""
        self.remove_current(chat_id)
        self.queues[chat_id].appendleft(item)
        if remove:
            self.queues[chat_id].rotate(-remove)
            self.queues[chat_id].popleft()
            self.queues[chat_id].rotate(remove)

    def get_current(self, chat_id: int) -> MediaItem | None:
        """Return the currently playing item (first in queue), if any."""
        return self.queues[chat_id][0] if self.queues[chat_id] else None

    def get_next(self, chat_id: int, check: bool = False) -> MediaItem | None:
        """Remove current item and return the next one, or None if empty."""
        if not self.queues[chat_id]:
            return None
        if check:
            return self.queues[chat_id][1] if len(self.queues[chat_id]) > 1 else None

        self.queues[chat_id].popleft()
        return self.queues[chat_id][0] if self.queues[chat_id] else None

    def get_queue(self, chat_id: int) -> list[MediaItem]:
        """Return the full queue including the currently playing item."""
        return list(self.queues[chat_id])
    
    def get_all(self, chat_id: int) -> list[MediaItem]:
        """Alias for get_queue() - return the full queue including currently playing item."""
        return self.get_queue(chat_id)

    def remove_current(self, chat_id: int) -> None:
        """Remove the currently playing item only (if exists)."""
        if self.queues[chat_id]:
            self.queues[chat_id].popleft()

    def clear(self, chat_id: int) -> None:
        """Clear the entire queue."""
        self.queues[chat_id].clear()

    def peek_next(self, chat_id: int, count: int = 2) -> list[MediaItem]:
        """
        Return next N upcoming tracks without removing them from queue.
        
        Args:
            chat_id: The chat ID to peek queue for
            count: Number of upcoming tracks to return (default: 2)
            
        Returns:
            List of upcoming MediaItem objects (excluding currently playing track)
        """
        if not self.queues[chat_id] or len(self.queues[chat_id]) <= 1:
            return []
        
        # Convert deque to list and skip first item (currently playing)
        queue_list = list(self.queues[chat_id])
        return queue_list[1:min(len(queue_list), count + 1)]
    
    @staticmethod
    def is_downloaded(item: MediaItem) -> bool:
        """
        Check if a track has already been downloaded.
        
        Args:
            item: MediaItem or Track object to check
            
        Returns:
            True if file_path exists and is not empty, False otherwise
        """
        return bool(getattr(item, 'file_path', None))
# ==============================================================================
# _thumbnails.py - Dynamic Thumbnail Generator
# ==============================================================================
# This file generates beautiful custom thumbnails for now playing messages.
# Features:
# - Modern frosted glass design
# - Background blur effect with album art
# - Track title and metadata display
# - Progress bar visualization
# - Social media icons
# - Responsive text sizing
# - Image caching for performance
# - Non-blocking PIL operations (runs in thread executor)
# ==============================================================================

import os
import re
import asyncio
import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from HasiiMusic import config
from HasiiMusic.helpers import Track

# Modern frosted glass design constants
PANEL_W, PANEL_H = 763, 545
PANEL_X = (1280 - PANEL_W) // 2
PANEL_Y = 88
TRANSPARENCY = 170

THUMB_W, THUMB_H = 542, 273
THUMB_X = PANEL_X + (PANEL_W - THUMB_W) // 2
THUMB_Y = PANEL_Y + 36

TITLE_X = 377
TITLE_Y = THUMB_Y + THUMB_H + 10
META_Y = TITLE_Y + 45

BAR_X, BAR_Y = 388, META_Y + 45
BAR_RED_LEN = 280
BAR_TOTAL_LEN = 480

ICONS_W, ICONS_H = 415, 45
ICONS_X = PANEL_X + (PANEL_W - ICONS_W) // 2
ICONS_Y = BAR_Y + 48

MAX_TITLE_WIDTH = 580


def trim_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """Trim text to fit within max width, adding ellipsis if needed."""
    ellipsis = "…"
    if font.getlength(text) <= max_w:
        return text
    for i in range(len(text) - 1, 0, -1):
        if font.getlength(text[:i] + ellipsis) <= max_w:
            return text[:i] + ellipsis
    return ellipsis


class Thumbnail:
    def __init__(self):
        try:
            self.title_font = ImageFont.truetype(
                "HasiiMusic/helpers/Raleway-Bold.ttf", 32)
            self.regular_font = ImageFont.truetype(
                "HasiiMusic/helpers/Inter-Light.ttf", 18)
        except OSError:
            self.title_font = self.regular_font = ImageFont.load_default()

    async def save_thumb(self, output_path: str, url: str) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                with open(output_path, "wb") as f:
                    f.write(await resp.read())
            return output_path

    async def generate(self, song: Track, size=(1280, 720)) -> str:
        """Generate thumbnail - downloads async, PIL operations in thread pool"""
        try:
            temp = f"cache/temp_{song.id}.jpg"
            output = f"cache/{song.id}_modern.png"
            if os.path.exists(output):
                return output

            # Download thumbnail (async operation)
            await self.save_thumb(temp, song.thumbnail)
            
            # **PERFORMANCE FIX**: Run PIL operations in thread executor to avoid blocking event loop
            # This prevents lag when generating thumbnails for multiple groups simultaneously
            return await asyncio.get_event_loop().run_in_executor(
                None, self._generate_sync, temp, output, song, size
            )
        except Exception:
            return config.DEFAULT_THUMB

    def _generate_sync(self, temp: str, output: str, song: Track, size=(1280, 720)) -> str:
        """Synchronous PIL operations - runs in thread pool"""
        try:
            # Prepare base image
            with Image.open(temp) as temp_img:
                base = temp_img.resize(size).convert("RGBA")

            # Create blurred background
            bg = ImageEnhance.Brightness(base.filter(
                ImageFilter.BoxBlur(10))).enhance(0.6)

            # Create frosted glass panel
            panel_area = bg.crop(
                (PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H))
            overlay = Image.new("RGBA", (PANEL_W, PANEL_H),
                                (255, 255, 255, TRANSPARENCY))
            frosted = Image.alpha_composite(panel_area, overlay)

            # Apply rounded corners to panel
            mask = Image.new("L", (PANEL_W, PANEL_H), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, PANEL_W, PANEL_H), 50, fill=255)
            bg.paste(frosted, (PANEL_X, PANEL_Y), mask)

            # Add thumbnail with rounded corners
            thumb = base.resize((THUMB_W, THUMB_H))
            tmask = Image.new("L", thumb.size, 0)
            ImageDraw.Draw(tmask).rounded_rectangle(
                (0, 0, THUMB_W, THUMB_H), 20, fill=255)
            bg.paste(thumb, (THUMB_X, THUMB_Y), tmask)

            # Draw text elements
            draw = ImageDraw.Draw(bg)

            # Clean and display title
            clean_title = re.sub(r"\W+", " ", song.title).title()
            draw.text(
                (TITLE_X, TITLE_Y),
                trim_to_width(clean_title, self.title_font, MAX_TITLE_WIDTH),
                fill="black",
                font=self.title_font
            )

            # Metadata
            draw.text(
                (TITLE_X, META_Y),
                f"YouTube | {song.view_count or 'Unknown Views'}",
                fill="black",
                font=self.regular_font
            )

            # Progress bar
            draw.line([(BAR_X, BAR_Y), (BAR_X + BAR_RED_LEN, BAR_Y)],
                      fill="red", width=6)
            draw.line([(BAR_X + BAR_RED_LEN, BAR_Y),
                      (BAR_X + BAR_TOTAL_LEN, BAR_Y)], fill="gray", width=5)
            draw.ellipse([(BAR_X + BAR_RED_LEN - 7, BAR_Y - 7),
                         (BAR_X + BAR_RED_LEN + 7, BAR_Y + 7)], fill="red")

            # Time labels
            draw.text((BAR_X, BAR_Y + 15), "00:00",
                      fill="black", font=self.regular_font)

            is_live = getattr(song, 'is_live', False)
            end_text = "Live" if is_live else song.duration
            draw.text(
                (BAR_X + BAR_TOTAL_LEN - (90 if is_live else 60), BAR_Y + 15),
                end_text,
                fill="red" if is_live else "black",
                font=self.regular_font
            )

            # Control icons (if available)
            icons_path = "HasiiMusic/helpers/play_icons.png"
            if os.path.isfile(icons_path):
                with Image.open(icons_path) as icons_img:
                    ic = icons_img.resize((ICONS_W, ICONS_H)).convert("RGBA")
                    r, g, b, a = ic.split()
                    black_ic = Image.merge(
                        "RGBA", (r.point(lambda _: 0), g.point(lambda _: 0), b.point(lambda _: 0), a))
                    bg.paste(black_ic, (ICONS_X, ICONS_Y), black_ic)

            # Save and cleanup
            bg.save(output)
            try:
                os.remove(temp)
            except OSError:
                pass

            return output
        except Exception:
            return config.DEFAULT_THUMB
# ==============================================================================
# _utilities.py - General Utility Functions
# ==============================================================================
# This file contains various helper functions used throughout the bot:
# - Time formatting (ETA, duration)
# - File size formatting (bytes to KB/MB/GB)
# - User extraction from messages (mentions, replies, user IDs)
# - Duration conversion (mm:ss to seconds)
# - Message text extraction
#
# These utilities keep code DRY (Don't Repeat Yourself) across plugins.
# ==============================================================================

import re
from pyrogram import enums, types
from HasiiMusic import app


class Utilities:
    def __init__(self):
        pass

    def format_eta(self, seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}:{seconds % 60:02d} min"
        else:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return f"{h}:{m:02d}:{s:02d} h"

    def format_size(self, bytes: int) -> str:
        if bytes >= 1024**3:
            return f"{bytes / 1024 ** 3:.2f} GB"
        elif bytes >= 1024**2:
            return f"{bytes / 1024 ** 2:.2f} MB"
        else:
            return f"{bytes / 1024:.2f} KB"

    def format_duration(self, seconds: int) -> str:
        """Format duration as HH:MM:SS or MM:SS depending on length."""
        if seconds >= 3600:  # 1 hour or more
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:  # Less than 1 hour
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes:02d}:{secs:02d}"

    def to_seconds(self, time: str) -> int:
        parts = [int(p) for p in time.strip().split(":")]
        return sum(value * 60**i for i, value in enumerate(reversed(parts)))

    async def extract_user(self, msg: types.Message) -> types.User | None:
        if msg.reply_to_message:
            return msg.reply_to_message.from_user

        if msg.entities:
            for e in msg.entities:
                if e.type == enums.MessageEntityType.TEXT_MENTION:
                    return e.user

        if msg.text:
            try:
                if m := re.search(r"@(\w{5,32})", msg.text):
                    return await app.get_users(m.group(0))
                if m := re.search(r"\b\d{6,15}\b", msg.text):
                    return await app.get_users(int(m.group(0)))
            except:
                pass

        return None

    async def play_log(
        self,
        m: types.Message,
        title: str,
        duration: str,
    ) -> None:
        if m.chat.id == app.logger:
            return
        _text = m.lang["play_log"].format(
            app.name,
            m.chat.id,
            m.chat.title,
            m.from_user.id,
            m.from_user.mention,
            m.link,
            title,
            duration,
        )
        await app.send_message(chat_id=app.logger, text=_text)

    async def send_log(self, m: types.Message) -> None:
        """Log new user to logger group when they start the bot in private chat."""
        await app.send_message(
            chat_id=app.logger,
            text=m.lang["log_user"].format(
                m.from_user.id,
                f"@{m.from_user.username}",
                m.from_user.mention,
            ),
        )
# ==============================================================================
# _play.py - Play Command Helper & Validator
# ==============================================================================
# This file contains the @checkUB decorator used by play commands.
# Validates:
# - User permissions (only real users, not anonymous admins)
# - Chat type (only supergroups)
# - Command syntax (query or reply required)
# - Queue limits
# - YouTube URL validity
#
# This decorator ensures all play commands have proper validation before execution.
# ==============================================================================

import asyncio

from pyrogram import enums, errors, types

from HasiiMusic import app, config, db, queue, yt


def checkUB(play):
    async def wrapper(_, m: types.Message):
        async def safe_reply(text):
            """Safely send reply, return None if chat doesn't allow messages"""
            try:
                return await m.reply_text(text)
            except (errors.ChatWriteForbidden, errors.ChatSendPlainForbidden):
                # Chat doesn't allow text messages - silently return
                return None
            except Exception:
                return None
        
        if not m.from_user:
            await safe_reply(m.lang["play_user_invalid"])
            return

        if m.chat.type != enums.ChatType.SUPERGROUP:
            await safe_reply(m.lang["play_chat_invalid"])
            return await app.leave_chat(m.chat.id)

        if not m.reply_to_message and (
            len(m.command) < 2 or (len(m.command)
                                   == 2 and m.command[1] == "-f")
        ):
            await safe_reply(m.lang["play_usage"])
            return

        if len(queue.get_queue(m.chat.id)) >= config.QUEUE_LIMIT:
            await safe_reply(m.lang["play_queue_full"].format(config.QUEUE_LIMIT))
            return

        force = m.command[0].endswith("force") or (
            len(m.command) > 1 and "-f" in m.command[1]
        )
        cplay = m.command[0][0] == "c"
        
        url = yt.url(m)
        # Only validate URL if not replying to media (Telegram files have t.me URLs)
        if url and not m.reply_to_message and not yt.valid(url):
            return await m.reply_text(m.lang["play_unsupported"])

        play_mode = await db.get_play_mode(m.chat.id)
        if play_mode or force:
            adminlist = await db.get_admins(m.chat.id)
            if (
                m.from_user.id not in adminlist
                and not await db.is_auth(m.chat.id, m.from_user.id)
                and not m.from_user.id in app.sudoers
            ):
                await safe_reply(m.lang["play_admin"])
                return

        if m.chat.id not in db.active_calls:
            client = await db.get_client(m.chat.id)
            try:
                member = await app.get_chat_member(m.chat.id, client.id)
                if member.status in [
                    enums.ChatMemberStatus.BANNED,
                    enums.ChatMemberStatus.RESTRICTED,
                ]:
                    try:
                        await app.unban_chat_member(
                            chat_id=m.chat.id, user_id=client.id
                        )
                    except:
                        await safe_reply(
                            m.lang["play_banned"].format(
                                app.name,
                                client.id,
                                client.mention,
                                f"@{client.username}" if client.username else None,
                            )
                        )
                        return
            except errors.ChatAdminRequired:
                await safe_reply(
                    f"<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
                    f"<blockquote>To play music in this chat, I need to be an <b>administrator</b>.\n\n"
                    f"<b>Required permissions:</b>\n"
                    f"• Manage Voice Chats\n"
                    f"• Invite Users via Link\n"
                    f"• Delete Messages\n\n"
                    f"Please promote me as admin with the required permissions.</blockquote>"
                )
                return
            except errors.UserNotParticipant:
                if m.chat.username:
                    invite_link = m.chat.username
                    try:
                        await client.resolve_peer(invite_link)
                    except:
                        pass
                else:
                    try:
                        invite_link = (await app.get_chat(m.chat.id)).invite_link
                        if not invite_link:
                            invite_link = await app.export_chat_invite_link(m.chat.id)
                    except errors.ChatAdminRequired:
                        await safe_reply(
                            f"<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
                            f"<blockquote>To play music in this chat, I need to be an <b>administrator</b>.\n\n"
                            f"<b>Required permissions:</b>\n"
                            f"• Manage Voice Chats\n"
                            f"• Invite Users via Link\n"
                            f"• Delete Messages\n\n"
                            f"Please promote me as admin with the required permissions.</blockquote>"
                        )
                        return
                    except errors.ChatAdminRequired:
                        await safe_reply(
                            f"<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
                            f"<blockquote>To play music in this chat, I need to be an <b>administrator</b>.\n\n"
                            f"<b>Required permissions:</b>\n"
                            f"• Manage Voice Chats\n"
                            f"• Invite Users via Link\n"
                            f"• Delete Messages\n\n"
                            f"Please promote me as admin with the required permissions.</blockquote>"
                        )
                        return
                    except Exception as ex:
                        await safe_reply(
                            m.lang["play_invite_error"].format(
                                type(ex).__name__)
                        )
                        return

                umm = await safe_reply(m.lang["play_invite"].format(app.name))
                if umm:
                    await asyncio.sleep(2)
                try:
                    await client.join_chat(invite_link)
                except errors.UserAlreadyParticipant:
                    pass
                except errors.InviteRequestSent:
                    try:
                        await client.approve_chat_join_request(m.chat.id, client.id)
                    except errors.ChatAdminRequired:
                        if umm:
                            try:
                                await umm.edit_text(
                                    f"<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
                                    f"<blockquote>To play music in this chat, I need to be an <b>administrator</b>.\n\n"
                                    f"<b>Required permissions:</b>\n"
                                    f"• Manage Voice Chats\n"
                                    f"• Invite Users via Link\n"
                                    f"• Delete Messages\n\n"
                                    f"Please promote me as admin with the required permissions.</blockquote>"
                                )
                            except:
                                pass
                        return
                    except Exception as ex:
                        if umm:
                            try:
                                await umm.edit_text(
                                    m.lang["play_invite_error"].format(
                                        type(ex).__name__)
                                )
                            except:
                                pass
                        return
                except errors.ChatAdminRequired:
                    if umm:
                        try:
                            await umm.edit_text(
                                f"<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
                                f"<blockquote>To play music in this chat, I need to be an <b>administrator</b>.\n\n"
                                f"<b>Required permissions:</b>\n"
                                f"• Manage Voice Chats\n"
                                f"• Invite Users via Link\n"
                                f"• Delete Messages\n\n"
                                f"Please promote me as admin with the required permissions.</blockquote>"
                            )
                        except:
                            pass
                    return
                except Exception as ex:
                    if umm:
                        try:
                            await umm.edit_text(
                                m.lang["play_invite_error"].format(type(ex).__name__)
                            )
                        except:
                            pass
                    return

                if umm:
                    try:
                        await umm.delete()
                    except:
                        pass
                await client.resolve_peer(m.chat.id)

        try:
            await m.delete()
        except:
            pass

        return await play(_, m, force, url, cplay)

    return wrapper
# ==============================================================================
# _preload.py - Background Track Preloading Manager
# ==============================================================================
# This module handles downloading the next track in the background while
# the current track is playing to ensure seamless transitions.
#
# Features:
# - Background download of next track
# - Automatic cancellation of outdated preload tasks
# - Prevents redundant downloads
# - Non-blocking async operations
# ==============================================================================

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger("HasiiMusic")


class PreloadManager:
    """
    Manages background preloading of upcoming tracks in queue.
    
    This ensures smooth transitions between songs by downloading
    the next track before the current one finishes.
    """

    def __init__(self):
        """Initialize the preload manager."""
        self._tasks: Dict[int, asyncio.Task] = {}
        self._preloaded: Dict[int, str] = {}  # chat_id -> media_id

    async def preload_next(self, chat_id: int, media) -> None:
        """
        Start preloading the next track for a chat.
        
        Args:
            chat_id: The chat ID to preload for
            media: The Media/Track object to preload
        """
        # Cancel any existing preload task for this chat
        await self.cancel_preload(chat_id)

        # Check if already preloaded
        if self._preloaded.get(chat_id) == media.id:
            logger.debug(f"Track {media.id} already preloaded for chat {chat_id}")
            return

        # Start new preload task
        task = asyncio.create_task(self._preload_task(chat_id, media))
        self._tasks[chat_id] = task

    async def _preload_task(self, chat_id: int, media) -> None:
        """
        Background task to preload a track.
        
        Args:
            chat_id: The chat ID to preload for
            media: The Media/Track object to preload
        """
        try:
            # Import here to avoid circular dependency
            from HasiiMusic import yt

            logger.debug(f"Starting preload for chat {chat_id}: {media.title}")
            
            # Download the track
            if not media.file_path:
                media.file_path = await yt.download(media.id)
                self._preloaded[chat_id] = media.id
                logger.debug(f"Preload complete for chat {chat_id}: {media.title}")
            else:
                logger.debug(f"Track already has file_path for chat {chat_id}: {media.title}")
                self._preloaded[chat_id] = media.id
                
        except asyncio.CancelledError:
            logger.debug(f"Preload cancelled for chat {chat_id}")
            raise
        except Exception as e:
            logger.error(f"Preload error for chat {chat_id}: {e}")
        finally:
            # Clean up task reference
            self._tasks.pop(chat_id, None)

    async def cancel_preload(self, chat_id: int) -> None:
        """
        Cancel any active preload task for a chat.
        
        Args:
            chat_id: The chat ID to cancel preload for
        """
        task = self._tasks.get(chat_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.debug(f"Cancelled preload for chat {chat_id}")
        
        # Clear preloaded cache
        self._preloaded.pop(chat_id, None)
        self._tasks.pop(chat_id, None)

    def is_preloaded(self, chat_id: int, media_id: str) -> bool:
        """
        Check if a specific track is preloaded for a chat.
        
        Args:
            chat_id: The chat ID to check
            media_id: The media ID to check
            
        Returns:
            bool: True if the track is preloaded
        """
        return self._preloaded.get(chat_id) == media_id

    def clear(self, chat_id: int) -> None:
        """
        Clear preload cache for a chat (non-async version).
        
        Args:
            chat_id: The chat ID to clear
        """
        self._preloaded.pop(chat_id, None)
        self._tasks.pop(chat_id, None)

    async def start_preload(self, chat_id: int, count: int = 2) -> None:
        """
        Start preloading multiple upcoming tracks from queue.
        
        Args:
            chat_id: The chat ID to preload for
            count: Number of tracks to preload (default: 2)
        """
        try:
            # Import here to avoid circular dependency
            from HasiiMusic import queue
            
            # Get full queue and preload upcoming tracks (skip first one - that's current)
            all_tracks = queue.get_queue(chat_id)
            if len(all_tracks) > 1:
                # Preload next 'count' tracks
                upcoming = all_tracks[1:min(1 + count, len(all_tracks))]
                for media in upcoming:
                    if not media.file_path:
                        await self.preload_next(chat_id, media)
                        
        except Exception as e:
            logger.debug(f"Error in start_preload for {chat_id}: {e}")

# HasiiMusic/helpers/Inter-Light.ttf
# HasiiMusic/helpers/Raleway-Bold.ttf

# HasiiMusic/locales/en.json
{
  "add_me": "ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ",
  "admin_required": "<blockquote>ʙᴏᴛ ʀᴇǫᴜɪʀᴇꜱ ᴛʜᴇ <b>ɪɴᴠɪᴛᴇ ᴜꜱᴇʀꜱ ᴠɪᴀ ʟɪɴᴋ</b> ᴘᴇʀᴍɪꜱꜱɪᴏɴ ᴛᴏ ᴡᴏʀᴋ.</blockquote>",
  "admin_cache_wait": "<blockquote>ʏᴏᴜ ᴄᴀɴ ᴏɴʟʏ ʀᴇꜰʀᴇꜱʜ ᴛʜᴇ ᴀᴅᴍɪɴ ᴄᴀᴄʜᴇ ᴏɴᴄᴇ ᴇᴠᴇʀʏ 10 ᴍɪɴᴜᴛᴇꜱ.</blockquote>",
  "admin_cache_reloading": "<blockquote>ʀᴇʟᴏᴀᴅɪɴɢ ᴀᴅᴍɪɴ ᴄᴀᴄʜᴇ...</blockquote>",
  "admin_cache_reloaded": "<blockquote>ᴀᴅᴍɪɴ ᴄᴀᴄʜᴇ ʀᴇꜰʀᴇꜱʜᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!</blockquote>",
  "auth_added": "<blockquote>ᴀᴅᴅᴇᴅ {0} ᴛᴏ ᴛʜᴇ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.</blockquote>",
  "auth_removed": "<blockquote>ʀᴇᴍᴏᴠᴇᴅ {0} ꜰʀᴏᴍ ᴛʜᴇ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.</blockquote>",
  "auth_is_admin": "<blockquote>ᴛʜᴇ ᴜꜱᴇʀ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀɴ <b>ᴀᴅᴍɪɴ</b> ᴀɴᴅ ᴄᴀɴ'ᴛ ʙᴇ ᴀᴅᴅᴇᴅ ᴛᴏ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ.</blockquote>",
  "auto_left": "<blockquote>ɪ'ᴍ ʟᴇᴀᴠɪɴɢ ᴛʜᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛ ʙᴇᴄᴀᴜꜱᴇ ɴᴏ ᴏɴᴇ ɪꜱ ʟɪꜱᴛᴇɴɪɴɢ ᴛᴏ ᴍᴇ.</blockquote>",
  "auto_end": "<blockquote>✅ <b>Qᴜᴇᴜᴇ ꜰɪɴɪꜱʜᴇᴅ.</b>\n\nꜱᴛʀᴇᴀᴍ ᴇɴᴅᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.</blockquote>",
  "bl_usage": "<blockquote><b>ᴜꜱᴀɢᴇ:</b>\n\n/{0} [ᴄʜᴀᴛ_ɪᴅ|ᴜꜱᴇʀ_ɪᴅ]</blockquote>",
  "bl_invalid": "<blockquote>ᴏɴʟʏ ᴄʜᴀᴛ ɪᴅꜱ ᴀɴᴅ ᴜꜱᴇʀ ɪᴅꜱ ᴀʀᴇ ꜱᴜᴘᴘᴏʀᴛᴇᴅ.</blockquote>",
  "bl_already": "<blockquote>ᴛʜɪꜱ ᴄʜᴀᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ.</blockquote>",
  "bl_not": "<blockquote>ᴛʜɪꜱ ᴄʜᴀᴛ ɪꜱ ɴᴏᴛ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ.</blockquote>",
  "bl_added": "<blockquote>ᴛʜɪꜱ ᴄʜᴀᴛ ʜᴀꜱ ʙᴇᴇɴ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ.</blockquote>",
  "bl_removed": "<blockquote>ᴛʜɪꜱ ᴄʜᴀᴛ ʜᴀꜱ ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ᴛʜᴇ ʙʟᴀᴄᴋʟɪꜱᴛ.</blockquote>",
  "bl_user_notify": "<blockquote>ʏᴏᴜ'ᴠᴇ ʙᴇᴇɴ ʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ ꜰʀᴏᴍ ᴜꜱɪɴɢ ᴛʜɪꜱ ʙᴏᴛ. ᴘʟᴇᴀꜱᴇ ʀᴇᴀᴄʜ ᴏᴜᴛ ɪɴ ᴛʜᴇ <a href={0}>ꜱᴜᴘᴘᴏʀᴛ ᴄʜᴀᴛ</a> ꜰᴏʀ ᴍᴏʀᴇ ɪɴꜰᴏ.</blockquote>",
  "dl_cancel": "<blockquote>ᴅᴏᴡɴʟᴏᴀᴅ ᴄᴀɴᴄᴇʟᴇᴅ ʙʏ {0}</blockquote>",
  "dl_complete": "<blockquote>ᴅᴏᴡɴʟᴏᴀᴅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ. ᴘʀᴏᴄᴇꜱꜱɪɴɢ ꜰɪʟᴇ...\n\nᴛɪᴍᴇ ᴇʟᴀᴘꜱᴇᴅ: {0}ꜱ</blockquote>",
  "dl_cancelling": "<blockquote>ʜᴀɴɢ ᴏɴ, ᴄᴀɴᴄᴇʟʟɪɴɢ ᴛʜᴇ ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ!</blockquote>",
  "dl_active": "<blockquote>ᴀʟʀᴇᴀᴅʏ ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ᴛʜᴀᴛ ꜰɪʟᴇ. ᴍɪɴᴅ ᴡᴀɪᴛɪɴɢ?</blockquote>",
  "dl_limit": "<blockquote>ꜰᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴄᴇꜱꜱ ᴛʜᴇ ꜰɪʟᴇ. ᴛʜᴇ ᴍᴀxɪᴍᴜᴍ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪᴍɪᴛ ɪꜱ <b>200 ᴍʙ</b>.</blockquote>",
  "dl_progress": "<blockquote><u><b>ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ꜰɪʟᴇ</b></u>\n\n<b>ᴘʀᴏɢʀᴇꜱꜱ:</b> {0} / {1} [{2:.1f}%]\n<b>ꜱᴘᴇᴇᴅ:</b> {3}/𝘀 | <b>ᴇᴛᴀ:</b> {4}</blockquote>",
  "dl_not_found": "<blockquote>ᴅᴏᴡɴʟᴏᴀᴅ ᴀʟʀᴇᴀᴅʏ ᴄᴀɴᴄᴇʟʟᴇᴅ.</blockquote>",
  "error_no_audio": "<blockquote>ᴍᴏᴠɪɴɢ ᴛᴏ ᴛʜᴇ ɴᴇxᴛ ᴛʀᴀᴄᴋ...\n\nᴀᴜᴅɪᴏ ꜱᴏᴜʀᴄᴇ ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ᴛʜᴇ ꜰɪʟᴇ.</blockquote>",
  "error_no_video": "<blockquote>ᴍᴏᴠɪɴɢ ᴛᴏ ᴛʜᴇ ɴᴇxᴛ ᴛʀᴀᴄᴋ...\n\nɴᴏ ᴠɪᴅᴇᴏ ꜱᴛʀᴇᴀᴍ ꜰᴏᴜɴᴅ ɪɴ ᴛʜᴇ ꜰɪʟᴇ. ᴛʜᴇ ꜰɪʟᴇ ᴍɪɢʜᴛ ʙᴇ ᴄᴏʀʀᴜᴘᴛᴇᴅ ᴏʀ ᴜɴꜱᴜᴘᴘᴏʀᴛᴇᴅ ꜰᴏʀᴍᴀᴛ.</blockquote>",
  "error_no_call": "<blockquote><b>ɴᴏ ᴀᴄᴛɪᴠᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛ ꜰᴏᴜɴᴅ.</b>\n\nᴘʟᴇᴀꜱᴇ ꜱᴛᴀʀᴛ ᴏɴᴇ ᴀɴᴅ <b>ᴛʀʏ ᴀɢᴀɪɴ</b>.</blockquote>",
  "error_vc_disabled": "<blockquote>❌ <b>ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ɪꜱ ᴅɪꜱᴀʙʟᴇᴅ ᴏʀ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ</b>\n\nᴘʟᴇᴀꜱᴇ ᴇɴᴀʙʟᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ɪɴ ᴛʜɪꜱ ɢʀᴏᴜᴘ ᴏʀ ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ᴀꜱꜱɪꜱᴛᴀɴᴛ ʜᴀꜱ <b>ᴀᴅᴍɪɴ ᴘᴇʀᴍɪꜱꜱɪᴏɴꜱ</b> ᴛᴏ ꜱᴛᴀʀᴛ ᴠᴏɪᴄᴇ ᴄʜᴀᴛꜱ.</blockquote>",
  "error_no_file": "<blockquote>ᴅᴏᴡɴʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ.\n\nɪꜰ ᴛʜᴇ ɪꜱꜱᴜᴇ ᴘᴇʀꜱɪꜱᴛꜱ, ʀᴇᴘᴏʀᴛ ɪᴛ ᴛᴏ ᴛʜᴇ <a href={0}>ꜱᴜᴘᴘᴏʀᴛ ᴄʜᴀᴛ</a></blockquote>",
  "error_tg_server": "<blockquote><u><b>ᴛᴇʟᴇɢʀᴀᴍ ꜱᴇʀᴠᴇʀ ᴇʀʀᴏʀ</b></u>\n\nᴛᴇʟᴇɢʀᴀᴍ ɪꜱ ᴇxᴘᴇʀɪᴇɴᴄɪɴɢ ɪɴᴛᴇʀɴᴀʟ ɪꜱꜱᴜᴇꜱ. ᴄʟᴇᴀʀɪɴɢ ᴛʜᴇ ǫᴜᴇᴜᴇ ᴀɴᴅ ʟᴇᴀᴠɪɴɢ ᴛʜᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛ.</blockquote>",
  "eval_error": "<blockquote>⚠️ ᴇʀʀᴏʀ ᴇxᴇᴄᴜᴛɪɴɢ ꜱɴɪᴘᴘᴇᴛ\n\n</blockquote>",
  "eval_inp": "<blockquote>ᴡʜᴀᴛ?</blockquote>",
  "eval_out": "<blockquote><b>ᴏᴜᴛᴘᴜᴛ:</b>\n<code>{0}</code></blockquote>",
  "gcast_usage": "<blockquote>ᴜꜱᴀɢᴇ: /broadcast [ʀᴇᴘʟʏ ᴛᴏ ᴍᴇꜱꜱᴀɢᴇ]\n\nᴇxᴀᴍᴘʟᴇ: /broadcast (ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇꜱꜱᴀɢᴇ)\n\nᴏᴘᴛɪᴏɴꜱ:\n-ᴜꜱᴇʀ : ᴀʟꜱᴏ ꜱᴇɴᴅ ᴛᴏ ᴜꜱᴇʀꜱ\n-ɴᴏᴄʜᴀᴛ : ᴅᴏɴ'ᴛ ꜱᴇɴᴅ ᴛᴏ ɢʀᴏᴜᴘꜱ\n-ᴄᴏᴘʏ : ꜱᴇɴᴅ ᴀꜱ ᴄᴏᴘʏ (ɴᴏ ꜰᴏʀᴡᴀʀᴅ ᴛᴀɢ)</blockquote>",
  "gcast_active": "<blockquote>ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ ꜰᴏʀ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ʙʀᴏᴀᴅᴄᴀꜱᴛ ᴛᴏ ꜰɪɴɪꜱʜ.</blockquote>",
  "gcast_inactive": "<blockquote>ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ɪꜱ ᴄᴜʀʀᴇɴᴛʟʏ ɪɴᴀᴄᴛɪᴠᴇ.</blockquote>",
  "gcast_start": "<blockquote>ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ꜱᴛᴀʀᴛᴇᴅ.</blockquote>",
  "gcast_stop": "<blockquote>ʙʀᴏᴀᴅᴄᴀꜱᴛ ꜱᴛᴏᴘᴘᴇᴅ.</blockquote>",
  "gcast_stopped": "<blockquote>ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ꜱᴛᴏᴘᴘᴇᴅ. (ᴄʜᴇᴄᴋ ʟᴏɢ ɢʀᴏᴜᴘ)\n\nꜱᴇɴᴛ ᴛᴏ {0} ɢʀᴏᴜᴘꜱ ᴀɴᴅ {1} ᴜꜱᴇʀꜱ.</blockquote>",
  "gcast_log": "<blockquote><u><b>ʙʀᴏᴀᴅᴄᴀꜱᴛ ʟᴏɢ</b></u>\n\n<b>ᴜꜱᴇʀ:</b> <code>{0}</code> | {1}\n<b>ᴄᴏᴍᴍᴀɴᴅ:</b> <code>{2}</code>\n\nᴜꜱᴇ /stop_gcast ᴛᴏ ꜱᴛᴏᴘ ᴛʜᴇ ʙʀᴏᴀᴅᴄᴀꜱᴛ.</blockquote>",
  "gcast_stop_log": "<blockquote>ʙʀᴏᴀᴅᴄᴀꜱᴛ ꜱᴛᴏᴘᴘᴇᴅ ʙʏ <code>{0}</code> | {1}</blockquote>",
  "gcast_end": "<blockquote>ʙʀᴏᴀᴅᴄᴀꜱᴛᴇᴅ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ {0} ɢʀᴏᴜᴘꜱ ᴀɴᴅ {1} ᴜꜱᴇʀꜱ.</blockquote>",
  "help_menu": "<b>ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴꜱ ʙᴇʟᴏᴡ ᴛᴏ ɢᴇᴛ ɪɴꜰᴏʀᴍᴀᴛɪᴏɴ ᴀʙᴏᴜᴛ ᴍʏ ᴄᴏᴍᴍᴀɴᴅꜱ.</b>\n<blockquote><i><b>ɴᴏᴛᴇ:</b> ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅꜱ ᴄᴀɴ ʙᴇ ᴜꜱᴇᴅ ᴡɪᴛʜ /</i></blockquote>",
  "help_btn_admins": "ᴀᴅᴍɪɴꜱ",
  "help_btn_auth": "ᴀᴜᴛʜ",
  "help_btn_blist": "ʙʟᴀᴄᴋʟɪꜱᴛ",
  "help_btn_games": "🏆 ɢᴀᴍᴇꜱ - ᴛᴏᴜʀɴᴀᴍᴇɴᴛ",
  "help_btn_lang": "ʟᴀɴɢᴜᴀɢᴇ",
  "help_btn_ping": "ᴘɪɴɢ",
  "help_btn_play": "ᴘʟᴀʏ",
  "help_btn_queue": "ǫᴜᴇᴜᴇ",
  "help_btn_stats": "ꜱᴛᴀᴛꜱ",
  "help_btn_sudo": "ꜱᴜᴅᴏ",
  "help_admins": "<u><b>ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\n\n<blockquote>/pause: ᴘᴀᴜꜱᴇ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ.\n/resume: ʀᴇꜱᴜᴍᴇ ᴛʜᴇ ᴘᴀᴜꜱᴇᴅ ꜱᴛʀᴇᴀᴍ.\n/skip: ꜱᴋɪᴘ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ꜱᴛʀᴇᴀᴍ.\n/stop: ꜱᴛᴏᴘ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ.\n\n</blockquote><blockquote>/seek [ᴅᴜʀᴀᴛɪᴏɴ ɪɴ ꜱᴇᴄᴏɴᴅꜱ/ᴍᴍ:ꜱꜱ]: ꜱᴇᴇᴋ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ.\n/seekback [ᴅᴜʀᴀᴛɪᴏɴ]: ꜱᴇᴇᴋ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ ʙᴀᴄᴋᴡᴀʀᴅ.\n/seekforward [ᴅᴜʀᴀᴛɪᴏɴ]: ꜱᴇᴇᴋ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ ꜰᴏʀᴡᴀʀᴅ.\n\n</blockquote><blockquote>/loop [ᴏꜰꜰ/ꜱɪɴɢʟᴇ/ǫᴜᴇᴜᴇ]: ᴇɴᴀʙʟᴇ ʟᴏᴏᴘ ᴍᴏᴅᴇ (ᴏꜰꜰ/ꜱɪɴɢʟᴇ ᴛʀᴀᴄᴋ/ᴇɴᴛɪʀᴇ ǫᴜᴇᴜᴇ).\n/shuffle: ꜱʜᴜꜰꜰʟᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ǫᴜᴇᴜᴇ.\n\n</blockquote><blockquote>/channelplay: ᴄᴏɴꜰɪɢᴜʀᴇ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ᴍᴏᴅᴇ.\n/reload: ʀᴇʟᴏᴀᴅꜱ ᴛʜᴇ ᴀᴅᴍɪɴ ᴄᴀᴄʜᴇ.\n/bots: ꜱʜᴏᴡ ᴀʟʟ ʙᴏᴛꜱ ɪɴ ᴛʜᴇ ɢʀᴏᴜᴘ.\n/groupdata: ꜱʜᴏᴡ ᴄᴏᴍᴘʀᴇʜᴇɴꜱɪᴠᴇ ɢʀᴏᴜᴘ ɪɴꜰᴏʀᴍᴀᴛɪᴏɴ (ɪᴅ, ᴍᴇᴍʙᴇʀꜱ, ᴀᴅᴍɪɴꜱ, ʙᴏᴛꜱ, ᴇᴛᴄ).</blockquote>",
  "help_auth": "<u><b>ᴀᴜᴛʜ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\nᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴄᴏɴᴛʀᴏʟ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ ᴡɪᴛʜᴏᴜᴛ ʙᴇɪɴɢ ᴀɴ ᴀᴅᴍɪɴ.\n<blockquote>/auth: ᴀᴅᴅ ᴀ ᴜꜱᴇʀ ᴛᴏ ᴛʜᴇ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.\n/unauth: ʀᴇᴍᴏᴠᴇ ᴀ ᴜꜱᴇʀ ꜰʀᴏᴍ ᴛʜᴇ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.</blockquote>",
  "help_blist": "<u><b>ʙʟᴀᴄᴋʟɪꜱᴛ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\nʙʟᴀᴄᴋʟɪꜱᴛᴇᴅ ᴄʜᴀᴛꜱ ᴀɴᴅ ᴜꜱᴇʀꜱ ᴄᴀɴɴᴏᴛ ᴜꜱᴇ ᴛʜᴇ ʙᴏᴛ.\n<blockquote>/blacklist [ᴄʜᴀᴛ_ɪᴅ|ᴜꜱᴇʀ_ɪᴅ]: ᴀᴅᴅ ᴀ ᴄʜᴀᴛ/𝘂𝘀𝗲𝗿 ᴛᴏ ᴛʜᴇ ʙʟᴀᴄᴋʟɪꜱᴛ.\n/unblacklist [ᴄʜᴀᴛ_ɪᴅ|ᴜꜱᴇʀ_ɪᴅ]: ʀᴇᴍᴏᴠᴇ ᴀ ᴄʜᴀᴛ/𝘂𝘀𝗲𝗿 ꜰʀᴏᴍ ᴛʜᴇ ʙʟᴀᴄᴋʟɪꜱᴛ</blockquote>",
  "help_lang": "<u><b>ʟᴀɴɢᴜᴀɢᴇ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\nᴛʜɪꜱ ʙᴏᴛ ꜱᴜᴘᴘᴏʀᴛꜱ ᴍᴜʟᴛɪᴘʟᴇ ʟᴀɴɢᴜᴀɢᴇꜱ.\n<blockquote>/lang: ᴄʜᴀɴɢᴇ ᴛʜᴇ ʟᴀɴɢᴜᴀɢᴇ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.</blockquote>",
  "help_ping": "<u><b>ᴘɪɴɢ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u><blockquote>/help: ꜱʜᴏᴡꜱ ᴛʜᴇ ʜᴇʟᴘ ᴍᴇɴᴜ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.\n/ping: ᴄʜᴇᴄᴋ ᴛʜᴇ ᴘɪɴɢ ᴀɴᴅ ᴍᴇᴍᴏʀʏ ᴜꜱᴀɢᴇ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.\n/start: ꜱᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ.\n/sudolist: ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ᴏꜰ ᴛʜᴇ ʙᴏᴛ.</blockquote>",
  "help_play": "<u><b>ᴘʟᴀʏ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\nʏᴏᴜ ᴄᴀɴ ᴘʟᴀʏ ᴍᴜꜱɪᴄ ɪɴ ᴛʜᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴜꜱɪɴɢ ᴛʜᴇ ꜰᴏʟʟᴏᴡɪɴɢ ᴄᴏᴍᴍᴀɴᴅꜱ.\n<blockquote>/play [ꜱᴏɴɢ ɴᴀᴍᴇ/ʏᴛ ᴜʀʟ/ʀᴇᴘʟʏ ᴛᴏ ᴀᴜᴅɪᴏ]: ᴘʟᴀʏ ᴀᴜᴅɪᴏ ɪɴ ᴛʜᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ.\n/cplay [ꜱᴏɴɢ/ᴜʀʟ]: ᴘʟᴀʏ ᴀᴜᴅɪᴏ ɪɴ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ.\n/playforce: ꜰᴏʀᴄᴇ ᴘʟᴀʏ ᴀᴜᴅɪᴏ (ꜱᴋɪᴘ ǫᴜᴇᴜᴇ).\n/radio: ᴘʟᴀʏ ᴏɴʟɪɴᴇ ʀᴀᴅɪᴏ ꜱᴛᴀᴛɪᴏɴꜱ.\n\n<b>ɪɴʟɪɴᴇ ᴄᴏɴᴛʀᴏʟꜱ:</b>\n• « 10 / « 30 - ꜱᴇᴇᴋ ʙᴀᴄᴋᴡᴀʀᴅ 10/30 ꜱᴇᴄᴏɴᴅꜱ\n• 30 » / 10 » - ꜱᴇᴇᴋ ꜰᴏʀᴡᴀʀᴅ 30/10 ꜱᴇᴄᴏɴᴅꜱ\n• ▷ II - ᴘʟᴀʏ/ᴘᴀᴜꜱᴇ ᴄᴏɴᴛʀᴏʟꜱ\n• ↻ - ʀᴇᴘʟᴀʏ ᴄᴜʀʀᴇɴᴛ ᴛʀᴀᴄᴋ\n• ‣‣I - ꜱᴋɪᴘ ᴛᴏ ɴᴇxᴛ ᴛʀᴀᴄᴋ\n• ▢ - ꜱᴛᴏᴘ ᴘʟᴀʏʙᴀᴄᴋ\n• ᴅᴇʟᴇᴛᴇ - ʀᴇᴍᴏᴠᴇ ᴘʟᴀʏᴇʀ ᴍᴇꜱꜱᴀɢᴇ\n\n<b>ʟᴏᴏᴘ & ꜱʜᴜꜰꜰʟᴇ ᴄᴏᴍᴍᴀɴᴅꜱ:</b>\n/loop [ᴏꜰꜰ/ꜱɪɴɢʟᴇ/ǫᴜᴇᴜᴇ] - ᴛᴏɢɢʟᴇ ʟᴏᴏᴘ ᴍᴏᴅᴇ\n/shuffle - ꜱʜᴜꜰꜰʟᴇ ᴛʜᴇ ǫᴜᴇᴜᴇ\n\n<b>ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ꜱᴇᴛᴜᴘ:</b>\n/channelplay ʟɪɴᴋᴇᴅ - ᴇɴᴀʙʟᴇ ꜰᴏʀ ɢʀᴏᴜᴘ'ꜱ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ\n/channelplay [ᴄʜᴀɴɴᴇʟ_ɪᴅ] - ᴇɴᴀʙʟᴇ ꜰᴏʀ ꜱᴘᴇᴄɪꜰɪᴄ ᴄʜᴀɴɴᴇʟ\n/channelplay ᴅɪꜱᴀʙʟᴇ - ᴅɪꜱᴀʙʟᴇ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ\n\n<b>ʟɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ:</b> ꜱᴜᴘᴘᴏʀᴛꜱ ʏᴏᴜᴛᴜʙᴇ ʟɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ\n\n<b>ᴇxᴀᴍᴘʟᴇ:</b> <code>/play -ꜰ ʟɪᴠᴇ_ꜱᴛʀᴇᴀᴍ_ɴᴀᴍᴇ</code></blockquote>",
  "help_queue": "<u><b>ǫᴜᴇᴜᴇ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\n<blockquote>/queue: ꜱʜᴏᴡꜱ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛʟʏ ǫᴜᴇᴜᴇᴅ ᴛʀᴀᴄᴋꜱ ɪɴ ᴛʜᴇ ǫᴜᴇᴜᴇ.\n/shuffle: ꜱʜᴜꜰꜰʟᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ǫᴜᴇᴜᴇ ʀᴀɴᴅᴏᴍʟʏ.\n/loop: ᴇɴᴀʙʟᴇ/ᴅɪꜱᴀʙʟᴇ ʟᴏᴏᴘ ᴍᴏᴅᴇ ꜰᴏʀ ǫᴜᴇᴜᴇ ᴏʀ ꜱɪɴɢʟᴇ ᴛʀᴀᴄᴋ.</blockquote>",
  "help_stats": "<blockquote><u><b>ꜱᴛᴀᴛꜱ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\n\n/stats: ꜱʜᴏᴡꜱ ᴛʜᴇ ʙᴏᴛ'ꜱ ꜱᴛᴀᴛꜱ.</blockquote>",
  "help_sudo": "<blockquote><b><u>ꜱᴜᴅᴏ ᴄᴏᴍᴍᴀɴᴅꜱ:</b></u>\n\n/ac: ꜱʜᴏᴡꜱ ᴛʜᴇ ᴀᴄᴛɪᴠᴇ ᴄᴀʟʟꜱ ᴄᴏᴜɴᴛ.\n\n/activevc: ꜱʜᴏᴡꜱ ᴛʜᴇ ʟɪꜱᴛ ᴏꜰ ᴛʜᴇ ᴀᴄᴛɪᴠᴇ ᴄᴀʟʟꜱ.\n\n/broadcast [ʀᴇᴘʟʏ ᴛᴏ ᴍᴇꜱꜱᴀɢᴇ]: ʙʀᴏᴀᴅᴄᴀꜱᴛꜱ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ᴀʟʟ ᴛʜᴇ ᴄʜᴀᴛꜱ.\n  • -ɴᴏᴄʜᴀᴛ: ᴇxᴄʟᴜᴅᴇꜱ ɢʀᴏᴜᴘꜱ ꜰʀᴏᴍ ᴛʜᴇ ʙʀᴏᴀᴅᴄᴀꜱᴛ.\n  • -ᴜꜱᴇʀ: ɪɴᴄʟᴜᴅᴇ ᴜꜱᴇʀꜱ ɪɴ ᴛʜᴇ ʙʀᴏᴀᴅᴄᴀꜱᴛ.\n  • -ᴄᴏᴘʏ: ʀᴇᴍᴏᴠᴇꜱ ꜰᴏʀᴡᴀʀᴅᴇᴅ ᴛᴀɢ.\n<b>ᴇxᴀᴍᴘʟᴇ:</b> <code>/broadcast -ᴜꜱᴇʀ -ᴄᴏᴘʏ</code>\n\n/eval: ᴇxᴇᴄᴜᴛᴇꜱ ᴛʜᴇ ɢɪᴠᴇɴ ᴄᴏᴅᴇ.\n\n/leave: ᴍᴀᴋᴇ ʙᴏᴛ ᴀɴᴅ ᴀꜱꜱɪꜱᴛᴀɴᴛ ʟᴇᴀᴠᴇ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴄʜᴀᴛ.\n\n/logs: ꜱᴇɴᴅꜱ ᴛʜᴇ ʟᴏɢ ꜰɪʟᴇ.\n\n/logger [ᴏɴ|ᴏꜰꜰ]: ᴇɴᴀʙʟᴇꜱ/𝗱𝗶𝘀𝗮𝗯𝗹𝗲𝘀 ᴛʜᴇ ʟᴏɢɢᴇʀ.\n\n/restart: ʀᴇꜱᴛᴀʀᴛꜱ ᴛʜᴇ ʙᴏᴛ.\n\n/addsudo: ᴀᴅᴅ ᴀ ᴜꜱᴇʀ ᴛᴏ ᴛʜᴇ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.\n/rmsudo: ʀᴇᴍᴏᴠᴇ ᴀ ᴜꜱᴇʀ ꜰʀᴏᴍ ᴛʜᴇ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.</blockquote>",
  "help_games": "🏆 <u><b>ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ᴀʀᴇɴᴀ - ɢᴀᴍᴇ ᴄᴏᴍᴍᴀɴᴅꜱ</b></u>\n\n<b>🎮 ɢᴀᴍᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ:</b>\n<blockquote>🎲 ᴅɪᴄᴇ ʀᴏʟʟ (1-6)\n🎯 ᴅᴀʀᴛꜱ (1-6, ʙᴜʟʟꜱᴇʏᴇ = 6)\n🏀 ʙᴀꜱᴋᴇᴛʙᴀʟʟ (1-5, 4 & 5 = ʙᴀꜱᴋᴇᴛꜱ)\n⚽ ꜰᴏᴏᴛʙᴀʟʟ/ꜱᴏᴄᴄᴇʀ (1-5, 4 & 5 = ɢᴏᴀʟꜱ)\n🎳 ʙᴏᴡʟɪɴɢ (1-6, 6 = ꜱᴛʀɪᴋᴇ)\n🎰 ꜱʟᴏᴛ ᴍᴀᴄʜɪɴᴇ (1-64, ꜱᴘᴇᴄɪꜰɪᴄ ᴄᴏᴍʙᴏꜱ)</blockquote>\n\n<b>👑 ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅꜱ:</b>\n<blockquote>/tournamentstart ᴏʀ /gamestart - <i>ꜱᴛᴀʀᴛ ᴀ ɴᴇᴡ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ</i>\n/tournamentbegin ᴏʀ /gamebegin - <i>ʙᴇɢɪɴ ᴛʜᴇ ɢᴀᴍᴇ</i>\n/tournamentstop ᴏʀ /gamestop - <i>ᴇɴᴅ ᴄᴜʀʀᴇɴᴛ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ</i>\n/tournamentcancel ᴏʀ /gamecancel - <i>ᴄᴀɴᴄᴇʟ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ</i>\n/score ᴏʀ /scores ᴏʀ /standings - <i>ᴠɪᴇᴡ ꜱᴄᴏʀᴇꜱ</i>\n/leaderboard ᴏʀ /rankings - <i>ᴠɪᴇᴡ ᴀʟʟ-ᴛɪᴍᴇ ʀᴀɴᴋɪɴɢꜱ</i></blockquote>\n\n<b>👤 ᴘʟᴀʏᴇʀ ᴄᴏᴍᴍᴀɴᴅꜱ:</b>\n<blockquote>/join ᴏʀ /jointeam ᴏʀ /register - <i>ᴊᴏɪɴ ᴛʜᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ</i>\n/leave ᴏʀ /leaveteam ᴏʀ /quit - <i>ʟᴇᴀᴠᴇ ᴛʜᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ</i>\n/myteam ᴏʀ /participants - <i>ᴠɪᴇᴡ ᴀʟʟ ᴘᴀʀᴛɪᴄɪᴘᴀɴᴛꜱ</i>\n/mystats ᴏʀ /profile ᴏʀ /tournamentstats - <i>ᴠɪᴇᴡ ʏᴏᴜʀ ꜱᴛᴀᴛꜱ</i>\n/tournamentinfo ᴏʀ /gameinfo - <i>ɢᴇᴛ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ɪɴꜰᴏ</i></blockquote>\n\n<b>🎮 ʜᴏᴡ ᴛᴏ ᴘʟᴀʏ:</b>\n<blockquote>• ᴀᴅᴍɪɴ ꜱᴛᴀʀᴛꜱ ᴡɪᴛʜ /tournamentstart\n• ᴘʟᴀʏᴇʀꜱ ᴊᴏɪɴ ᴡɪᴛʜ /join\n• ᴀᴅᴍɪɴ ʙᴇɢɪɴꜱ ɢᴀᴍᴇ ᴡɪᴛʜ /tournamentbegin\n• ᴇɴᴅ ᴇᴍᴏᴊɪ (🎲 🎯 🏀 ⚽ 🎳 🎰) & ʙᴏᴛ ʀᴇᴘʟɪᴇꜱ ᴡɪᴛʜ ʏᴏᴜʀ ꜱᴄᴏʀᴇ\n• ᴛᴀᴋᴇ ᴛᴜʀɴꜱ ᴘʟᴀʏɪɴɢ & ᴇᴀʀɴ ᴘᴏɪɴᴛꜱ!</blockquote>\n\n<i>💡 ᴊᴜꜱᴛ ꜱᴇɴᴅ ᴀɴ ᴇᴍᴏᴊɪ & ᴛʜᴇ ʙᴏᴛ ɪɴꜱᴛᴀɴᴛʟʏ ʀᴇᴘʟɪᴇꜱ ᴡɪᴛʜ ʏᴏᴜʀ ꜱᴄᴏʀᴇ ᴀɴᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴄᴏᴜɴᴛꜱ ɪᴛ ᴛᴏᴡᴀʀᴅ ᴛʜᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ.</i>",
  "lang_choose": "<blockquote>ᴘʟᴇᴀꜱᴇ ᴄʜᴏᴏꜱᴇ ᴛʜᴇ ʟᴀɴɢᴜᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ꜱᴇᴛ ꜰᴏʀ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴄʜᴀᴛ:</blockquote>",
  "lang_change": "ᴄʜᴀɴɢɪɴɢ ᴛʜᴇ ʟᴀɴɢᴜᴀɢᴇ ᴏꜰ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴄʜᴀᴛ ᴛᴏ: {0}",
  "lang_changed": "<blockquote>ᴛʜᴇ ʟᴀɴɢᴜᴀɢᴇ ᴏꜰ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴄʜᴀᴛ ʜᴀꜱ ʙᴇᴇɴ ᴄʜᴀɴɢᴇᴅ ᴛᴏ: <i>{0}</i></blockquote>",
  "lang_same": "<blockquote>ᴛʜᴇ ʟᴀɴɢᴜᴀɢᴇ ᴏꜰ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴄʜᴀᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ꜱᴇᴛ ᴛᴏ: {0}</blockquote>",
  "log_fetch": "<blockquote>ꜰᴇᴛᴄʜɪɴɢ ʟᴏɢꜱ...</blockquote>",
  "log_not_found": "<blockquote>ʟᴏɢ ꜰɪʟᴇ ᴅᴏᴇꜱɴ'ᴛ ᴇxɪꜱᴛ.</blockquote>",
  "log_sent": "<blockquote>ʟᴏɢ ꜰɪʟᴇ ᴏꜰ {0}</blockquote>",
  "log_chat": "<blockquote><u><b>ɴᴇᴡ ᴄʜᴀᴛ ʟᴏɢ</b></u>\n\n<b>ᴄʜᴀᴛ:</b> <code>{0}</code> | {1}\n<b>ᴜꜱᴇʀ:</b> <code>{2}</code> | {3}</blockquote>",
  "log_user": "<blockquote><u><b>ɴᴇᴡ ᴜꜱᴇʀ ʟᴏɢ</b></u>\n\n<b>ɪᴅ:</b> <code>{0}</code>\n<b>ɴᴀᴍᴇ:</b> {1} | {2}</blockquote>",
  "logger_on": "<blockquote>ʟᴏɢɢᴇʀ ᴇɴᴀʙʟᴇᴅ.</blockquote>",
  "logger_off": "<blockquote>ʟᴏɢɢᴇʀ ᴅɪꜱᴀʙʟᴇᴅ.</blockquote>",
  "logger_usage": "<blockquote><b>ᴜꜱᴀɢᴇ:</b>\n\n/{0} [ᴏɴ|ᴏꜰꜰ]</blockquote>",
  "not_playing": "ᴛʜᴇ ʙᴏᴛ ɪꜱɴ'ᴛ ꜱᴛʀᴇᴀᴍɪɴɢ ɪɴ ᴛʜᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛ.",
  "pinging": "<blockquote>ᴘɪɴɢɪɴɢ… ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ…</blockquote>",
  "ping_pong": "<blockquote><u><b>ᴘᴏɴɢ!</b></u>\n\n<b>ʟᴀᴛᴇɴᴄʏ:</b> <code>{0}ᴍꜱ</code>\n\n<b>ᴜᴘᴛɪᴍᴇ:</b> {1}\n<b>ᴘʏᴛɢᴄᴀʟʟꜱ ʟᴀᴛᴇɴᴄʏ:</b> <code>{2}ᴍꜱ</code></blockquote>",
  "play_admin": "<blockquote><u><b>ᴀᴅᴍɪɴ ᴏɴʟʏ ᴘʟᴀʏ</b></u>\n\nᴏɴʟʏ ᴀᴅᴍɪɴꜱ ᴀʀᴇ ᴀʟʟᴏᴡᴇᴅ ᴛᴏ ᴘʟᴀʏ ɪɴ ᴛʜɪꜱ ᴄʜᴀᴛ.</blockquote>",
  "play_banned": "<blockquote><u><b>{0} ᴀꜱꜱɪꜱᴛᴀɴᴛ ɪꜱ ʙᴀɴɴᴇᴅ ʏᴏᴜʀ ᴄʜᴀᴛ</b></u>\n\n<b>ɪᴅ:</b> <code>{1}</code>\n<b>ɴᴀᴍᴇ:</b> {2}\n<b>ᴜꜱᴇʀɴᴀᴍᴇ:</b> {3}</blockquote>",
  "play_media": "<blockquote>🔴 <b>ᴛʜᴇ ʀᴇǫᴜᴇꜱᴛᴇᴅ ꜱᴛʀᴇᴀᴍ ꜱᴛᴀʀᴛᴇᴅ</b></blockquote>\n<blockquote>➤ <b>ᴛɪᴛʟᴇ :</b> <a href={0}>{1}</a>\n➤ <b>ᴅᴜʀᴀᴛɪᴏɴ :</b> {2} ᴍɪɴᴜᴛᴇꜱ\n➤ <b>ʀᴇǫᴜᴇꜱᴛᴇᴅ ʙʏ :</b> {3}</blockquote>",
  "play_log": "<blockquote><u>{0} ᴘʟᴀʏ ʟᴏɢ</u>\n\n<b>ᴄʜᴀᴛ:</b> <code>{1}</code> | {2}\n<b>ᴜꜱᴇʀ:</b> <code>{3}</code> | {4}\n<b>ᴍᴇꜱꜱᴀɢᴇ ʟɪɴᴋ:</b> {5}\n\n<b>ᴛɪᴛʟᴇ:</b> {6}\n<b>ᴅᴜʀᴀᴛɪᴏɴ:</b> {7} ᴍɪɴ</blockquote>",
  "play_queued": "<blockquote><u><b>ᴀᴅᴅᴇᴅ ᴛᴏ ǫᴜᴇᴜᴇ: {0} </b></u></blockquote>\n <blockquote><b>ᴛɪᴛʟᴇ:</b> <a href={1}>{2}</a>\n<b>ᴅᴜʀᴀᴛɪᴏɴ:</b> {3} ᴍɪɴ\n<b>ʀᴇǫᴜᴇꜱᴛᴇᴅ ʙʏ:</b> {4}</blockquote>",
  "play_usage": "<blockquote><b>ᴜꜱᴀɢᴇ:</b> <code>/play [ꜱᴏɴɢ ɴᴀᴍᴇ/ʏᴏᴜᴛᴜʙᴇ ᴜʀʟ/ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴀᴜᴅɪᴏ ꜰɪʟᴇ]</code></blockquote>",
  "play_seeking": "<blockquote>ꜱᴇᴇᴋɪɴɢ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ꜱᴛʀᴇᴀᴍ...</blockquote>",
  "play_again": "<blockquote>ʀᴇᴘʟᴀʏɪɴɢ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴍᴇᴅɪᴀ...</blockquote>",
  "play_now": "ᴘʟᴀʏ ɴᴏᴡ",
  "play_next": "<blockquote>ʜᴏʟᴅ ᴏɴ...ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ɴᴇxᴛ ᴍᴇᴅɪᴀ ꜰʀᴏᴍ ᴛʜᴇ ǫᴜᴇᴜᴇ.</blockquote>",
  "play_invite": "<blockquote>ʜᴏʟᴅ ᴏɴ ᴀ ᴍᴏᴍᴇɴᴛ...\nɪɴᴠɪᴛɪɴɢ {0} ᴀꜱꜱɪꜱᴛᴀɴᴛ ᴛᴏ ʏᴏᴜʀ ᴄʜᴀᴛ.</blockquote>",
  "play_emoji": "🪄",
  "play_searching": "{0}",
  "play_paused": "<blockquote><b>ꜱᴛʀᴇᴀᴍ ᴘᴀᴜꜱᴇᴅ ʙʏ</b> {0}</blockquote>",
  "play_resumed": "<blockquote><b>ꜱᴛʀᴇᴀᴍ ʀᴇꜱᴜᴍᴇᴅ ʙʏ</b> {0}</blockquote>",
  "play_replayed": "<blockquote><b>ꜱᴛʀᴇᴀᴍ ʀᴇᴘʟᴀʏᴇᴅ ʙʏ</b> {0}</blockquote>",
  "play_skipped": "<blockquote><b>ꜱᴛʀᴇᴀᴍ ꜱᴋɪᴘᴘᴇᴅ ʙʏ</b> {0}</blockquote>",
  "play_stopped": "<blockquote><b>ꜱᴛʀᴇᴀᴍ ᴇɴᴅᴇᴅ ʙʏ</b> {0}</blockquote>",
  "play_seeked": "<blockquote><b>ꜱᴛʀᴇᴀᴍ ꜱᴋɪᴘᴘᴇᴅ {0} ᴀɴᴅ ꜱᴛᴀʀᴛᴇᴅ ꜰʀᴏᴍ {1} ꜱᴇᴄᴏɴᴅꜱ ʙʏ</b> {2}</blockquote>",
  "play_expired": "<blockquote>ᴛʜɪꜱ ʙᴜᴛᴛᴏɴ ʜᴀꜱ ᴇxᴘɪʀᴇᴅ.</blockquote>",
  "play_live": "<blockquote>🔴 <b>ʟɪᴠᴇ ꜱᴛʀᴇᴀᴍ ᴅᴇᴛᴇᴄᴛᴇᴅ</b>\nꜱᴛᴀʀᴛɪɴɢ ʟɪᴠᴇ ꜱᴛʀᴇᴀᴍ ᴘʟᴀʏʙᴀᴄᴋ...</blockquote>",
  "play_already_paused": "<blockquote>ᴅᴏ ʏᴏᴜ ʀᴇᴍᴇᴍʙᴇʀ ᴛʜᴀᴛ ʏᴏᴜ ʀᴇꜱᴜᴍᴇᴅ ᴛʜᴇ ꜱᴛʀᴇᴀᴍ?</blockquote>",
  "play_not_paused": "<blockquote>ᴅᴏ ʏᴏᴜ ʀᴇᴍᴇᴍʙᴇʀ ᴛʜᴀᴛ ʏᴏᴜ ᴘᴀᴜꜱᴇᴅ ᴛʜᴇ ꜱᴛʀᴇᴀᴍ?</blockquote>",
  "play_seek_usage": "<blockquote><b>ᴜꜱᴀɢᴇ:</b> /{0} ᴅᴜʀᴀᴛɪᴏɴ\n<b>ᴇxᴀᴍᴘʟᴇ:</b> <code>/{0} 15</code></blockquote>",
  "play_seek_no_dur": "<blockquote>ꜰᴀɪʟᴇᴅ ᴛᴏ ꜰᴇᴛᴄʜ ᴛʜᴇ ᴅᴜʀᴀᴛɪᴏɴ ᴏꜰ ᴛʜᴇ ᴏɴɢᴏɪɴɢ ꜱᴛʀᴇᴀᴍ.</blockquote>",
  "play_seek_min": "<blockquote>ᴍɪɴɪᴍᴜᴍ ꜱᴇᴇᴋ ᴛɪᴍᴇ ɪꜱ 10 ꜱᴇᴄᴏɴᴅꜱ — ᴛʀʏ ᴀ ʙɪᴛ ʟᴏɴɢᴇʀ!</blockquote>",
  "play_duration_limit": "<blockquote>ꜱᴛʀᴇᴀᴍꜱ ʟᴏɴɢᴇʀ ᴛʜᴀɴ {0} ᴍɪɴᴜᴛᴇꜱ ᴀʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ᴛᴏ ᴘʟᴀʏ.</blockquote>",
  "play_queue_full": "<blockquote>ᴛʜᴇ ǫᴜᴇᴜᴇ ʟɪᴍɪᴛ ({0}) ʜᴀꜱ ʙᴇᴇɴ ʀᴇᴀᴄʜᴇᴅ. ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ ꜰᴏʀ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛʟʏ ǫᴜᴇᴜᴇᴅ ᴛʀᴀᴄᴋꜱ ᴛᴏ ꜰɪɴɪꜱʜ ᴘʟᴀʏɪɴɢ, ᴛʜᴇɴ ᴛʀʏ ᴀɢᴀɪɴ.</blockquote>",
  "play_invite_error": "<blockquote>ꜰᴀɪʟᴇᴅ ᴛᴏ ɪɴᴠɪᴛᴇ ᴀꜱꜱɪꜱᴛᴀɴᴛ ᴛᴏ ᴛʜᴇ ᴄʜᴀᴛ.\n\nʀᴇᴀꜱᴏɴ: <code>{0}</code></blockquote>",
  "play_not_found": "<blockquote>ꜰᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴄᴇꜱꜱ ᴛʜᴇ ǫᴜᴇʀʏ.\nɪꜰ ᴛʜᴇ ɪꜱꜱᴜᴇ ᴘᴇʀꜱɪꜱᴛꜱ, ʀᴇᴘᴏʀᴛ ɪᴛ ᴛᴏ ᴛʜᴇ <a href={0}>ꜱᴜᴘᴘᴏʀᴛ ᴄʜᴀᴛ</a>.</blockquote>",
  "play_unsupported": "<blockquote>ꜱᴏʀʀʏ, ᴛʜɪꜱ ᴍᴇᴅɪᴀ ᴛʏᴘᴇ ɪꜱ ɴᴏᴛ ꜱᴜᴘᴘᴏʀᴛᴇᴅ.</blockquote>",
  "play_chat_invalid": "<blockquote>ᴛʜɪꜱ ʙᴏᴛ ᴄᴀɴ ᴏɴʟʏ ʙᴇ ᴜꜱᴇᴅ ɪɴ <b>ꜱᴜᴘᴇʀɢʀᴏᴜᴘꜱ</b>.\n\nᴛᴏ ᴄᴏɴᴠᴇʀᴛ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴛᴏ ᴀ ꜱᴜᴘᴇʀɢʀᴏᴜᴘ, ᴍᴀᴋᴇ ᴛʜᴇ ᴄʜᴀᴛ ʜɪꜱᴛᴏʀʏ <b>ᴠɪꜱɪʙʟᴇ</b> ᴏɴᴄᴇ.\n\nʟᴇᴀᴠɪɴɢ ᴛʜɪꜱ ᴄʜᴀᴛ...</blockquote>",
  "play_user_invalid": "<blockquote>🔒 <b>ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ ᴅᴇᴛᴇᴄᴛᴇᴅ</b> ʀᴇᴠᴇʀᴛ ʙᴀᴄᴋ ᴛᴏ ᴜꜱᴇʀ ᴀᴄᴄᴏᴜɴᴛ ᴛᴏ ᴜꜱᴇ ᴍᴇ.</blockquote>",
  "playlist_fetch": "<blockquote>ꜰᴇᴛᴄʜɪɴɢ ᴛʜᴇ ᴘʟᴀʏʟɪꜱᴛ...\nᴘʟᴇᴀꜱᴇ ʜᴏʟᴅ ᴏɴ.</blockquote>",
  "playlist_error": "<blockquote>ꜱᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ ᴡʜɪʟᴇ ꜰᴇᴛᴄʜɪɴɢ ᴛʜᴇ ᴘʟᴀʏʟɪꜱᴛ.</blockquote>",
  "playlist_queued": "<blockquote><u><b>ᴀᴅᴅᴇᴅ {0} ᴛʀᴀᴄᴋꜱ ꜰʀᴏᴍ ᴛʜᴇ ᴘʟᴀʏʟɪꜱᴛ ᴛᴏ ǫᴜᴇᴜᴇ:</b></u>\n\n</blockquote>",
  "queue_curr": "<blockquote><u><b>ᴄᴜʀʀᴇɴᴛʟʏ ᴘʟᴀʏɪɴɢ:</b></u>\n\n<b>ᴛɪᴛʟᴇ:</b> <a href={0}>{1}</a>\n<b>ᴅᴜʀᴀᴛɪᴏɴ:</b> {2}\n<b>ʀᴇǫᴜᴇꜱᴛᴇᴅ ʙʏ:</b> {3}\n\n</blockquote>",
  "queue_item": "<b>{0}. ᴛɪᴛʟᴇ:</b> {1}\n     - {2} ᴍɪɴ\n\n",
  "queue_fetching": "<blockquote>ꜰᴇᴛᴄʜɪɴɢ ǫᴜᴇᴜᴇ...</blockquote>",
  "restarting": "<blockquote>ʀᴇꜱᴛᴀʀᴛɪɴɢ...</blockquote>",
  "restarted": "<blockquote>ʀᴇꜱᴛᴀʀᴛ ɪɴ ᴘʀᴏɢʀᴇꜱꜱ. ᴅᴏɴ'ᴛ ᴡᴏʀʀᴀ, ɪᴛ'ʟʟ ᴏɴʟʏ ᴛᴀᴋᴇ ᴀ ꜰᴇᴡ ꜱᴇᴄᴏɴᴅꜱ… ᴍᴀʏʙᴇ.</blockquote>",
  "start_pm": "<blockquote>ʜᴇʏ {0}, ᴛʜɪꜱ ɪꜱ {1}!</blockquote>\n<blockquote>ʏᴏᴜʀ ᴍᴜꜱɪᴄ ᴘʟᴀʏᴇʀ ʙᴏᴛ ɪꜱ ʀᴇᴀᴅʏ ᴛᴏ ɢᴏ! ᴇɴᴊᴏʏ ǫᴜᴀʟɪᴛʏ ꜱᴛʀᴇᴀᴍɪɴɢ, ᴄʟᴇᴀɴ ᴄᴏᴍᴍᴀɴᴅꜱ, ᴀɴᴅ 24/𝟳 ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ.<br>\n\n• 🎵 ꜱᴛʀᴇᴀᴍ ᴍᴜꜱɪᴄ ꜰʀᴏᴍ ʏᴏᴜᴛᴜʙᴇ ᴏʀ ꜱᴘᴏᴛɪꜰʏ ʟɪɴᴋꜱ<br>\n• 🎧 ꜱᴍᴏᴏᴛʜ ʀᴇᴀʟ-ᴛɪᴍᴇ ᴘʟᴀʏʙᴀᴄᴋ ɪɴ ᴠᴏɪᴄᴇ ᴄʜᴀᴛꜱ<br>\n• 👨‍💼 ᴍᴇɴᴛɪᴏɴ ᴀʟʟ ᴀᴅᴍɪɴꜱ ᴡɪᴛʜ @ᴀᴅᴍɪɴ, .ᴀᴅᴍɪɴ, ᴏʀ /admin<br>\n• 🎮 ᴘʟᴀʏ ꜰᴜɴ ɢᴀᴍᴇꜱ <br><br>\n• ⚡ ꜱɪᴍᴘʟᴇ, ꜰᴀꜱᴛ, ᴀɴᴅ ᴇᴀꜱʏ ᴛᴏ ᴜꜱᴇ<br>\n• 🚫 ɴᴏ ᴀᴅꜱ ᴏʀ ɪɴᴛᴇʀʀᴜᴘᴛɪᴏɴꜱ<br>\n• 🌙 ᴏɴʟɪɴᴇ 24/7 ᴡɪᴛʜ ꜱᴛᴀʙʟᴇ ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ<br>\n\nᴛᴀᴘ ᴛʜᴇ ʜᴇʟᴘ ʙᴜᴛᴛᴏɴ ᴛᴏ ꜱᴇᴇ ᴀʟʟ ꜰᴇᴀᴛᴜʀᴇꜱ.\n</blockquote>",
  "start_gp": "<blockquote>ʜᴇʏ,\nᴛʜɪꜱ ɪꜱ {0}\n\n<u><b>ᴀ ᴍᴜꜱɪᴄ ᴘʟᴀʏᴇʀ ʙᴏᴛ ᴡɪᴛʜ ꜱᴏᴍᴇ ᴀᴡᴇꜱᴏᴍᴇ ᴀɴᴅ ᴜꜱᴇꜰᴜʟ ꜰᴇᴀᴛᴜʀᴇꜱ.</b></u></blockquote>",
  "start_settings": "<blockquote><u><b>{0} ꜱᴇᴛᴛɪɴɢꜱ</b></u>\n\nᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴꜱ ʙᴇʟᴏᴡ ᴛᴏ ᴄʜᴀɴɢᴇ ᴛʜɪꜱ ᴄʜᴀᴛ'ꜱ ᴄᴜʀʀᴇɴᴛ ꜱᴇᴛᴛɪɴɢꜱ.</blockquote>",
  "stats_fetching": "<blockquote>ꜰᴇᴛᴄʜɪɴɢ ꜱᴛᴀᴛꜱ...</blockquote>",
  "stats_sudo": "<blockquote>\n<b>ᴍᴏᴅᴜʟᴇꜱ:</b> {0}\n<b>ᴘʟᴀᴛꜰᴏʀᴍ:</b> {1}\n\n<b>ʀᴀᴍ ᴜꜱᴀɢᴇ:</b> {2}\n<b>ᴄᴘᴜ ᴜꜱᴀɢᴇ:</b> {3}\n<b>ꜱᴛᴏʀᴀɢᴇ:</b> {4}\n\n<b>ᴘʏᴛʜᴏɴ:</b> <code>ᴠ{5}</code>\n<b>ᴘʏʀᴏɢʀᴀᴍ:</b> <code>ᴠ{6}</code>\n<b>ᴘʏᴛɢᴄᴀʟʟꜱ:</b> <code>ᴠ{7}</code></blockquote>",
  "stats_user": "<blockquote><u><b>{0} ꜱᴛᴀᴛꜱ</b></u>\n\n<b>ᴀꜱꜱɪꜱᴛᴀɴᴛꜱ:</b> {1}\n<b>ᴀᴜᴛᴏ ʟᴇᴀᴠᴇ:</b> {2}\n\n<b>ʙʟᴏᴄᴋᴇᴅ ᴄʜᴀᴛꜱ:</b> {3}\n<b>ʙʟᴏᴄᴋᴇᴅ ᴜꜱᴇʀꜱ:</b> {4}\n<b>ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ:</b> {5}\n\n<b>ꜱᴇʀᴠᴇᴅ ᴄʜᴀᴛꜱ:</b> {6}\n<b>ꜱᴇʀᴠᴇᴅ ᴜꜱᴇʀꜱ:</b> {7}</blockquote>",
  "sudo_already": "<blockquote>{0} ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀɴ ꜱᴜᴅᴏ ᴜꜱᴇʀ.</blockquote>",
  "sudo_added": "<blockquote>ᴀᴅᴅᴇᴅ {0} ᴛᴏ ᴛʜᴇ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.</blockquote>",
  "sudo_not": "<blockquote>{0} ɪꜱ ɴᴏᴛ ᴀɴ ꜱᴜᴅᴏ ᴜꜱᴇʀ.</blockquote>",
  "sudo_removed": "<blockquote>ʀᴇᴍᴏᴠᴇᴅ {0} ꜰʀᴏᴍ ᴛʜᴇ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ʟɪꜱᴛ.</blockquote>",
  "sudo_fetching": "<blockquote>ꜰᴇᴛᴄʜɪɴɢ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ʟɪꜱᴛ...</blockquote>",
  "sudo_owner": "<blockquote><u><b>ᴏᴡɴᴇʀ:</b></u>\n- {0}\n\n</blockquote>",
  "sudo_users": "<blockquote><u><b>ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ:</b></u></blockquote>",
  "user_not_admin": "<blockquote>ᴏʜ ꜱᴜʀᴇ, ɢᴏ ᴀʜᴇᴀᴅ ᴀɴᴅ ᴍᴀɴᴀɢᴇ ᴛʜᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛ... ᴏʜ ᴡᴀɪᴛ, ʏᴏᴜ'ʀᴇ ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴ.</blockquote>",
  "user_no_perms": "ʏᴏᴜ? ᴍᴀɴᴀɢᴇ ᴛʜᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛ? ɴᴏᴛ ᴡɪᴛʜ ᴛʜᴏꜱᴇ ᴘᴇʀᴍɪꜱꜱɪᴏɴꜱ, ꜱᴡᴇᴇᴛɪᴇ.",
  "user_not_found": "<blockquote>ɪ'ʟʟ ɴᴇᴇᴅ ᴀ ᴜꜱᴇʀ ɪᴅ — ᴏʀ ᴡᴏᴜʟᴅ ʏᴏᴜ ᴍɪɴᴅ ʀᴇᴘʟʏɪɴɢ ᴛᴏ ꜱᴏᴍᴇᴏɴᴇ'ꜱ ᴍᴇꜱꜱᴀɢᴇ?</blockquote>",
  "vc_empty": "<blockquote>ʟᴏᴏᴋꜱ ʟɪᴋᴇ ᴛʜᴇʀᴇ ᴀʀᴇɴ'ᴛ ᴀɴʏ ᴀᴄᴛɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ ᴏɴ ᴛʜᴇ ʙᴏᴛ.</blockquote>",
  "vc_count": "<blockquote>ᴀᴄᴛɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ ᴏɴ ᴛʜᴇ ʙᴏᴛ: <b>{0}</b></blockquote>",
  "vc_fetching": "<blockquote>ꜰᴇᴛᴄʜɪɴɢ ʟɪꜱᴛ ᴏꜰ ᴀᴄᴛɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ...</blockquote>",
  "vc_list": "<blockquote><u><b>ʟɪꜱᴛ ᴏꜰ ᴀᴄᴛɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ:</b></u></blockquote>",
  "back": "ʙᴀᴄᴋ",
  "backward": "ʙᴀᴄᴋᴡᴀʀᴅ",
  "cancel": "ᴄᴀɴᴄᴇʟ",
  "channel": "ᴄʜᴀɴɴᴇʟ",
  "close": "ᴄʟᴏꜱᴇ",
  "forward": "ꜰᴏʀᴡᴀʀᴅ",
  "help": "ʜᴇʟᴘ",
  "language": "ʟᴀɴɢᴜᴀɢᴇ",
  "paused": "ꜱᴛʀᴇᴀᴍ ᴘᴀᴜꜱᴇᴅ",
  "play_mode": "ᴀᴅᴍɪɴ ᴏɴʟʏ ᴘʟᴀʏ",
  "playing": "ᴘʟᴀʏɪɴɢ",
  "processing": "ᴘʀᴏᴄᴇꜱꜱɪɴɢ...",
  "seeking": "ꜱᴇᴇᴋɪɴɢ...",
  "replayed": "ꜱᴛʀᴇᴀᴍ ʀᴇᴘʟᴀʏᴇᴅ",
  "skipped": "ꜱᴛʀᴇᴀᴍ ꜱᴋɪᴘᴘᴇᴅ",
  "stopped": "ꜱᴛʀᴇᴀᴍ ᴇɴᴅᴇᴅ",
  "source": "ꜱᴏᴜʀᴄᴇ",
  "support": "ꜱᴜᴘᴘᴏʀᴛ",
  "help_0": "ᴀᴅᴍɪɴꜱ",
  "help_1": "ᴀᴜᴛʜ",
  "help_2": "ʙʟᴀᴄᴋʟɪꜱᴛ",
  "help_3": "ʟᴀɴɢᴜᴀɢᴇ",
  "help_4": "ᴘɪɴɢ",
  "help_5": "ᴘʟᴀʏ",
  "help_6": "ǫᴜᴇᴜᴇ",
  "help_7": "ꜱᴛᴀᴛꜱ",
  "help_8": "ꜱᴜᴅᴏᴇʀꜱ",
  "tournament_already_exists": "<blockquote>❌ ᴀ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴄᴛɪᴠᴇ! ᴜꜱᴇ /ᴛᴏᴜʀɴᴀᴍᴇɴᴛꜱᴛᴏᴘ ᴛᴏ ᴇɴᴅ ɪᴛ ꜰɪʀꜱᴛ.</blockquote>",
  "tournament_setup": "<blockquote>🎮 <b>ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ᴀʀᴇɴᴀ ꜱᴇᴛᴜᴘ</b>\n\nᴄʜᴏᴏꜱᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ᴛʏᴘᴇ ᴀɴᴅ ɢᴀᴍᴇ ᴍᴏᴅᴇ:\n\n👥 <b>ᴛᴇᴀᴍ ʙᴀᴛᴛʟᴇ:</b> ᴘʟᴀʏᴇʀꜱ ᴊᴏɪɴ ᴛᴇᴀᴍꜱ ᴀɴᴅ ᴄᴏᴍᴘᴇᴛᴇ ᴛᴏɢᴇᴛʜᴇʀ\n🏆 <b>ꜱᴏʟᴏ:</b> ᴇᴠᴇʀʏ ᴘʟᴀʏᴇʀ ꜰᴏʀ ᴛʜᴇᴍꜱᴇʟᴠᴇꜱ\n\nꜱᴇʟᴇᴄᴛ ɢᴀᴍᴇ ᴛʏᴘᴇ ᴏʀ ᴄʀᴇᴀᴛᴇ ᴡɪᴛʜ ᴅᴇꜰᴀᴜʟᴛ ꜱᴇᴛᴛɪɴɢꜱ (ᴛᴇᴀᴍ + ᴀʟʟ ɢᴀᴍᴇꜱ)</blockquote>",
  "no_tournament": "<blockquote>❌ ɴᴏ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ꜰᴏᴜɴᴅ! ᴜꜱᴇ /ɢᴀᴍᴇᴏɴ ᴛᴏ ᴄʀᴇᴀᴛᴇ ᴏɴᴇ.</blockquote>",
  "tournament_already_active": "<blockquote>❌ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴄᴛɪᴠᴇ!</blockquote>",
  "tournament_min_players": "<blockquote>❌ ɴᴇᴇᴅ ᴀᴛ ʟᴇᴀꜱᴛ 2 ᴘʟᴀʏᴇʀꜱ ᴛᴏ ꜱᴛᴀʀᴛ! ᴄᴜʀʀᴇɴᴛ ᴘʟᴀʏᴇʀꜱ: {0}</blockquote>",
  "tournament_start_failed": "<blockquote>❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ꜱᴛᴀʀᴛ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ!</blockquote>",
  "no_active_tournament": "<blockquote>❌ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ꜰᴏᴜɴᴅ!</blockquote>",
  "tournament_cancelled": "<blockquote>🚫 ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ!</blockquote>",
  "no_leaderboard": "<blockquote>📊 ɴᴏ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴅᴀᴛᴀ ʏᴇᴛ! ᴘʟᴀʏ ꜱᴏᴍᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛꜱ ꜰɪʀꜱᴛ.</blockquote>",
  "leaderboard_title": "<blockquote>🏆 <b>ʜᴀʟʟ ᴏꜰ ᴄʜᴀᴍᴘɪᴏɴꜱ</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n</blockquote>",
  "tournament_joined": "<blockquote>✅ ʏᴏᴜ ᴊᴏɪɴᴇᴅ <b>{0}</b>!\n\nᴡᴀɴᴛ ᴛᴏ ꜱᴡɪᴛᴄʜ ᴛᴇᴀᴍꜱ? ᴄʜᴏᴏꜱᴇ ʙᴇʟᴏᴡ:</blockquote>",
  "tournament_already_started": "<blockquote>❌ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ᴀʟʀᴇᴀᴅʏ ꜱᴛᴀʀᴛᴇᴅ! ᴡᴀɪᴛ ꜰᴏʀ ᴛʜᴇ ɴᴇxᴛ ᴏɴᴇ.</blockquote>",
  "tournament_already_joined": "<blockquote>❌ ʏᴏᴜ'ᴠᴇ ᴀʟʀᴇᴀᴅʏ ᴊᴏɪɴᴇᴅ ᴛʜɪꜱ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ!</blockquote>",
  "tournament_max_players": "<blockquote>❌ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ɪꜱ ꜰᴜʟʟ!</blockquote>",
  "tournament_left": "<blockquote>👋 ʏᴏᴜ ʟᴇꜰᴛ ᴛʜᴇ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ!</blockquote>",
  "not_in_tournament": "<blockquote>❌ ʏᴏᴜ'ʀᴇ ɴᴏᴛ ɪɴ ᴀɴʏ ᴛᴏᴜʀɴᴀᴍᴇɴᴛ ᴏʀ ɪᴛ ᴀʟʀᴇᴀᴅʏ ꜱᴛᴀʀᴛᴇᴅ!</blockquote>",
  "no_player_stats": "<blockquote>❌ ʏᴏᴜ ʜᴀᴠᴇɴ'ᴛ ᴘᴀʀᴛɪᴄɪᴘᴀᴛᴇᴅ ɪɴ ᴀɴʏ ᴛᴏᴜʀɴᴀᴍᴇɴᴛꜱ ʏᴇᴛ!</blockquote>"
}
# ==============================================================================
# __init__.py - Plugin Auto-Discovery Module
# ==============================================================================
# This file automatically discovers all plugin files in subdirectories.
# It scans the plugins/ folder recursively and builds a list of module paths.
#
# Example output: ['admin-controles.broadcast', 'events.callbacks', 'playback-controls.play']
#
# This list is used by __main__.py to dynamically load all plugins at startup,
# making it easy to add new commands without manual registration.
# ==============================================================================

from pathlib import Path


def _list_modules():
    """
    List all Python module filenames (without extension) in the current directory
    and subdirectories, excluding the __init__.py file.

    Returns:
        list: A list of module names as strings with relative paths (e.g., 'admin-controles.broadcast').
    """
    mod_dir = Path(__file__).parent
    modules = []

    # Get all Python files in subdirectories
    for file in mod_dir.rglob("*.py"):
        if file.is_file() and file.name != "__init__.py":
            # Get relative path from plugins directory
            relative_path = file.relative_to(mod_dir)
            # Convert path to module format: folder/file.py -> folder.file
            module_path = str(relative_path.with_suffix(
                '')).replace('\\', '.').replace('/', '.')
            modules.append(module_path)

    return modules


all_modules = frozenset(sorted(_list_modules()))
import os
import asyncio

from pyrogram import errors, filters, types

from HasiiMusic import app, db, lang


broadcasting = False

@app.on_message(filters.command(["broadcast"]) & app.sudoers)
@lang.language()
async def _broadcast(_, message: types.Message):
    global broadcasting
    
    # Extract the broadcast message from command
    if len(message.command) < 2:
        return await message.reply_text(message.lang["gcast_usage"])

    if broadcasting:
        return await message.reply_text(message.lang["gcast_active"])

    # Parse flags and extract actual message
    parts = message.text.split(None, 1)[1].split()
    flags = [part for part in parts if part.startswith('-')]
    message_parts = [part for part in parts if not part.startswith('-')]
    
    if not message_parts:
        return await message.reply_text(message.lang["gcast_usage"])
    
    broadcast_text = ' '.join(message_parts)
    
    count, ucount = 0, 0
    chats, groups, users = [], [], []
    sent = await message.reply_text(message.lang["gcast_start"])

    if "-nochat" not in flags:
        groups.extend(await db.get_chats())
    if "-user" in flags:
        users.extend(await db.get_users())

    chats.extend(groups + users)
    broadcasting = True

    # Log to logger group
    await (await app.send_message(
        chat_id=app.logger, 
        text=message.lang["gcast_log"].format(
            message.from_user.id,
            message.from_user.mention,
            message.text,
        )
    )).pin(disable_notification=False)
    await asyncio.sleep(5)

    failed = ""
    for chat in chats:
        if not broadcasting:
            await sent.edit_text(message.lang["gcast_stopped"].format(count, ucount))
            break

        try:
            # Send as direct message from bot
            await app.send_message(chat, broadcast_text)
            if chat in groups:
                count += 1
            else:
                ucount += 1
            await asyncio.sleep(0.1)
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 30)
            # Retry after flood wait
            try:
                await app.send_message(chat, broadcast_text)
                if chat in groups:
                    count += 1
                else:
                    ucount += 1
            except Exception as retry_ex:
                failed += f"{chat} - {retry_ex}\n"
        except Exception as ex:
            failed += f"{chat} - {ex}\n"
            continue

    text = message.lang["gcast_end"].format(count, ucount)
    if failed:
        with open("errors.txt", "w") as f:
            f.write(failed)
        await message.reply_document(
            document="errors.txt",
            caption=text,
        )
        os.remove("errors.txt")
    broadcasting = False
    await sent.edit_text(text)


@app.on_message(filters.command(["stop_gcast", "stop_broadcast"]) & app.sudoers)
@lang.language()
async def _stop_gcast(_, message: types.Message):
    global broadcasting
    if not broadcasting:
        return await message.reply_text(message.lang["gcast_inactive"])

    broadcasting = False
    await (await app.send_message(
        chat_id=app.logger,
        text=message.lang["gcast_stop_log"].format(
            message.from_user.id,
            message.from_user.mention
        )

    )).pin(disable_notification=False)
    await message.reply_text(message.lang["gcast_stop"])
# HasiiMusic/plugins/admin-controles
# ==============================================================================
# autoleave.py - Auto Leave Command
# ==============================================================================
# This plugin allows sudo users to enable/disable auto-leave feature.
# When enabled, assistant will leave voice chat after 5 minutes if no users
# are listening (only assistant is in the VC).
# ==============================================================================

from pyrogram import filters
from pyrogram.types import Message

from HasiiMusic import app, db


@app.on_message(
    filters.command(["autoleave"])
    & filters.group
    & ~app.bl_users
)
async def autoleave_command(_, m: Message) -> None:
    """Handle /autoleave enable or /autoleave disable command."""
    
    # Check if user is sudo user
    if m.from_user.id not in app.sudoers:
        return await m.reply_text(
            "❌ ᴏɴʟʏ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ."
        )
    
    # Check if subcommand is provided
    if len(m.command) < 2:
        current_status = await db.get_autoleave(m.chat.id)
        status_text = "ᴇɴᴀʙʟᴇᴅ" if current_status else "ᴅɪꜱᴀʙʟᴇᴅ"
        return await m.reply_text(
            f"<blockquote>🔧 ᴀᴜᴛᴏ ʟᴇᴀᴠᴇ ꜱᴛᴀᴛᴜꜱ: {status_text}</blockquote>\n\n"
            "<blockquote><b>ᴜꜱᴀɢᴇ:</b>\n"
            "• `/autoleave enable` - ᴇɴᴀʙʟᴇ ᴀᴜᴛᴏ ʟᴇᴀᴠᴇ\n"
            "• `/autoleave disable` - ᴅɪꜱᴀʙʟᴇ ᴀᴜᴛᴏ ʟᴇᴀᴠᴇ</blockquote>\n\n"
            "<blockquote><i>ᴡʜᴇɴ ᴇɴᴀʙʟᴇᴅ, ᴀꜱꜱɪꜱᴛᴀɴᴛ ᴡɪʟʟ ʟᴇᴀᴠᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴀꜰᴛᴇʀ 5 ᴍɪɴᴜᴛᴇꜱ "
            "ɪꜰ ɴᴏ ᴜꜱᴇʀꜱ ᴀʀᴇ ʟɪꜱᴛᴇɴɪɴɢ.</i></blockquote>"
        )
    
    subcommand = m.command[1].lower()
    
    if subcommand == "enable":
        await db.set_autoleave(m.chat.id, True)
        await m.reply_text(
            "✅ <blockquote>ᴀᴜᴛᴏ ʟᴇᴀᴠᴇ ᴇɴᴀʙʟᴇᴅ!</blockquote>\n\n"
            "<blockquote>ᴀꜱꜱɪꜱᴛᴀɴᴛ ᴡɪʟʟ ʟᴇᴀᴠᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴀꜰᴛᴇʀ <b>5 ᴍɪɴᴜᴛᴇꜱ</b> "
            "ɪꜰ ɴᴏ ᴜꜱᴇʀꜱ ᴀʀᴇ ʟɪꜱᴛᴇɴɪɴɢ.</blockquote>"
        )
    elif subcommand == "disable":
        await db.set_autoleave(m.chat.id, False)
        await m.reply_text(
            "✅ <blockquote>ᴀᴜᴛᴏ ʟᴇᴀᴠᴇ ᴅɪꜱᴀʙʟᴇᴅ!</blockquote>\n\n"
            "<blockquote>ᴀꜱꜱɪꜱᴛᴀɴᴛ ᴡɪʟʟ ꜱᴛᴀʏ ɪɴ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴇᴠᴇɴ ᴡʜᴇɴ ɴᴏ ᴏɴᴇ ɪꜱ ʟɪꜱᴛᴇɴɪɴɢ.</blockquote>"
        )
    else:
        await m.reply_text(
            "❌ <blockquote>ɪɴᴠᴀʟɪᴅ ꜱᴜʙᴄᴏᴍᴍᴀɴᴅ!</blockquote>\n\n"
            "<blockquote><b>ᴜꜱᴀɢᴇ:</b>\n"
            "• `/autoleave enable`\n"
            "• `/autoleave disable`</blockquote>"
        )
"""
Broadcast plugin for HasiiMusicBot.

This plugin allows sudo users to broadcast messages to all groups and users
where the bot is active. It supports various options like sending to users only,
excluding groups, and more.

Commands:
    /broadcast <message> [-user] [-nochat] [-pin] [-pinloud] [-copy]: Broadcast a message
    /stop_gcast, /stop_broadcast: Stop ongoing broadcast

Flags:
    -user: Also send to individual users (in addition to groups)
    -nochat: Don't send to groups (only valid with -user)
    -pin: Pin the broadcasted message (silently)
    -pinloud: Pin the broadcasted message (with notification)
    -copy: Send as a copy without forward tag (default: forwards with tag)
"""

import os
import asyncio
from typing import List, Tuple

from pyrogram import enums, errors, filters, types

from HasiiMusic import app, db, lang


# Global flag to track if a broadcast is currently running
broadcasting: bool = False


@app.on_message(filters.command(["broadcast"]) & app.sudo_filter)
@lang.language()
async def broadcast_message(_, message: types.Message) -> None:
    """
    Broadcast a message to all groups and/or users.

    Usage:
        /broadcast <reply to message> - Forward message to all groups
        /broadcast -copy <reply to message> - Send as copy (no forward tag) to all groups
        /broadcast -user <reply to message> - Forward to all groups and users
        /broadcast -nochat -user <message> - Send only to users

    Args:
        message: The Telegram message containing the broadcast command.

    Returns:
        None
    """
    global broadcasting

    # Check if another broadcast is already running
    if broadcasting:
        return await message.reply_text(message.lang["gcast_active"])

    # Determine if the command was a reply to a media message
    media_message = None
    media_group = None
    if message.reply_to_message:
        media_message = message.reply_to_message
        
        # Check if it's part of a media group (album)
        if media_message.media_group_id:
            try:
                # Get all messages in the media group
                media_group = await _get_media_group(message.chat.id, media_message)
            except Exception as e:
                # If fetching media group fails, just use single message
                pass

    # Parse command: extract flags and actual message
    flags, broadcast_text = _parse_broadcast_command(message.text)

    # Validate: either text or media must be present
    if not broadcast_text and not media_message:
        return await message.reply_text(message.lang["gcast_usage"])

    # Determine recipients based on flags
    groups, users = await _get_broadcast_recipients(flags)
    all_chats = groups + users

    if not all_chats:
        return await message.reply_text(
            "❌ No recipients found. Make sure the bot is added to groups or has users."
        )

    # Set broadcasting flag
    broadcasting = True
    sent = await message.reply_text(message.lang["gcast_start"])

    # Log broadcast initiation
    await _log_broadcast_start(message)
    await asyncio.sleep(5)

    # Perform the broadcast (supports text and media messages)
    success_groups, success_users, failed_chats = await _send_broadcast(
        broadcast_text, groups, users, sent, media_message, flags, message.lang, media_group
    )

    # Reset broadcasting flag
    broadcasting = False

    # Send completion message
    await _send_broadcast_completion(
        message, sent, success_groups, success_users, failed_chats, media_message
    )


@app.on_message(filters.command(["stop_gcast", "stop_broadcast"]) & app.sudo_filter)
@lang.language()
async def stop_broadcast(_, message: types.Message) -> None:
    """
    Stop an ongoing broadcast operation.

    Args:
        message: The Telegram message containing the stop command.

    Returns:
        None
    """
    global broadcasting

    if not broadcasting:
        return await message.reply_text(message.lang["gcast_inactive"])

    broadcasting = False

    # Log broadcast stop
    await (await app.send_message(
        chat_id=app.logger,
        text=message.lang["gcast_stop_log"].format(
            message.from_user.id,
            message.from_user.mention
        )
    )).pin(disable_notification=False)

    await message.reply_text(message.lang["gcast_stop"])


def _parse_broadcast_command(text: str) -> Tuple[List[str], str]:
    """
    Parse broadcast command to extract flags and message.

    Args:
        text: The full command text.

    Returns:
        Tuple of (flags list, message text)
    """
    # Handle None or empty text
    if not text:
        return [], ""

    # Split command from the rest (preserve everything after command)
    parts = text.split(None, 1)
    if len(parts) < 2:
        return [], ""

    remaining_text = parts[1]

    # Extract flags (words starting with '-') from the beginning
    flags = []
    lines = remaining_text.split('\n')
    first_line_parts = lines[0].split()


async def _get_media_group(chat_id: int, message: types.Message) -> List[types.Message]:
    """
    Get all messages in a media group (album).

    Args:
        chat_id: The chat ID where the media group is.
        message: One message from the media group.

    Returns:
        List of messages in the media group, sorted by message ID.
    """
    if not message.media_group_id:
        return None

    media_group_id = message.media_group_id
    messages = []
    
    # Search backward and forward from the current message to find all messages with same media_group_id
    # Telegram typically sends media group messages with consecutive IDs
    search_range = 20  # Search 20 messages before and after
    
    try:
        # Get messages around the replied message
        start_id = max(1, message.id - search_range)
        end_id = message.id + search_range
        
        for msg_id in range(start_id, end_id + 1):
            try:
                msg = await app.get_messages(chat_id, msg_id)
                if msg and hasattr(msg, 'media_group_id') and msg.media_group_id == media_group_id:
                    messages.append(msg)
            except:
                continue
                
        # Sort by message ID to maintain order
        messages.sort(key=lambda x: x.id)
        return messages if messages else None
    except Exception as e:
        return None


def _parse_broadcast_command(text: str) -> Tuple[List[str], str]:
    """
    Parse broadcast command to extract flags and message.

    Args:
        text: The full command text.

    Returns:
        Tuple of (flags list, message text)
    """
    # Handle None or empty text
    if not text:
        return [], ""

    # Split command from the rest (preserve everything after command)
    parts = text.split(None, 1)
    if len(parts) < 2:
        return [], ""

    remaining_text = parts[1]

    # Extract flags (words starting with '-') from the beginning
    flags = []
    lines = remaining_text.split('\n')
    first_line_parts = lines[0].split()

    # Collect flags only from first line
    message_start_index = 0
    for i, part in enumerate(first_line_parts):
        if part.startswith('-'):
            flags.append(part)
            message_start_index = i + 1
        else:
            # Stop collecting flags once we hit non-flag text
            break

    # Reconstruct message preserving all newlines and formatting
    if message_start_index > 0:
        # Remove flags from first line
        first_line_without_flags = ' '.join(
            first_line_parts[message_start_index:])
        if len(lines) > 1:
            message_text = first_line_without_flags + \
                '\n' + '\n'.join(lines[1:])
        else:
            message_text = first_line_without_flags
    else:
        message_text = remaining_text

    return flags, message_text.strip()


async def _get_broadcast_recipients(flags: List[str]) -> Tuple[List[int], List[int]]:
    """
    Get list of groups and users to broadcast to based on flags.

    Args:
        flags: List of command flags.

    Returns:
        Tuple of (groups list, users list)
    """
    groups = []
    users = []

    # Include groups unless -nochat flag is present
    if "-nochat" not in flags:
        groups = await db.get_chats()

    # Include users if -user flag is present
    if "-user" in flags:
        users = await db.get_users()

    return groups, users


async def _log_broadcast_start(message: types.Message) -> None:
    """
    Log broadcast initiation to logger group.

    Args:
        message: The original broadcast command message.

    Returns:
        None
    """
    try:
        log_message = await app.send_message(
            chat_id=app.logger,
            text=message.lang["gcast_log"].format(
                message.from_user.id,
                message.from_user.mention,
                message.text,
            )
        )
    except errors.FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        log_message = await app.send_message(
            chat_id=app.logger,
            text=message.lang["gcast_log"].format(
                message.from_user.id,
                message.from_user.mention,
                message.text,
            )
        )

    try:
        await log_message.pin(disable_notification=False)
    except errors.FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        try:
            await log_message.pin(disable_notification=False)
        except Exception:
            pass


async def _send_broadcast(
    text: str,
    groups: List[int],
    users: List[int],
    status_message: types.Message,
    media_message: types.Message | None = None,
    flags: List[str] = None,
    lang: dict = None,
    media_group: List[types.Message] = None,
) -> Tuple[int, int, str]:
    """
    Send broadcast message to all recipients.

    Args:
        text: Message text to broadcast.
        groups: List of group chat IDs.
        users: List of user IDs.
        status_message: Message to update with progress.
        media_message: Optional media message to broadcast.
        flags: List of command flags.
        lang: Language dictionary for localized strings.
        media_group: Optional list of messages if broadcasting a media group (album).

    Returns:
        Tuple of (successful groups count, successful users count, failed chats log)
    """
    global broadcasting

    # Use provided flags or default to empty list
    if flags is None:
        flags = []

    success_groups = 0
    success_users = 0
    failed_log = ""
    pinned_count = 0
    all_chats = groups + users
    total_chats = len(all_chats)

    for index, chat_id in enumerate(all_chats, start=1):
        # Check if broadcast was stopped
        if not broadcasting:
            await status_message.edit_text(
                lang["gcast_stopped"].format(
                    success_groups, success_users)
            )
            break

        # Update progress every 50 chats
        if index % 50 == 0:
            try:
                await status_message.edit_text(
                    f"📤 Broadcasting...\n\n"
                    f"Progress: {index}/{total_chats}\n"
                    f"✅ Groups: {success_groups}\n"
                    f"✅ Users: {success_users}"
                )
            except:
                pass

        # Attempt to send message
        try:
            # Check if it's a channel (not a group) - skip channels
            if chat_id in groups:
                try:
                    chat = await app.get_chat(chat_id)
                    # Skip channels - only send to supergroups (groups)
                    if chat.type == enums.ChatType.CHANNEL:
                        failed_log += f"{chat_id} - Skipped (channel, not a group)\n"
                        continue
                except:
                    pass  # If can't get chat info, try to send anyway

            # Priority 1: If media_group exists (album), send as media group
            if media_group:
                sent_message = None
                try:
                    # Check if -copy flag is present
                    if "-copy" in flags:
                        # Copy mode: send as media group without forward tag
                        media_list = []
                        for idx, msg in enumerate(media_group):
                            # Use caption from first media or provided text
                            caption = text if (idx == 0 and text) else (msg.caption if idx == 0 else None)
                            
                            if msg.photo:
                                file_id = msg.photo.file_id if hasattr(msg.photo, 'file_id') else msg.photo[-1].file_id
                                media_list.append(types.InputMediaPhoto(media=file_id, caption=caption))
                            elif getattr(msg, 'video', None):
                                media_list.append(types.InputMediaVideo(media=msg.video.file_id, caption=caption))
                            elif getattr(msg, 'audio', None):
                                media_list.append(types.InputMediaAudio(media=msg.audio.file_id, caption=caption))
                            elif getattr(msg, 'document', None):
                                media_list.append(types.InputMediaDocument(media=msg.document.file_id, caption=caption))
                        
                        if media_list:
                            sent_messages = await app.send_media_group(chat_id=chat_id, media=media_list)
                            sent_message = sent_messages[0] if sent_messages else None
                            
                            # Handle pinning if requested (pin first message)
                            if sent_message and chat_id in groups:
                                if "-pin" in flags:
                                    try:
                                        await sent_message.pin(disable_notification=True)
                                        pinned_count += 1
                                    except:
                                        pass
                                elif "-pinloud" in flags:
                                    try:
                                        await sent_message.pin(disable_notification=False)
                                        pinned_count += 1
                                    except:
                                        pass
                        else:
                            failed_log += f"{chat_id} - No valid media in group\n"
                            await asyncio.sleep(0.3)
                            continue
                    else:
                        # Forward mode: forward all messages in the group
                        sent_messages = []
                        for msg in media_group:
                            try:
                                fwd = await msg.forward(chat_id)
                                sent_messages.append(fwd)
                            except Exception as fwd_ex:
                                continue
                        
                        if sent_messages:
                            sent_message = sent_messages[0]
                            # Handle pinning if requested (pin first message)
                            if sent_message and chat_id in groups:
                                if "-pin" in flags:
                                    try:
                                        await sent_message.pin(disable_notification=True)
                                        pinned_count += 1
                                    except:
                                        pass
                                elif "-pinloud" in flags:
                                    try:
                                        await sent_message.pin(disable_notification=False)
                                        pinned_count += 1
                                    except:
                                        pass
                        else:
                            failed_log += f"{chat_id} - Failed to forward media group\n"
                            await asyncio.sleep(0.3)
                            continue
                            
                except Exception as mg_ex:
                    failed_log += f"{chat_id} - Media group send failed: {type(mg_ex).__name__}: {str(mg_ex)}\n"
                    await asyncio.sleep(0.3)
                    continue

            # Priority 2: If a single media message was provided, forward or copy based on -copy flag
            elif media_message:
                sent_message = None

                # Check if -copy flag is present
                if "-copy" in flags:
                    # Copy mode: send media without forward tag
                    caption = text if text else (media_message.caption or "")
                    try:
                        if media_message.photo:
                            # Photo is a list of PhotoSize objects, get the largest
                            file_id = media_message.photo.file_id if hasattr(
                                media_message.photo, 'file_id') else media_message.photo[-1].file_id
                            sent_message = await app.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
                        elif getattr(media_message, 'video', None):
                            file_id = media_message.video.file_id
                            sent_message = await app.send_video(chat_id=chat_id, video=file_id, caption=caption)
                        elif getattr(media_message, 'audio', None):
                            file_id = media_message.audio.file_id
                            sent_message = await app.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
                        elif getattr(media_message, 'voice', None):
                            file_id = media_message.voice.file_id
                            sent_message = await app.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
                        elif getattr(media_message, 'document', None):
                            file_id = media_message.document.file_id
                            sent_message = await app.send_document(chat_id=chat_id, document=file_id, caption=caption)
                        elif getattr(media_message, 'animation', None):
                            file_id = media_message.animation.file_id
                            sent_message = await app.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
                        elif getattr(media_message, 'sticker', None):
                            file_id = media_message.sticker.file_id
                            sent_message = await app.send_sticker(chat_id=chat_id, sticker=file_id)
                        else:
                            # Text-only message: copy the text
                            message_text = text if text else (
                                media_message.text or media_message.caption or "")
                            if message_text:
                                sent_message = await app.send_message(chat_id, message_text)
                            else:
                                failed_log += f"{chat_id} - Empty message\n"
                                await asyncio.sleep(0.3)
                                continue

                        # Handle pinning if requested
                        if sent_message and chat_id in groups:
                            if "-pin" in flags:
                                try:
                                    await sent_message.pin(disable_notification=True)
                                    pinned_count += 1
                                except:
                                    pass
                            elif "-pinloud" in flags:
                                try:
                                    await sent_message.pin(disable_notification=False)
                                    pinned_count += 1
                                except:
                                    pass

                    except Exception as send_ex:
                        failed_log += f"{chat_id} - Media send failed: {type(send_ex).__name__}: {str(send_ex)}\n"
                        continue
                else:
                    # Forward mode: forward the message with forward tag
                    try:
                        sent_message = await media_message.forward(chat_id)

                        # Handle pinning if requested
                        if sent_message and chat_id in groups:
                            if "-pin" in flags:
                                try:
                                    await sent_message.pin(disable_notification=True)
                                    pinned_count += 1
                                except:
                                    pass
                            elif "-pinloud" in flags:
                                try:
                                    await sent_message.pin(disable_notification=False)
                                    pinned_count += 1
                                except:
                                    pass
                    except Exception as fwd_ex:
                        failed_log += f"{chat_id} - Forward failed: {type(fwd_ex).__name__}: {str(fwd_ex)}\n"
                        continue
            else:
                # No media: send text message
                sent_message = await app.send_message(chat_id, text)

                # Handle pinning if requested
                if sent_message and chat_id in groups:
                    if "-pin" in flags:
                        try:
                            await sent_message.pin(disable_notification=True)
                            pinned_count += 1
                        except:
                            pass
                    elif "-pinloud" in flags:
                        try:
                            await sent_message.pin(disable_notification=False)
                            pinned_count += 1
                        except:
                            pass

            # Track success
            if chat_id in groups:
                success_groups += 1
            else:
                success_users += 1

            # Anti-flood delay: 300ms between messages (safer than 100ms)
            await asyncio.sleep(0.3)

        except errors.FloodWait as fw:
            # Handle flood wait by waiting and continuing (don't stop broadcast)
            try:
                await status_message.edit_text(
                    f"⏳ Flood wait triggered. Waiting {fw.value} seconds...\n\n"
                    f"Progress: {index}/{total_chats}\n"
                    f"Don't worry, broadcast will continue!"
                )
            except:
                pass

            await asyncio.sleep(fw.value + 5)

            # Retry sending after waiting
            try:
                retry_sent = None
                
                # Retry media group if it was a media group
                if media_group:
                    if "-copy" in flags:
                        media_list = []
                        for idx, msg in enumerate(media_group):
                            caption = text if (idx == 0 and text) else (msg.caption if idx == 0 else None)
                            if msg.photo:
                                file_id = msg.photo.file_id if hasattr(msg.photo, 'file_id') else msg.photo[-1].file_id
                                media_list.append(types.InputMediaPhoto(media=file_id, caption=caption))
                            elif getattr(msg, 'video', None):
                                media_list.append(types.InputMediaVideo(media=msg.video.file_id, caption=caption))
                            elif getattr(msg, 'audio', None):
                                media_list.append(types.InputMediaAudio(media=msg.audio.file_id, caption=caption))
                            elif getattr(msg, 'document', None):
                                media_list.append(types.InputMediaDocument(media=msg.document.file_id, caption=caption))
                        if media_list:
                            retry_msgs = await app.send_media_group(chat_id=chat_id, media=media_list)
                            retry_sent = retry_msgs[0] if retry_msgs else None
                    else:
                        for msg in media_group:
                            try:
                                fwd = await msg.forward(chat_id)
                                if not retry_sent:
                                    retry_sent = fwd
                            except:
                                continue
                
                # Retry single media message
                elif media_message:
                    # Check if -copy flag for retry as well
                    if "-copy" in flags:
                        caption = text if text else (
                            media_message.caption or "")
                        if media_message.photo:
                            # Photo is a list of PhotoSize objects, get the largest
                            file_id = media_message.photo.file_id if hasattr(
                                media_message.photo, 'file_id') else media_message.photo[-1].file_id
                            retry_sent = await app.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
                        elif getattr(media_message, 'video', None):
                            file_id = media_message.video.file_id
                            retry_sent = await app.send_video(chat_id=chat_id, video=file_id, caption=caption)
                        elif getattr(media_message, 'audio', None):
                            file_id = media_message.audio.file_id
                            retry_sent = await app.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
                        elif getattr(media_message, 'voice', None):
                            file_id = media_message.voice.file_id
                            retry_sent = await app.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
                        elif getattr(media_message, 'document', None):
                            file_id = media_message.document.file_id
                            retry_sent = await app.send_document(chat_id=chat_id, document=file_id, caption=caption)
                        elif getattr(media_message, 'animation', None):
                            file_id = media_message.animation.file_id
                            retry_sent = await app.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
                        elif getattr(media_message, 'sticker', None):
                            file_id = media_message.sticker.file_id
                            retry_sent = await app.send_sticker(chat_id=chat_id, sticker=file_id)
                        else:
                            retry_sent = await app.send_message(chat_id, text)
                    else:
                        # Forward mode for retry
                        retry_sent = await media_message.forward(chat_id)
                else:
                    retry_sent = await app.send_message(chat_id, text)

                # Handle pinning on retry
                if retry_sent and chat_id in groups:
                    if "-pin" in flags:
                        try:
                            await retry_sent.pin(disable_notification=True)
                            pinned_count += 1
                        except:
                            pass
                    elif "-pinloud" in flags:
                        try:
                            await retry_sent.pin(disable_notification=False)
                            pinned_count += 1
                        except:
                            pass

                if chat_id in groups:
                    success_groups += 1
                else:
                    success_users += 1
            except Exception as retry_ex:
                failed_log += f"{chat_id} - FloodWait retry failed: {retry_ex}\n"

        except errors.UserIsBlocked:
            # User blocked the bot - skip silently
            failed_log += f"{chat_id} - User blocked bot\n"
            continue

        except errors.ChatWriteForbidden:
            # Bot can't write in this chat - skip
            failed_log += f"{chat_id} - No write permission\n"
            continue

        except errors.ChannelPrivate:
            # Bot was removed from channel/group - remove from database
            if chat_id in groups:
                try:
                    await db.rm_chat(chat_id)
                    failed_log += f"{chat_id} - Removed from group (cleaned from database)\n"
                except:
                    failed_log += f"{chat_id} - Channel private (bot not member)\n"
            else:
                failed_log += f"{chat_id} - Channel private\n"
            continue

        except errors.PeerIdInvalid:
            # Invalid chat ID - remove from database
            if chat_id in groups:
                try:
                    await db.rm_chat(chat_id)
                    failed_log += f"{chat_id} - Invalid ID (cleaned from database)\n"
                except:
                    failed_log += f"{chat_id} - Invalid chat ID\n"
            else:
                failed_log += f"{chat_id} - Invalid user ID\n"
            continue

        except Exception as ex:
            # Log failed send but CONTINUE to next chat
            failed_log += f"{chat_id} - {type(ex).__name__}: {str(ex)}\n"
            continue

    return success_groups, success_users, failed_log


async def _send_broadcast_completion(
    message: types.Message,
    status_message: types.Message,
    success_groups: int,
    success_users: int,
    failed_log: str,
    media_message: types.Message | None = None,
) -> None:
    """
    Send broadcast completion message with results.

    Args:
        message: Original command message.
        status_message: Status message to edit.
        success_groups: Number of successful group sends.
        success_users: Number of successful user sends.
        failed_log: Log of failed sends.
        media_message: Optional media message that was broadcast.

    Returns:
        None
    """
    media_type = "text"
    if media_message:
        if media_message.photo:
            media_type = "photo"
        elif getattr(media_message, 'video', None):
            media_type = "video"
        elif getattr(media_message, 'audio', None):
            media_type = "audio"
        elif getattr(media_message, 'document', None):
            media_type = "document"
        elif getattr(media_message, 'animation', None):
            media_type = "animation"
        elif getattr(media_message, 'sticker', None):
            media_type = "sticker"

    completion_text = message.lang["gcast_end"].format(
        success_groups, success_users)
    if media_message:
        completion_text += f"\n📎 Media type: {media_type}"

    # If there were failures, send error file
    if failed_log:
        error_file = "errors.txt"
        with open(error_file, "w") as f:
            f.write(failed_log)

        await message.reply_document(
            document=error_file,
            caption=completion_text,
        )
        os.remove(error_file)

    await status_message.edit_text(completion_text)
# ==============================================================================
# eval.py - Code Execution Command (Owner Only)
# ==============================================================================
# This plugin allows the bot owner to execute Python code and shell commands remotely.
#
# Commands:
# - /eval <code> - Execute Python code in the bot's context
# - /exec <code> - Same as /eval (alias)
#
# Security: Only the bot owner (defined in config) can use this command.
#
# Features:
# - Async code support (can use await)
# - Access to all bot variables (app, db, userbot, etc.)
# - Output returned as message or file if too long
# - Syntax highlighting in responses
# ==============================================================================

import io
import os
import re
import sys
import traceback
import uuid
from html import escape
from typing import Any, Optional, Tuple

from pyrogram import filters, types

from HasiiMusic import tune, app, config, db, lang, userbot
from HasiiMusic.helpers import format_exception, meval


@app.on_message(filters.command(["eval", "exec"]) & filters.user(app.owner))
@app.on_edited_message(filters.command(["eval", "exec"]) & filters.user(app.owner))
@lang.language()
async def eval_handler(_, message: types.Message):
    if len(message.command) < 2:
        return await message.reply_text(message.lang["eval_inp"])

    code = message.text.split(None, 1)[1]
    out_buf = io.StringIO()

    async def _eval_code() -> Tuple[str, Optional[str]]:
        async def send(*args: Any, **kwargs: Any) -> types.Message:
            return await message.reply_text(*args, **kwargs)

        def _print(*args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("file", out_buf)
            print(*args, **kwargs)

        eval_vars = {
            "m": message,
            "r": message.reply_to_message,
            "chat": message.chat,
            "user": message.from_user,
            "app": app,
            "tune": tune,
            "db": db,
            "client": app,
            "ub": userbot,
            "ikb": types.InlineKeyboardButton,
            "ikm": types.InlineKeyboardMarkup,
            "send": send,
            "config": config,
            "print": _print,
            "os": os,
            "re": re,
            "sys": sys,
            "traceback": traceback,
        }

        try:
            result = await meval(code, globals(), **eval_vars)
            return "", result
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            snippet_tb = next(
                (i for i, f in enumerate(tb) if f.filename == "<string>"), -1
            )
            formatted_tb = format_exception(
                e, tb[snippet_tb:] if snippet_tb != -1 else tb
            )
            return message.lang["eval_error"], formatted_tb

    prefix, result = await _eval_code()

    if result is not None or not out_buf.getvalue():
        print(result, file=out_buf)

    output = out_buf.getvalue().strip()
    response = message.lang["eval_out"].format(escape(output))

    if len(response) > 4096:
        with io.BytesIO(output.encode()) as out_file:
            out_file.name = f"{uuid.uuid4().hex[:8].lower()}.txt"
            return await message.reply_document(
                document=out_file, disable_notification=True
            )

    await message.reply_text(response)
# ==============================================================================
# leave.py - Force Leave Command (Sudo Only)
# ==============================================================================
# This plugin allows sudo users to make the bot and assistant leave any chat.
#
# Commands:
# - /leave - Make bot and assistant leave the current chat
#
# Only sudo users can use this command.
# ==============================================================================

from pyrogram import filters, types, errors

from HasiiMusic import app, db, lang


@app.on_message(filters.command(["leave"]) & app.sudo_filter)
@lang.language()
async def _leave(_, m: types.Message):
    """
    Command handler for /leave
    Makes both bot and assistant leave the current chat.
    """
    chat_id = m.chat.id
    chat_name = m.chat.title or "this chat"

    # Send confirmation message
    sent = await m.reply_text(
        f"<blockquote><b>👋 Leaving Chat</b></blockquote>\n\n"
        f"<blockquote>Bot and assistant are leaving <b>{chat_name}</b>...</blockquote>"
    )

    # Try to make assistant leave if it's in the chat
    try:
        client = await db.get_client(chat_id)
        try:
            await client.leave_chat(chat_id)
        except errors.UserNotParticipant:
            # Assistant is not in the chat, skip
            pass
        except Exception as e:
            # Log any other errors but continue with bot leaving
            pass
    except Exception:
        # If getting client fails, just continue with bot leaving
        pass

    # Make bot leave the chat
    try:
        await app.leave_chat(chat_id)
    except Exception as e:
        # If bot can't leave, inform the sudo user
        await sent.edit_text(
            f"<blockquote><b>❌ Error</b></blockquote>\n\n"
            f"<blockquote>Failed to leave chat: {str(e)}</blockquote>"
        )
# ==============================================================================
# restart.py - Bot Restart & Logging Commands (Sudo Only)
# ==============================================================================
# This plugin provides administrative commands for bot maintenance.
#
# Commands:
# - /logs - Get log file
# - /logger on/off - Enable/disable database logging
# - /restart - Restart the bot
# - /update - Update bot from git and restart
#
# All commands require sudo user permissions.
# ==============================================================================

import os
import sys
import shutil
import asyncio

from pyrogram import filters, types

from HasiiMusic import app, db, lang, stop


@app.on_message(filters.command(["logs"]) & app.sudo_filter)
@lang.language()
async def _logs(_, m: types.Message):
    sent = await m.reply_text(m.lang["log_fetch"])
    if not os.path.exists("log.txt"):
        return await sent.edit_text(m.lang["log_not_found"])
    await sent.edit_media(
        media=types.InputMediaDocument(
            media="log.txt",
            caption=m.lang["log_sent"].format(app.name),
        )
    )


@app.on_message(filters.command(["logger"]) & app.sudo_filter)
@lang.language()
async def _logger(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(m.lang["logger_usage"].format(m.command[0]))
    if m.command[1] not in ("on", "off"):
        return await m.reply_text(m.lang["logger_usage"].format(m.command[0]))

    if m.command[1] == "on":
        await db.set_logger(True)
        await m.reply_text(m.lang["logger_on"])
    else:
        await db.set_logger(False)
        await m.reply_text(m.lang["logger_off"])


@app.on_message(filters.command(["restart"]) & app.sudo_filter)
@lang.language()
async def _restart(_, m: types.Message):
    sent = await m.reply_text(m.lang["restarting"])

    for directory in ["cache", "downloads"]:
        shutil.rmtree(directory, ignore_errors=True)

    await sent.edit_text(m.lang["restarted"])
    asyncio.create_task(stop())
    await asyncio.sleep(2)

    os.execl(sys.executable, sys.executable, "-m", "HasiiMusic")
# ==============================================================================
# sudoers.py - Sudo User Management (Owner Only)
# ==============================================================================
# This plugin allows the bot owner to add/remove sudo users.
# Sudo users have elevated permissions and can use admin commands.
#
# Commands:
# - /addsudo <user> - Grant sudo permissions
# - /delsudo <user> - Revoke sudo permissions
# - /rmsudo <user> - Same as /delsudo
#
# Only the bot owner (defined in config) can manage sudo users.
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import app, db, lang
from HasiiMusic.helpers import utils


@app.on_message(filters.command(["addsudo", "delsudo", "rmsudo"]) & app.sudo_filter)
@lang.language()
async def _sudo(_, m: types.Message):
    user = await utils.extract_user(m)
    if not user:
        return await m.reply_text(m.lang["user_not_found"])

    if m.command[0] == "addsudo":
        if user.id in app.sudoers:
            return await m.reply_text(m.lang["sudo_already"].format(user.mention))

        app.sudoers.add(user.id)
        app.sudo_filter.update([user.id])
        await db.add_sudo(user.id)
        await m.reply_text(m.lang["sudo_added"].format(user.mention))
    else:
        if user.id not in app.sudoers:
            return await m.reply_text(m.lang["sudo_not"].format(user.mention))

        app.sudoers.discard(user.id)
        app.sudo_filter.update([])  # Reset filter
        app.sudo_filter.update(app.sudoers)  # Rebuild with remaining users
        await db.del_sudo(user.id)
        await m.reply_text(m.lang["sudo_removed"].format(user.mention))


o_mention = None


@app.on_message(filters.command(["listsudo", "sudolist"]))
@lang.language()
async def _listsudo(_, m: types.Message):
    global o_mention
    sent = await m.reply_text(m.lang["sudo_fetching"])

    if not o_mention:
        o_mention = (await app.get_users(app.owner)).mention
    txt = m.lang["sudo_owner"].format(o_mention)
    sudoers = await db.get_sudoers()
    if sudoers:
        txt += m.lang["sudo_users"]

    for user_id in sudoers:
        try:
            user = (await app.get_users(user_id)).mention
            txt += f"\n- {user}"
        except:
            continue

    await sent.edit_text(txt)
# ==============================================================================
# callbacks.py - Callback Query Handler
# ==============================================================================
# This plugin handles inline button callbacks (when users press inline buttons).
#
# Callback Types:
# - cancel_dl - Cancel ongoing download
# - controls - Playback controls (pause, resume, skip, replay, etc.)
# - close - Close/delete message
# - help_menu - Navigate help pages
#
# Most callbacks require admin/authorized user permissions.
# ==============================================================================

import re
import asyncio
from functools import wraps

from pyrogram import filters, types
from pyrogram.errors import FloodWait

from HasiiMusic import tune, app, db, lang, logger, queue, tg, yt
from HasiiMusic.helpers import admin_check, buttons, can_manage_vc


def safe_callback(func):
    """Decorator to handle exceptions in callback handlers."""
    @wraps(func)
    async def wrapper(client, query: types.CallbackQuery):
        try:
            return await func(client, query)
        except Exception as e:
            logger.error(f"Error in callback {func.__name__}: {e}", exc_info=True)
            try:
                await query.answer("❌ An error occurred. Please try again.", show_alert=True)
            except Exception:
                pass
    return wrapper


@app.on_callback_query(filters.regex("cancel_dl") & ~app.bl_users)
@lang.language()
@safe_callback
async def cancel_dl(_, query: types.CallbackQuery):
    await query.answer()
    await tg.cancel(query)


@app.on_callback_query(filters.regex("controls") & ~app.bl_users)
@lang.language()
@safe_callback
async def _controls(_, query: types.CallbackQuery):
    args = query.data.split()
    action, chat_id = args[1], int(args[2])
    qaction = len(args) == 4
    user = query.from_user.mention

    # Handle close action first - allow any user to delete the message (no popup notification)
    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # Check admin permissions for all other controls
    # Inline permission check: sudo users, authorized users, or group admins
    user_id = query.from_user.id
    has_permission = False
    
    if user_id in app.sudoers:
        has_permission = True
    elif await db.is_auth(chat_id, user_id):
        has_permission = True
    else:
        admins = await db.get_admins(chat_id)
        if user_id in admins:
            has_permission = True
    
    if not has_permission:
        return await query.answer("⚠️ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴘᴇʀᴍɪssɪᴏɴ ᴛᴏ ᴜsᴇ ᴛʜɪs.", show_alert=True)

    if not await db.get_call(chat_id):
        return await query.answer(query.lang["not_playing"], show_alert=True)

    if action == "status":
        return await query.answer()
    
    # Handle seek actions
    if action.startswith("seek_"):
        return await handle_seek(query, chat_id, action, user)
    
    # Handle loop action
    if action == "loop":
        return await handle_loop(query, chat_id, user)
    
    # Handle shuffle action
    if action == "shuffle":
        return await handle_shuffle(query, chat_id, user)
    
    await query.answer(query.lang["processing"], show_alert=True)

    if action == "pause":
        if not await db.playing(chat_id):
            return await query.answer(
                query.lang["play_already_paused"], show_alert=True
            )
        await tune.pause(chat_id)
        if qaction:
            return await query.edit_message_reply_markup(
                reply_markup=buttons.queue_markup(
                    chat_id, query.lang["paused"], False)
            )
        status = query.lang["paused"]
        reply = query.lang["play_paused"].format(user)

    elif action == "resume":
        status = query.lang["playing"]
        if await db.playing(chat_id):
            return await query.answer(query.lang["play_not_paused"], show_alert=True)
        await tune.resume(chat_id)
        if qaction:
            return await query.edit_message_reply_markup(
                reply_markup=buttons.queue_markup(
                    chat_id, query.lang["playing"], True)
            )
        reply = query.lang["play_resumed"].format(user)

    elif action == "skip":
        await tune.play_next(chat_id)
        status = query.lang["skipped"]
        reply = query.lang["play_skipped"].format(user)

    elif action == "force":
        pos, media = queue.check_item(chat_id, args[3])
        if not media or pos == -1:
            return await query.edit_message_text(query.lang["play_expired"])

        current = queue.get_current(chat_id)
        m_id = current.message_id if current else None
        queue.force_add(chat_id, media, remove=pos)
        try:
            await app.delete_messages(
                chat_id=chat_id, message_ids=[
                    m_id, media.message_id], revoke=True
            )
            media.message_id = None
        except:
            pass

        msg = await app.send_message(chat_id=chat_id, text=query.lang["play_next"])
        if not media.file_path:
            media.file_path = await yt.download(media.id)
        media.message_id = msg.id
        return await tune.play_media(chat_id, msg, media)

    elif action == "replay":
        media = queue.get_current(chat_id)
        media.user = user
        await tune.replay(chat_id)
        status = query.lang["replayed"]
        reply = query.lang["play_replayed"].format(user)

    elif action == "stop":
        await tune.stop(chat_id)
        status = query.lang["stopped"]
        reply = query.lang["play_stopped"].format(user)

    try:
        if action in ["skip", "replay", "stop"]:
            try:
                await query.message.reply_text(reply, quote=False)
            except FloodWait as e:
                # If FloodWait occurs, wait and retry once
                await asyncio.sleep(e.value)
                try:
                    await query.message.reply_text(reply, quote=False)
                except Exception:
                    pass
            except Exception:
                pass
            await query.message.delete()
        else:
            mtext = re.sub(
                r"\n\n<blockquote>.*?</blockquote>",
                "",
                query.message.caption.html or query.message.text.html,
                flags=re.DOTALL,
            )
            keyboard = buttons.controls(
                chat_id, status=status if action != "resume" else None
            )
        await query.edit_message_text(
            f"{mtext}\n\n<blockquote>{reply}</blockquote>", reply_markup=keyboard
        )
    except FloodWait as e:
        # Handle FloodWait on edit_message_text
        await asyncio.sleep(e.value)
        try:
            await query.edit_message_text(
                f"{mtext}\n\n<blockquote>{reply}</blockquote>", reply_markup=keyboard
            )
        except Exception:
            pass
    except Exception:
        pass


async def handle_seek(query: types.CallbackQuery, chat_id: int, action: str, user: str):
    """Handle seek forward/backward actions."""
    media = queue.get_current(chat_id)
    if not media or media.is_live:
        return await query.answer("⚠️ ᴄᴀɴɴᴏᴛ ꜱᴇᴇᴋ ɪɴ ʟɪᴠᴇ ꜱᴛʀᴇᴀᴍꜱ!", show_alert=True)
    
    if not media.duration_sec or media.duration_sec == 0:
        return await query.answer("⚠️ ᴄᴀɴɴᴏᴛ ꜱᴇᴇᴋ ɪɴ ᴛʜɪꜱ ᴛʀᴀᴄᴋ!", show_alert=True)
    
    # Determine seek amount and direction
    if action == "seek_back_10":
        seconds = -10
        label = "« 10s"
    elif action == "seek_back_30":
        seconds = -30
        label = "« 30s"
    elif action == "seek_forward_10":
        seconds = 10
        label = "10s »"
    elif action == "seek_forward_30":
        seconds = 30
        label = "30s »"
    else:
        return await query.answer("⚠️ ɪɴᴠᴀʟɪᴅ ꜱᴇᴇᴋ ᴀᴄᴛɪᴏɴ!", show_alert=True)
    
    # Calculate new position
    current_time = getattr(media, 'time', 0)
    new_time = max(0, min(current_time + seconds, media.duration_sec - 5))
    
    # Check if we're at the boundaries
    if new_time == 0 and seconds < 0:
        return await query.answer(f"⏮️ ᴀʟʀᴇᴀᴅʏ ᴀᴛ ᴛʜᴇ ʙᴇɢɪɴɴɪɴɢ!", show_alert=True)
    if new_time >= media.duration_sec - 5 and seconds > 0:
        return await query.answer(f"⏭️ ᴛᴏᴏ ᴄʟᴏꜱᴇ ᴛᴏ ᴛʜᴇ ᴇɴᴅ!", show_alert=True)
    
    # Perform seek
    success = await tune.seek_stream(chat_id, int(new_time))
    if success:
        # Format time display
        import time as time_module
        if media.duration_sec >= 3600:
            time_str = time_module.strftime('%H:%M:%S', time_module.gmtime(new_time))
        else:
            time_str = time_module.strftime('%M:%S', time_module.gmtime(new_time))
        
        # Use callback answer to avoid FloodWait
        await query.answer(f"✅ ꜱᴇᴇᴋᴇᴅ ᴛᴏ {time_str}", show_alert=True)
        
        # Try to send reply message with FloodWait handling
        try:
            await query.message.reply_text(
                f"✅ ꜱᴇᴇᴋᴇᴅ ᴛᴏ {time_str}\n\n<blockquote>ʙʏ {user}</blockquote>",
                quote=False
            )
        except FloodWait as e:
            # If rate limited, just skip the message since user already got feedback via callback
            pass
        except Exception:
            pass


async def handle_loop(query: types.CallbackQuery, chat_id: int, user: str):
    """Handle loop mode toggling."""
    current_loop = await db.get_loop(chat_id)
    
    # Cycle through loop modes: 0 (off) -> 1 (single) -> 10 (queue) -> 0
    if current_loop == 0:
        new_loop = 1
        text = "🔂 ʟᴏᴏᴘ: ꜱɪɴɢʟᴇ ᴛʀᴀᴄᴋ"
        message = f"🔂 ʟᴏᴏᴘ ᴍᴏᴅᴇ ꜱᴇᴛ ᴛᴏ <b>ꜱɪɴɢʟᴇ ᴛʀᴀᴄᴋ</b>"
    elif current_loop == 1:
        new_loop = 10
        text = "🔁 ʟᴏᴏᴘ: ǫᴜᴇᴜᴇ"
        message = f"🔁 ʟᴏᴏᴘ ᴍᴏᴅᴇ ꜱᴇᴛ ᴛᴏ <b>ǫᴜᴇᴜᴇ</b>"
    else:
        new_loop = 0
        text = "➡️ ʟᴏᴏᴘ: ᴏꜰꜰ"
        message = f"➡️ ʟᴏᴏᴘ ᴍᴏᴅᴇ <b>ᴅɪꜱᴀʙʟᴇᴅ</b>"
    
    await db.set_loop(chat_id, new_loop)
    await query.answer(text, show_alert=False)
    await query.message.reply_text(message, quote=False)


async def handle_shuffle(query: types.CallbackQuery, chat_id: int, user: str):
    """Handle queue shuffling."""
    import random
    
    items = queue.get_queue(chat_id)
    if not items or len(items) <= 1:
        return await query.answer("⚠️ ǫᴜᴇᴜᴇ ɪꜱ ᴇᴍᴘᴛʏ ᴏʀ ʜᴀꜱ ᴏɴʟʏ ᴏɴᴇ ᴛʀᴀᴄᴋ!", show_alert=True)
    
    # Get current track and remove from list
    current = items[0] if items else None
    remaining = items[1:] if len(items) > 1 else []
    
    if not remaining:
        return await query.answer("⚠️ ɴᴏ ᴛʀᴀᴄᴋꜱ ᴛᴏ ꜱʜᴜꜰꜰʟᴇ!", show_alert=True)
    
    # Shuffle remaining tracks
    random.shuffle(remaining)
    
    # Rebuild queue with current track first
    queue.clear(chat_id)
    if current:
        queue.add(chat_id, current)
    for item in remaining:
        queue.add(chat_id, item)
    
    await query.answer("🔀 ǫᴜᴇᴜᴇ ꜱʜᴜꜰꜰʟᴇᴅ!", show_alert=False)
    await query.message.reply_text(
        f"🔀 ǫᴜᴇᴜᴇ <b>ꜱʜᴜꜰꜰʟᴇᴅ</b> ({len(remaining)} ᴛʀᴀᴄᴋꜱ)",
        quote=False
    )


@app.on_callback_query(filters.regex(r"^help($| )") & ~app.bl_users)
@lang.language()
async def _help(_, query: types.CallbackQuery):
    data = query.data.split()
    if len(data) == 1:
        return await query.answer(url=f"https://t.me/{app.username}?start=help")

    if data[1] == "back":
        return await query.edit_message_text(
            text=query.lang["help_menu"], reply_markup=buttons.help_markup(
                query.lang)
        )
    elif data[1] == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.message.reply_to_message.delete()
        except Exception:
            pass
        return

    await query.edit_message_text(
        text=query.lang[f"help_{data[1]}"],
        reply_markup=buttons.help_markup(query.lang, True),
    )


@app.on_callback_query(filters.regex("playmode") & ~app.bl_users)
@lang.language()
@admin_check
async def _playmode(_, query: types.CallbackQuery):
    await query.answer(query.lang["processing"], show_alert=True)
    chat_id = query.message.chat.id
    admin_only = await db.get_play_mode(chat_id)
    _language = "en"
    await db.set_play_mode(chat_id, admin_only)
    await query.edit_message_reply_markup(
        reply_markup=buttons.settings_markup(
            query.lang,
            not admin_only,
            _language,
            chat_id,
        )
    )
    # ==============================================================================
# iquery.py - Inline Query Handler
# ==============================================================================
# This plugin handles inline mode queries (@botname <query>).
#
# Features:
# - Search YouTube videos in inline mode
# - Display up to 15 results
# - Show thumbnails, duration, views, channel info
# - Users can select a video to share in any chat
#
# Usage: @HasiiMusicBot search query
# ==============================================================================

from py_yt import VideosSearch
from pyrogram import types

from HasiiMusic import app
from HasiiMusic.helpers import buttons


@app.on_inline_query(~app.bl_users)
async def inline_query_handler(_, query: types.InlineQuery):
    text = query.query.strip().lower()
    if not text:
        return

    try:
        search = VideosSearch(text, limit=15)
        results = (await search.next()).get("result", [])

        answers = []
        for video in results:
            title = video.get("title", "Unknown Title").title()
            duration = video.get("duration", "N/A")
            views = video.get("viewCount", {}).get("short", "N/A")
            thumbnail = video.get("thumbnails", [{}])[
                0].get("url", "").split("?")[0]
            channel = video.get("channel", {}).get("name", "Unknown Channel")
            channellink = video.get("channel", {}).get(
                "link", "https://youtube.com")
            link = video.get("link", "https://youtube.com")
            published = video.get("publishedTime", "N/A")

            description = f"{views} | {duration} | {channel} | {published}"
            caption = (
                f"<b>Title:</b> <a href='{link}'>{title[:250]}</a>\n\n"
                f"<b>Duration:</b> {duration}\n"
                f"<b>Views:</b> <code>{views}</code>\n"
                f"<b>Channel:</b> <a href='{channellink}'>{channel}</a>\n"
                f"<b>Published:</b> {published}\n\n"
                f"<u><i>Fetched by {app.name}</i></u>"
            )

            answers.append(
                types.InlineQueryResultPhoto(
                    photo_url=thumbnail,
                    title=title,
                    description=description,
                    caption=caption,
                    reply_markup=buttons.yt_key(link),
                )
            )

        if answers:
            await app.answer_inline_query(query.id, results=answers, cache_time=5)
    except:
        pass
# ==============================================================================
# misc.py - Miscellaneous Event Handlers
# ==============================================================================
# This plugin handles various bot events and background tasks.
#
# Events:
# - Voice chat started/ended - Auto-stop playback
# - Bot mentioned - Send info message
# - Auto-leave - Remove inactive assistants from groups every 30 minutes
#
# Features:
# - Automatic cleanup of inactive voice chat sessions
# - Bot promotion reminders
# - Keep assistants from cluttering unused groups
# ==============================================================================

import asyncio
import time

from pyrogram import enums, filters, types

from HasiiMusic import tune, app, config, db, lang, logger, queue, tasks, userbot, yt
from HasiiMusic.helpers import buttons


@app.on_message(filters.video_chat_started, group=19)
@app.on_message(filters.video_chat_ended, group=20)
async def _watcher_vc(_, m: types.Message):
    await tune.stop(m.chat.id)


async def auto_leave():
    """Auto-leave inactive groups. Runs in background with error recovery."""
    while True:
        try:
            await asyncio.sleep(1800)
            for ub in userbot.clients:
                left = 0
                try:
                    for dialog in await ub.get_dialogs():
                        chat_id = dialog.chat.id
                        if left >= 20:
                            break
                        # Skip logger and any excluded chats
                        excluded = [app.logger] + config.EXCLUDED_CHATS
                        if chat_id in excluded:
                            continue
                        if dialog.chat.type in [
                            enums.ChatType.GROUP,
                            enums.ChatType.SUPERGROUP,
                        ]:
                            if chat_id in db.active_calls:
                                continue
                            await ub.leave_chat(chat_id)
                            left += 1
                        await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"Auto-leave error for assistant {ub.me.username if hasattr(ub, 'me') and ub.me else 'Unknown'}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Critical error in auto_leave task: {e}")
            await asyncio.sleep(60)  # Wait before retrying
            continue


async def track_time():
    """Track playback time. Runs in background with error recovery."""
    while True:
        try:
            await asyncio.sleep(1)
            for chat_id in list(db.active_calls):
                try:
                    if not await db.playing(chat_id):
                        continue
                    media = queue.get_current(chat_id)
                    if not media:
                        continue
                    media.time += 1
                except Exception as e:
                    # Log error but continue tracking other chats
                    logger.debug(f"track_time error for chat {chat_id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Critical error in track_time task: {e}")
            await asyncio.sleep(1)  # Brief pause before retrying
            continue


async def update_timer(length=10):
    """Update progress bar every 20 seconds for all active chats independently."""
    chat_tasks = {}  # Track individual chat update tasks

    async def _preload_next(chat_id, next_media):
        """Pre-download next song without blocking timer updates."""
        try:
            next_media.file_path = await yt.download(next_media.id)
        except Exception as e:
            print(f"Preload error for chat {chat_id}: {e}")

    async def update_chat_timer(chat_id):
        """Update timer for a specific chat every 20 seconds."""
        while True:
            try:
                await asyncio.sleep(20)

                # Check if chat is still active and playing
                if chat_id not in db.active_calls or not await db.playing(chat_id):
                    break

                media = queue.get_current(chat_id)
                if not media:
                    break

                # Ensure media.time is initialized
                if not hasattr(media, 'time') or media.time is None:
                    media.time = 0

                duration, message_id = media.duration_sec, media.message_id
                if not duration or not message_id:
                    continue

                played = media.time
                remaining = duration - played
                # Generate progress bar with original style
                bar_length = 12
                if duration == 0:
                    percentage = 0
                else:
                    percentage = min((played / duration) * 100, 100)
                filled = int(round(bar_length * percentage / 100))
                timer_bar = "—" * filled + "●" + "—" * (bar_length - filled)

                # Pre-download next song if needed (don't block timer update)
                if remaining <= 30:
                    next = queue.get_next(chat_id, check=True)
                    if next and not next.file_path:
                        asyncio.create_task(_preload_next(chat_id, next))

                if remaining < 10:
                    remove = True
                    timer_text = timer_bar
                else:
                    remove = False
                    # Format time properly with hours support
                    if duration >= 3600:
                        played_time = time.strftime('%H:%M:%S', time.gmtime(played))
                        total_time = time.strftime('%H:%M:%S', time.gmtime(duration))
                    else:
                        played_time = time.strftime('%M:%S', time.gmtime(played))
                        total_time = time.strftime('%M:%S', time.gmtime(duration))
                    timer_text = f"{played_time} {timer_bar} {total_time}"

                await app.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=buttons.controls(
                        chat_id=chat_id, timer=timer_text, remove=remove),
                )
            except Exception as e:
                error_str = str(e)
                # Silently ignore expected Telegram API errors
                if not any(err in error_str for err in [
                    "MESSAGE_NOT_MODIFIED",
                    "MESSAGE_ID_INVALID",
                    "MESSAGE_DELETE",
                    "MESSAGE_AUTHOR_REQUIRED",
                    "CHAT_ADMIN_REQUIRED",
                    "CHANNEL_PRIVATE",
                    "haven't joined this channel"
                ]):
                    print(f"update_timer error for chat {chat_id}: {e}")
                # Stop tracking chats with CHANNEL_PRIVATE errors
                if "CHANNEL_PRIVATE" in error_str:
                    break
                await asyncio.sleep(1)  # Brief pause before retry

    # Monitor and spawn individual chat timers
    while True:
        await asyncio.sleep(2)  # Check for new chats every 2 seconds

        for chat_id in list(db.active_calls):
            # Start timer for new active chats
            if chat_id not in chat_tasks:
                task = asyncio.create_task(update_chat_timer(chat_id))
                chat_tasks[chat_id] = task

        # Clean up finished tasks
        finished_chats = [
            chat_id for chat_id, task in chat_tasks.items()
            if task.done() or chat_id not in db.active_calls
        ]
        for chat_id in finished_chats:
            chat_tasks.pop(chat_id, None)


async def vc_watcher(sleep=15):
    """Leave voice chat after 5 minutes if no users are listening."""
    alone_times = {}  # Track when assistant started being alone in VC
    LEAVE_TIMEOUT = 300  # 5 minutes in seconds (hardcoded)
    
    while True:
        await asyncio.sleep(sleep)
        current_time = time.time()
        
        for chat_id in list(db.active_calls):
            try:
                # Check if auto-leave is enabled for this chat
                if not await db.get_autoleave(chat_id):
                    alone_times.pop(chat_id, None)
                    continue
                
                client = await db.get_assistant(chat_id)
                
                # Check if userbot is actually in the call
                try:
                    participants = await client.get_participants(chat_id)
                except Exception as call_err:
                    # Userbot is not in the call or call doesn't exist
                    # Remove from tracking and continue
                    alone_times.pop(chat_id, None)
                    continue
                
                # Check if only assistant is in VC (participants < 2 means only assistant)
                if len(participants) < 2:
                    # Start tracking alone time
                    if chat_id not in alone_times:
                        alone_times[chat_id] = current_time
                    else:
                        # Check if alone for 5 minutes
                        alone_duration = current_time - alone_times[chat_id]
                        if alone_duration >= LEAVE_TIMEOUT:
                            _lang = await lang.get_lang(chat_id)
                            try:
                                current_media = queue.get_current(chat_id)
                                if current_media and current_media.message_id:
                                    sent = await app.edit_message_reply_markup(
                                        chat_id=chat_id,
                                        message_id=current_media.message_id,
                                        reply_markup=buttons.controls(
                                            chat_id=chat_id, status=_lang["stopped"], remove=True
                                        ),
                                    )
                                    await sent.reply_text(_lang["auto_left"])
                            except:
                                pass
                            
                            # Stop playback and leave
                            await tune.stop(chat_id)
                            try:
                                await client.leave_call(chat_id, close=False)
                            except Exception as e:
                                # Suppress expected call disconnection errors
                                error_msg = str(e).lower()
                                if not any(ignore in error_msg for ignore in [
                                    "not in a call",
                                    "not in the group call",
                                    "no active group call",
                                    "call was already stopped",
                                    "call already disconnected"
                                ]):
                                    print(f"Error leaving call for {chat_id}: {e}")
                            alone_times.pop(chat_id, None)
                else:
                    # Reset timer if users join
                    alone_times.pop(chat_id, None)
                    
            except Exception as e:
                print(f"vc_watcher error for chat {chat_id}: {e}")
                alone_times.pop(chat_id, None)
                continue


# Always run VC watcher to check for empty voice chats
tasks.append(asyncio.create_task(vc_watcher()))
if config.AUTO_LEAVE:
    tasks.append(asyncio.create_task(auto_leave()))
tasks.append(asyncio.create_task(track_time()))
tasks.append(asyncio.create_task(update_timer()))
# ==============================================================================
# new_chat.py - New Group Handler
# ==============================================================================
# This plugin handles when the bot is added to a new group.
#
# Features:
# - Send notification to logger channel with group info
# - Welcome message in the group
# - Check if group meets requirements (supergroup, admin permissions)
# - Log group details (name, ID, member count, who added the bot)
# ==============================================================================

from pyrogram import filters, types
from pyrogram.errors import ChatAdminRequired

from HasiiMusic import app, config


@app.on_message(filters.new_chat_members & filters.group)
async def new_chat_member(_, message: types.Message):
    """Handler for when bot is added to a new group"""

    # Check if the bot itself was added
    for member in message.new_chat_members:
        if member.id == app.id:
            chat = message.chat

            # Get chat information
            chat_name = chat.title
            chat_id = chat.id
            chat_username = f"@{chat.username}" if chat.username else "ᴘʀɪᴠᴀᴛᴇ ɢʀᴏᴜᴘ"
            members_count = await app.get_chat_members_count(chat_id)

            # Get the user who added the bot
            added_by = message.from_user
            added_by_name = added_by.mention if added_by else "ᴜɴᴋɴᴏᴡɴ"

            # Create the formatted message with blockquote
            text = f"""<blockquote>🟢 <b>˹˹ʜᴀꜱɪɪ ꭙ ᴍᴜꜱɪᴄ˼ ᴀᴅᴅᴇᴅ ɪɴ ᴀ ɴᴇᴡ ɢʀᴏᴜᴘ</b></blockquote>

<blockquote>
🔖 <b>ᴄʜᴀᴛ ɴᴀᴍᴇ:</b> {chat_name}
🆔 <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{chat_id}</code>
👤 <b>ᴄʜᴀᴛ ᴜꜱᴇʀɴᴀᴍᴇ:</b> {chat_username}
🔗 <b>ᴄʜᴀᴛ ʟɪɴᴋ:</b> {f"https://t.me/{chat.username}" if chat.username else "ᴄʟɪᴄᴋ ʜᴇʀᴇ"}
👥 <b>ɢʀᴏᴜᴘ ᴍᴇᴍʙᴇʀs:</b> {members_count}
🤵 <b>ᴀᴅᴅᴇᴅ ʙʏ:</b> {added_by_name}
</blockquote>
"""

            try:
                # Send the notification to the logger group
                await app.send_photo(
                    chat_id=config.LOGGER_ID,
                    photo=config.START_IMG,
                    caption=text
                )
            except Exception as e:
                print(f"Failed to send new chat notification: {e}")

            break


@app.on_message(filters.left_chat_member & filters.group)
async def left_chat_member(_, message: types.Message):
    """Handler for when bot is removed from a group"""

    # Check if the bot itself was removed
    if message.left_chat_member.id == app.id:
        chat = message.chat

        # Get chat information
        chat_name = chat.title
        chat_id = chat.id
        chat_username = f"@{chat.username}" if chat.username else "ᴘʀɪᴠᴀᴛᴇ ɢʀᴏᴜᴘ"

        # Get the user who removed the bot
        removed_by = message.from_user
        removed_by_name = removed_by.mention if removed_by else "ᴜɴᴋɴᴏᴡɴ"

        # Create the formatted message with blockquote
        text = f"""<blockquote>🔴 <b>˹ʜᴀꜱɪɪ ꭙ ᴍᴜꜱɪᴄ˼ ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ᴀ ɢʀᴏᴜᴘ</b></blockquote>

<blockquote>
🔖 <b>ᴄʜᴀᴛ ɴᴀᴍᴇ:</b> {chat_name}
🆔 <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{chat_id}</code>
👤 <b>ᴄʜᴀᴛ ᴜꜱᴇʀɴᴀᴍᴇ:</b> {chat_username}
🔗 <b>ᴄʜᴀᴛ ʟɪɴᴋ:</b> {f"https://t.me/{chat.username}" if chat.username else "ᴄʟɪᴄᴋ ʜᴇʀᴇ"}
🚫 <b>ʀᴇᴍᴏᴠᴇᴅ ʙʏ:</b> {removed_by_name}</blockquote>
"""

        try:
            # Send the notification to the logger group
            await app.send_photo(
                chat_id=config.LOGGER_ID,
                photo=config.START_IMG,
                caption=text
            )
        except Exception as e:
            print(f"Failed to send left chat notification: {e}")
# ==============================================================================
# adminmention.py - Admin Mention Plugin
# ==============================================================================
# This plugin allows users to mention all group admins by typing @admin,
# .admin, or /admin followed by a message.
#
# Commands:
# - @admin [message] - Mention all admins with a message
# - .admin [message] - Mention all admins with a message
# - /admin [message] - Mention all admins with a message
#
# Requirements:
# - Bot must have permission to read messages in the group
# ==============================================================================

import re
from pyrogram import filters, types, enums

from HasiiMusic import app


# =============================================================================
# CONFIGURATION: Add usernames here to exclude from admin mentions (without @)
# =============================================================================
EXCLUDED_USERNAMES = [
    "Hasindu_Lakshan",
    "Itz_Me_Rana_o_O",
    "zqxiro",
    "Dark_Ryker"
]

# Pattern to detect admin triggers
TRIGGER_PATTERN = re.compile(r"(?i)(\.|@|\/)admin")


@app.on_message(filters.group & filters.regex(r"(?i)(\.|@|\/)admin"))
async def mention_admins(_, message: types.Message):
    """
    Mention all group admins when someone types @admin, .admin, or /admin
    """
    try:
        # Extract the message without the trigger
        message_text = message.text or message.caption or ""
        cleaned_text = TRIGGER_PATTERN.sub("", message_text).strip()

        # Get user info (handle anonymous admins)
        sender = message.from_user
        if sender:
            user_display = f"{sender.first_name}"
            if sender.username:
                user_display += f" (@{sender.username})"
        else:
            # Anonymous admin or channel
            user_display = "ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ"

        # Build formatted reply message
        if cleaned_text:
            reply_msg = (
                f"<blockquote><b><i>\"{cleaned_text}\"</i></b>\n"
                f"ʀᴇᴘᴏʀᴛᴇᴅ ʙʏ: {user_display} 🔔</blockquote>\n\n"
            )
        else:
            reply_msg = (
                f"<blockquote>ʀᴇᴘᴏʀᴛᴇᴅ ʙʏ: {user_display} 🔔</blockquote>\n\n"
            )

        # Get all administrators
        mentions = []
        try:
            async for admin in app.get_chat_members(
                message.chat.id,
                filter=enums.ChatMembersFilter.ADMINISTRATORS
            ):
                user = admin.user

                # Skip bots and deleted accounts
                if user.is_bot or user.is_deleted:
                    continue

                # Skip admins who have "Remain Anonymous" enabled
                # Check privileges - if is_anonymous privilege is True, skip them
                if hasattr(admin, 'privileges') and admin.privileges:
                    if getattr(admin.privileges, 'is_anonymous', False):
                        continue

                # Skip usernames in the excluded list
                if user.username and user.username.lower() in [u.lower() for u in EXCLUDED_USERNAMES]:
                    continue

                # Add mention
                if user.username:
                    mentions.append(f"@{user.username}")
                else:
                    # Use HTML link format to mention users without username
                    mentions.append(
                        f"<a href='tg://user?id={user.id}'>{user.first_name}</a>")
        except Exception as e:
            await message.reply_text(
                "<blockquote>❌ Failed to fetch administrators. Make sure the bot has proper permissions.</blockquote>"
            )
            return

        if mentions:
            reply_msg += ", ".join(mentions)
        else:
            reply_msg += "<i>No visible human admins found to mention.</i>"

        # Send the reply
        try:
            await message.reply_text(reply_msg, disable_web_page_preview=True)
        except Exception as e:
            await message.reply_text(
                "<blockquote>❌ Failed to send admin notification.</blockquote>"
            )
    except Exception as e:
        # Catch all exceptions to prevent bot crashes
        try:
            await message.reply_text("<blockquote>❌ An error occurred while processing admin mention.</blockquote>")
        except:
            pass  # Silent failure if reply fails
from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatMembersFilter, ParseMode

from HasiiMusic import app


@app.on_message(filters.command("bots") & filters.group)
async def list_bots(client, message: Message):
    """List all bots in the current group"""
    
    try:
        bot_list = []
        bot_count = 0
        
        # Send initial message
        status_msg = await message.reply_text("🔍 <b>Scanning for bots...</b>", parse_mode=ParseMode.HTML)
        
        # Iterate through all members and filter bots
        async for member in client.get_chat_members(message.chat.id, filter=ChatMembersFilter.BOTS):
            bot_count += 1
            bot_username = f"@{member.user.username}" if member.user.username else "No Username"
            bot_list.append(f"{bot_count}. <a href='tg://user?id={member.user.id}'>{member.user.first_name}</a> - {bot_username}")
        
        if bot_count == 0:
            await status_msg.edit_text("❌ <b>No bots found in this group.</b>", parse_mode=ParseMode.HTML)
            return
        
        # Format the response
        response = f"🤖 <b>Bots in {message.chat.title}</b>\n\n"
        response += "<blockquote>" + "\n".join(bot_list) + "</blockquote>"
        response += f"\n\n📊 <b>Total Bots:</b> {bot_count}"
        
        await status_msg.edit_text(response, disable_web_page_preview=True, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        await message.reply_text(f"⚠️ <b>Error:</b> {str(e)}", parse_mode=ParseMode.HTML)
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode, ChatMemberStatus, ChatMembersFilter
from pyrogram.types import Message

from HasiiMusic import app


@app.on_message(filters.command(["groupdata", "chatinfo", "groupinfo"]) & filters.group)
async def group_data_handler(client: Client, message: Message):
    """Display comprehensive information about the current group"""
    
    chat = message.chat
    chat_id = chat.id
    
    try:
        # Get chat information
        chat_info = await client.get_chat(chat_id)
        
        # Count members by type
        total_members = 0
        admin_count = 0
        bot_count = 0
        banned_count = 0
        deleted_count = 0
        premium_count = 0
        
        try:
            total_members = await client.get_chat_members_count(chat_id)
            
            # Count admins
            async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                admin_count += 1
            
            # Count bots
            async for _ in client.get_chat_members(chat_id, filter=ChatMembersFilter.BOTS):
                bot_count += 1
                
            # Count banned users
            try:
                async for _ in client.get_chat_members(chat_id, filter=ChatMembersFilter.BANNED):
                    banned_count += 1
            except Exception:
                pass
            
            # Iterate through recent members to count deleted accounts and premium users
            try:
                member_sample = 0
                async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.SEARCH, limit=200):
                    member_sample += 1
                    if member.user.is_deleted:
                        deleted_count += 1
                    if member.user.is_premium:
                        premium_count += 1
            except Exception:
                pass
                
        except Exception:
            pass
        
        # Build information text
        info_lines = []
        info_lines.append("<b>📊 GROUP INFORMATION</b>\n")
        
        # Basic info
        info_lines.append(f"<b>📌 ɴᴀᴍᴇ:</b> {chat_info.title}")
        info_lines.append(f"<b>🆔 ɪᴅ:</b> <code>{chat_id}</code>")
        
        if chat_info.username:
            info_lines.append(f"<b>🔗 ᴜꜱᴇʀɴᴀᴍᴇ:</b> @{chat_info.username}")
        
        # Chat type
        chat_type_str = "ɢʀᴏᴜᴘ" if chat.type == ChatType.GROUP else "ꜱᴜᴘᴇʀɢʀᴏᴜᴘ"
        info_lines.append(f"<b>📂 ᴛʏᴘᴇ:</b> {chat_type_str}")
        
        # Member statistics
        info_lines.append(f"\n<b>👥 ᴍᴇᴍʙᴇʀꜱ:</b> {total_members}")
        info_lines.append(f"<b>👮 ᴀᴅᴍɪɴꜱ:</b> {admin_count}")
        info_lines.append(f"<b>🤖 ʙᴏᴛꜱ:</b> {bot_count}")
        
        if banned_count > 0:
            info_lines.append(f"<b>🚫 ʙᴀɴɴᴇᴅ:</b> {banned_count}")
        
        if deleted_count > 0:
            info_lines.append(f"<b>👻 ᴅᴇʟᴇᴛᴇᴅ ᴀᴄᴄᴏᴜɴᴛꜱ:</b> {deleted_count}")
            
        if premium_count > 0:
            info_lines.append(f"<b>⭐ ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀꜱ:</b> {premium_count}")
        
        # Description if available
        if chat_info.description:
            desc = chat_info.description
            if len(desc) > 100:
                desc = desc[:100] + "..."
            info_lines.append(f"\n<b>📝 ᴅᴇꜱᴄʀɪᴘᴛɪᴏɴ:</b>\n{desc}")
        
        # Linked chat if available
        if chat_info.linked_chat:
            info_lines.append(f"\n<b>🔗 ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ:</b> {chat_info.linked_chat.title}")
            info_lines.append(f"<b>🆔 ᴄʜᴀɴɴᴇʟ ɪᴅ:</b> <code>{chat_info.linked_chat.id}</code>")
        
        # Invite link if available
        if hasattr(chat_info, 'invite_link') and chat_info.invite_link:
            info_lines.append(f"\n<b>🔗 ɪɴᴠɪᴛᴇ ʟɪɴᴋ:</b> {chat_info.invite_link}")
        
        # Check user's admin status
        try:
            user_member = await client.get_chat_member(chat_id, message.from_user.id)
            if user_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                info_lines.append(f"\n<b>🔐 ʏᴏᴜʀ ʀᴏʟᴇ:</b> {'ᴏᴡɴᴇʀ' if user_member.status == ChatMemberStatus.OWNER else 'ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ'}")
        except Exception:
            pass
        
        # Combine all info
        response = "<blockquote>" + "\n".join(info_lines) + "</blockquote>"
        
        await message.reply_text(
            response,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        
    except Exception as e:
        await message.reply_text(
            f"<blockquote>❌ <b>ᴇʀʀᴏʀ ɢᴇᴛᴛɪɴɢ ɢʀᴏᴜᴘ ᴅᴀᴛᴀ:</b>\n<code>{str(e)}</code></blockquote>",
            parse_mode=ParseMode.HTML
        )
from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatType, ParseMode
from pyrogram.errors import ChatSendPlainForbidden, ChatWriteForbidden, Forbidden, ChannelPrivate

from HasiiMusic import app


async def _safe_reply_text(message: Message, text: str):
    """Safely send reply text with error handling"""
    chat = getattr(message, "chat", None)
    if not chat or chat.type == ChatType.CHANNEL:
        return
    try:
        await message.reply_text(text, parse_mode=ParseMode.HTML)
    except (ChatSendPlainForbidden, ChatWriteForbidden, Forbidden, ChannelPrivate):
        pass
    except Exception:
        pass


@app.on_message(filters.video_chat_started & filters.group)
async def on_voice_chat_started(_, message: Message):
    """Handler for voice chat started event"""
    await _safe_reply_text(message, "🎙 <b>ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ʜᴀs sᴛᴀʀᴛᴇᴅ!</b>")
    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.video_chat_ended & filters.group)
async def on_voice_chat_ended(_, message: Message):
    """Handler for voice chat ended event"""
    await _safe_reply_text(message, "🔕 <b>ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴇɴᴅᴇᴅ.</b>")
    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.video_chat_members_invited & filters.group)
async def on_voice_chat_members_invited(_, message: Message):
    """Handler for when members are invited to voice chat"""
    inviter = "Someone"
    if message.from_user:
        try:
            inviter = message.from_user.mention
        except Exception:
            inviter = message.from_user.first_name or "Someone"

    invited = []
    vcmi = getattr(message, "video_chat_members_invited", None)
    users = getattr(vcmi, "users", []) if vcmi else []
    
    for user in users:
        try:
            name = user.first_name or "User"
            invited.append(f"<a href='tg://user?id={user.id}'>{name}</a>")
        except Exception:
            continue

    if invited:
        await _safe_reply_text(
            message,
            f"👥 {inviter} <b>ɪɴᴠɪᴛᴇᴅ</b> {', '.join(invited)} <b>ᴛᴏ ᴛʜᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ.</b> 😉",
        )
        try:
            await message.delete()
        except Exception:
            pass
# ==============================================================================
# active.py - Active Voice Chats Command (Sudo Only)
# ==============================================================================
# This plugin shows statistics about active voice chats where the bot is playing.
#
# Commands:
# - /ac - Show count of active voice chats
# - /activevc - Show detailed list of active chats with currently playing track
#
# Only sudo users can use these commands.
# ==============================================================================

import os
from pyrogram import filters, types
from HasiiMusic import app, db, lang, queue


@app.on_message(filters.command(["ac", "activevc"]) & app.sudo_filter)
@lang.language()
async def _activevc(_, m: types.Message):
    if not db.active_calls:
        return await m.reply_text(m.lang["vc_empty"])

    if m.command[0] == "ac":
        return await m.reply_text(m.lang["vc_count"].format(len(db.active_calls)))

    sent = await m.reply_text(m.lang["vc_fetching"])
    text = ""

    for i, chat in enumerate(db.active_calls):
        playing = queue.get_current(chat)
        if playing:
            text += f"\n{i+1}. <code>{chat}</code>\n    ➜ {playing.title[:25]}"

    if len(text) < 4000:
        return await sent.edit_text(m.lang["vc_list"] + text)

    with open("activevc.txt", "w") as f:
        f.write(text)

    try:
        await sent.edit_media(
            media=types.InputMediaDocument(
                media="activevc.txt",
                caption=m.lang["vc_list"],
            )
        )
    finally:
        os.remove("activevc.txt")
# ==============================================================================
# ping.py - Ping/Alive Command
# ==============================================================================
# This plugin shows bot status and performance metrics.
#
# Commands:
# - /ping - Show bot latency and system stats
# - /alive - Same as /ping
#
# Displays:
# - Response latency
# - Uptime
# - CPU usage
# - RAM usage
# - Disk usage
# - Voice call latency
# ==============================================================================

import time
import psutil

from pyrogram import filters, types
from HasiiMusic import app, tune, boot, config, lang
from HasiiMusic.helpers import buttons


@app.on_message(filters.command(["alive", "ping"]) & ~app.bl_users)
@lang.language()
async def _ping(_, m: types.Message):
    start = time.time()
    sent = await m.reply_text(m.lang["pinging"])

    def get_time(s): return (lambda r: (f"{r[-1]}, " if r[-1][:-4] != "0" else "") + ":".join(reversed(r[:-1])))(
        [f"{v}{u}" for v, u in zip([s % 60, (s//60) % 60, (s//3600) % 24, s//86400], ["s", "m", "h", "days"])])
    uptime = get_time(int(time.time() - boot))
    latency = round((time.time() - start) * 1000, 2)
    await sent.edit_media(
        media=types.InputMediaPhoto(
            media=config.PING_IMG,
            caption=m.lang["ping_pong"].format(
                latency,
                uptime,
                await tune.ping(),
            )
        ),
        reply_markup=buttons.ping_markup(m.lang["support"]),
    )
# ==============================================================================
# start.py - Start Command and Basic Bot Interactions
# ==============================================================================
# This plugin handles:
# - /start command (welcome message for new users)
# - /help command (show help menu)
# - /playmode or /settings command (group settings)
# - New member detection (when bot joins a group)
# ==============================================================================

from pyrogram import enums, errors, filters, types

from HasiiMusic import app, config, db, lang
from HasiiMusic.helpers import buttons, utils


@app.on_message(filters.command(["help"]) & filters.private & ~app.bl_users)
@lang.language()
async def _help(_, m: types.Message):
    """Handle /help command in private chats - shows help menu."""
    await m.reply_text(
        text=m.lang["help_menu"],
        reply_markup=buttons.help_markup(m.lang),
        quote=True,
    )


@app.on_message(filters.command(["start"]))
@lang.language()
async def start(_, message: types.Message):
    """
    Handle /start command - welcome message for users.

    - In private chat: Shows welcome message with inline buttons
    - In group chat: Shows short welcome message
    - Adds new users to database
    - Sends log to logger group for new users
    """
    # Skip if message from channel or anonymous admin
    if not message.from_user:
        return

    # Check if user is blacklisted
    if message.from_user.id in app.bl_users and message.from_user.id not in db.notified:
        return await message.reply_text(message.lang["bl_user_notify"])

    # If /start help, show help menu
    if len(message.command) > 1 and message.command[1] == "help":
        return await _help(_, message)

    # Determine if chat is private or group
    private = message.chat.type == enums.ChatType.PRIVATE

    # Choose appropriate welcome message
    _text = (
        message.lang["start_pm"].format(message.from_user.first_name, app.name)
        if private
        else message.lang["start_gp"].format(app.name)
    )

    key = buttons.start_key(message.lang, private)
    try:
        await message.reply_photo(
            photo=config.START_IMG,
            caption=_text,
            reply_markup=key,
            quote=not private,
        )
    except errors.ChatSendPhotosForbidden:
        # If photos are not allowed, send text only
        await message.reply_text(
            text=_text,
            reply_markup=key,
            quote=not private,
        )

    # For private chats, add user to database if new
    if private:
        if await db.is_user(message.from_user.id):
            return  # User already exists, no need to add
        # Log new user to logger group
        await utils.send_log(message)
        # Add user to database
        return await db.add_user(message.from_user.id)


@app.on_message(filters.command(["playmode", "settings"]) & filters.group & ~app.bl_users)
@lang.language()
async def settings(_, message: types.Message):
    """
    Handle /playmode or /settings command - show group settings.

    Displays:
    - Play mode (everyone or admin only)
    - Current language
    - Options to change settings
    """
    admin_only = await db.get_play_mode(message.chat.id)  # Get play mode setting
    _language = "en"
    await message.reply_text(
        text=message.lang["start_settings"].format(message.chat.title),
        reply_markup=buttons.settings_markup(
            message.lang, admin_only, _language, message.chat.id
        ),
        quote=True,
    )


@app.on_message(filters.new_chat_members, group=7)
@lang.language()
async def _new_member(_, message: types.Message):
    """
    Handle new member events - detect when bot is added to groups.

    - Leaves non-supergroup chats
    - Adds new groups to database
    """
    # Only work in supergroups (not basic groups)
    if message.chat.type != enums.ChatType.SUPERGROUP:
        return await message.chat.leave()

    # Check each new member
    for member in message.new_chat_members:
        if member.id == app.id:  # Bot itself was added
            if await db.is_chat(message.chat.id):
                return  # Chat already in database
            # Add chat to database (log is sent from new_chat.py with photo)
            await db.add_chat(message.chat.id)
# ==============================================================================
# stats.py - Bot Statistics Command (Sudo Only)
# ==============================================================================
# This plugin shows comprehensive bot statistics and system information.
#
# Commands:
# - /stats - Show detailed bot statistics
#
# Displays:
# - Total users and groups using the bot
# - System info (OS, Python version, Pyrogram version)
# - Bot uptime
# - Memory and CPU usage
# - Number of loaded plugins
#
# Only sudo users can use this command.
# ==============================================================================

# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
import platform
import sys

import psutil
from pyrogram import __version__, filters, types
from pytgcalls import __version__ as pytgver

from HasiiMusic import app, config, db, lang, userbot
from HasiiMusic.plugins import all_modules


@app.on_message(filters.command(["stats"]) & ~app.bl_users)
@lang.language()
async def _stats(_, m: types.Message):
    # Check if user is sudo
    if m.from_user.id not in app.sudoers:
        return
    
    sent = await m.reply_photo(
        photo=config.PING_IMG,
        caption=m.lang["stats_fetching"],
    )

    pid = os.getpid()
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count()
    
    # Get memory info
    mem = psutil.virtual_memory()
    used_mem = round(mem.used / (1024 ** 3), 2)  # Convert to GB
    total_mem = round(mem.total / (1024 ** 3), 2)
    
    # Get disk info
    disk = psutil.disk_usage("/")
    used_disk = round(disk.used / (1024 ** 3), 2)  # Convert to GB
    total_disk = round(disk.total / (1024 ** 3), 2)
    
    _utext = m.lang["stats_user"].format(
        app.name,
        len(userbot.clients),
        config.AUTO_LEAVE,
        len(db.blacklisted),
        len(app.bl_users),
        len(app.sudoers),
        len(await db.get_chats()),
        len(await db.get_users()),
    )
    
    # Add system stats for sudo users
    _utext += m.lang["stats_sudo"].format(
        len(all_modules),
        platform.system(),
        f"{used_mem}GB | {total_mem}GB",
        f"{cpu_percent}% ({cpu_count} cores)",
        f"{used_disk}GB | {total_disk}GB",
        sys.version.split()[0],
        __version__,
        pytgver,
    )
    
    await sent.edit_caption(_utext)

# HasiiMusic/plugins/playback-controls
# ==============================================================================
# loop.py - Loop Mode Command
# ==============================================================================
# This plugin handles loop mode management.
#
# Commands:
# - /loop - Cycle through loop modes (off -> single -> queue -> off)
# - /loop off - Disable loop
# - /loop single - Loop current track
# - /loop queue - Loop entire queue
#
# Requirements:
# - User must be admin or authorized user
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import app, db, lang
from HasiiMusic.helpers import can_manage_vc


@app.on_message(filters.command(["loop"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _loop(_, m: types.Message):
    current_loop = await db.get_loop(m.chat.id)
    
    # Check if user specified a mode
    if len(m.command) > 1:
        mode_arg = m.command[1].lower()
        if mode_arg in ["off", "0", "disable"]:
            new_loop = 0
            text = "➡️ Loop mode **disabled**"
        elif mode_arg in ["single", "1", "one"]:
            new_loop = 1
            text = "🔂 Loop mode set to **Single Track**"
        elif mode_arg in ["queue", "all", "10"]:
            new_loop = 10
            text = "🔁 Loop mode set to **Queue**"
        else:
            return await m.reply_text(
                "**Usage:**\n"
                "• `/loop` - Cycle through modes\n"
                "• `/loop off` - Disable loop\n"
                "• `/loop single` - Loop current track\n"
                "• `/loop queue` - Loop entire queue"
            )
    else:
        # Cycle through modes
        if current_loop == 0:
            new_loop = 1
            text = "🔂 Loop mode set to **Single Track**"
        elif current_loop == 1:
            new_loop = 10
            text = "🔁 Loop mode set to **Queue**"
        else:
            new_loop = 0
            text = "➡️ Loop mode **disabled**"
    
    await db.set_loop(m.chat.id, new_loop)
    await m.reply_text(text)
# ==============================================================================
# pause.py - Pause Playback Command
# ==============================================================================
# This plugin handles pausing the current voice chat playback.
#
# Commands:
# - /pause - Pause current playback
#
# Requirements:
# - User must be admin or authorized user
# - Music must be currently playing
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import tune, app, db, lang
from HasiiMusic.helpers import buttons, can_manage_vc


@app.on_message(filters.command(["pause"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _pause(_, m: types.Message):
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    if not await db.playing(m.chat.id):
        return await m.reply_text(m.lang["play_already_paused"])

    await tune.pause(m.chat.id)
    await m.reply_text(
        text=m.lang["play_paused"].format(m.from_user.mention),
        reply_markup=buttons.controls(m.chat.id),
    )
# ==============================================================================
# play.py - Main Play Command Handler
# ==============================================================================
# This is the core plugin that handles all play-related commands:
# - /play <query> - Play audio from YouTube search or URL
# - /playforce - Force play (skip queue and play immediately)
# - /cplay - Play in connected channel
# 
# Supports:
# - YouTube search queries
# - YouTube URLs (videos and playlists)
# - Telegram audio files (via reply)
# - Queue management
# - Channel play mode
# ==============================================================================

from pyrogram import filters
from pyrogram import types
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageDeleteForbidden

from HasiiMusic import tune, app, config, db, lang, queue, tg, yt
from HasiiMusic.helpers import buttons, utils
from HasiiMusic.helpers._play import checkUB
import asyncio


async def safe_edit(message, text, **kwargs):
    """
    Safely edit a message with proper error handling for common Telegram API errors.
    
    Args:
        message: The message object to edit
        text: New text content
        **kwargs: Additional arguments for edit_text
        
    Returns:
        True if successful, False otherwise
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await message.edit_text(text, **kwargs)
            return True
        except (MessageIdInvalid, MessageDeleteForbidden, Exception):
            return False
    except (MessageIdInvalid, MessageDeleteForbidden):
        # Message was deleted or became invalid - this is expected
        return False
    except Exception:
        # Other errors - log but don't crash
        return False


def playlist_to_queue(chat_id: int, tracks: list) -> str:
    """
    Add multiple tracks to queue and format them as a message.
    
    Args:
        chat_id: The chat ID where queue is managed
        tracks: List of Track objects to add
        
    Returns:
        Formatted string listing all added tracks
    """
    text = "<blockquote expandable>"
    for track in tracks:
        pos = queue.add(chat_id, track)  # Add track to queue (returns 0-based index)
        text += f"<b>{pos}.</b> {track.title}\n"  # Show actual queue position
    text = text[:1948] + "</blockquote>"  # Limit message length
    return text

@app.on_message(
    filters.command(["play", "playforce", "cplay", "cplayforce"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
@checkUB
async def play_hndlr(
    _,
    m: types.Message,
    force: bool = False,
    url: str = None,
    cplay: bool = False,
) -> None:
    # Handle channel play mode
    chat_id = m.chat.id
    if cplay:
        channel_id = await db.get_cmode(m.chat.id)
        if channel_id is None:
            return await m.reply_text(
                "❌ **Channel play is not enabled.**\n\n"
                "**To enable for linked channel:**\n"
                "`/channelplay linked`\n\n"
                "**To enable for any channel:**\n"
                "`/channelplay [channel_id]`"
            )
        try:
            chat = await app.get_chat(channel_id)
            chat_id = channel_id
        except:
            await db.set_cmode(m.chat.id, None)
            return await m.reply_text(
                "❌ **ꜰᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ ᴄʜᴀɴɴᴇʟ.**\n\n"
                "ᴍᴀᴋᴇ ꜱᴜʀᴇ ɪ'ᴍ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ɪꜱ ꜱᴇᴛ ᴄᴏʀʀᴇᴄᴛʟʏ."
            )

    # Select emoji for this play session
    play_emoji = m.lang["play_emoji"]
    
    try:
        sent = await m.reply_text(m.lang["play_searching"].format(play_emoji))
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            sent = await m.reply_text(m.lang["play_searching"].format(play_emoji))
        except FloodWait as e2:
            # If still flood wait, wait longer and give up gracefully
            await asyncio.sleep(e2.value)
            return  # Abort silently
        except Exception:
            return  # Abort silently
    except Exception:
        return  # If we can't even send initial message, abort
    
    mention = m.from_user.mention
    media = tg.get_media(m.reply_to_message) if m.reply_to_message else None
    tracks = []
    file = None  # Initialize file variable

    # Check media first (Telegram files) before URL extraction
    if media:
        setattr(sent, "lang", m.lang)
        file = await tg.download(m.reply_to_message, sent)

    elif url:
        if "playlist" in url:
            await safe_edit(sent, m.lang["playlist_fetch"])
            try:
                tracks = await yt.playlist(
                    config.PLAYLIST_LIMIT, mention, url
                )
            except Exception as e:
                await safe_edit(
                    sent,
                    f"<blockquote>❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ꜰᴇᴛᴄʜ ᴘʟᴀʏʟɪꜱᴛ.\n\n"
                    f"ʏᴏᴜᴛᴜʙᴇ ᴘʟᴀʏʟɪꜱᴛꜱ ᴀʀᴇ ᴄᴜʀʀᴇɴᴛʟʏ ᴇxᴘᴇʀɪᴇɴᴄɪɴɢ ɪꜱꜱᴜᴇꜱ. "
                    f"ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴘʟᴀʏɪɴɢ ɪɴᴅɪᴠɪᴅᴜᴀʟ ꜱᴏɴɢꜱ ɪɴꜱᴛᴇᴀᴅ.</blockquote>"
                )
                return

            if not tracks:
                await safe_edit(sent, m.lang["playlist_error"])
                return

            file = tracks[0]
            tracks.remove(file)
            file.message_id = sent.id
        else:
            file = await yt.search(url, sent.id)

        if not file:
            await safe_edit(
                sent,
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )
            return

    elif len(m.command) >= 2:
        query = " ".join(m.command[1:])
        file = await yt.search(query, sent.id)
        if not file:
            await safe_edit(
                sent,
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )
            return

    if not file:
        return

    # Skip duration check for live streams
    if not file.is_live and file.duration_sec > config.DURATION_LIMIT:
        await safe_edit(
            sent,
            m.lang["play_duration_limit"].format(config.DURATION_LIMIT // 60)
        )
        return

    if await db.is_logger():
        await utils.play_log(m, file.title, file.duration)

    file.user = mention
    if force:
        queue.force_add(chat_id, file)
    else:
        position = queue.add(chat_id, file)  # Returns 0-based index

        if await db.get_call(chat_id):
            # When call is active, position 0 is currently playing
            # So actual waiting position is: position (e.g., 1st waiting = index 1)
            # Display as 1-based for users: index 1 → "1st in queue"
            await safe_edit(
                sent,
                m.lang["play_queued"].format(
                    position,  # Shows waiting position: 1, 2, 3...
                    file.url,
                    file.title,
                    file.duration,
                    m.from_user.mention,
                ),
                reply_markup=buttons.play_queued(
                    chat_id, file.id, m.lang["play_now"]
                ),
            )
            if tracks:
                added = playlist_to_queue(chat_id, tracks)
                try:
                    await app.send_message(
                        chat_id=m.chat.id,
                        text=m.lang["playlist_queued"].format(len(tracks)) + added,
                    )
                except Exception:
                    # Can't send message, continue anyway
                    pass
            
            # ✨ NEW: Start preloading queued tracks in background
            try:
                from HasiiMusic import preload
                asyncio.create_task(preload.start_preload(chat_id, count=2))
            except Exception:
                # Non-critical, continue without preload
                pass
            
            return

    if not file.file_path:
        file.file_path = await yt.download(file.id, is_live=file.is_live)
        if not file.file_path:
            await safe_edit(
                sent,
                "<blockquote>❌ Failed to download media.\n\n"
                "Possible reasons:\n"
                "• YouTube detected bot activity (update cookies)\n"
                "• Video is region-blocked or private\n"
                "• Age-restricted content (requires cookies)</blockquote>"
            )

    try:
        await tune.play_media(chat_id=chat_id, message=sent, media=file)
        # React with emoji on successful play
        try:
            emoji = m.lang["play_emoji"]
            await m.react(emoji)
        except Exception:
            # If reaction fails, continue anyway (not critical)
            pass
    except Exception as e:
        error_msg = str(e)
        if "bot" in error_msg.lower() or "sign in" in error_msg.lower():
            await safe_edit(
                sent,
                "❌ **YouTube bot detection triggered.**\n\n"
                "**Solution:**\n"
                "• Update YouTube cookies in `HasiiMusic/cookies/` folder\n"
                "• Wait a few minutes before trying again\n"
                "• Try /radio for uninterrupted music\n\n"
                f"**Support:** {config.SUPPORT_CHAT}"
            )
        else:
            await safe_edit(
                sent,
                f"❌ **Playback error:**\n{error_msg}\n\n"
                f"**Support:** {config.SUPPORT_CHAT}"
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
                
            )
        return
    if not tracks:
        return
    added = playlist_to_queue(chat_id, tracks)
    try:
        await app.send_message(
            chat_id=m.chat.id,
            text=m.lang["playlist_queued"].format(len(tracks)) + added,
        )
    except Exception:
        # Can't send message, but playback is working
        pass
# ==============================================================================
# queue.py - Queue Display Command
# ==============================================================================
# This plugin displays the current queue and now playing information.
#
# Commands:
# - /queue - Show current queue
# - /playing - Same as /queue
#
# Displays:
# - Currently playing track with thumbnail
# - Track title, duration, user who requested
# - Upcoming tracks in queue (expandable list)
# - Queue length and total duration
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import app, config, db, lang, queue
from HasiiMusic.helpers import Track, buttons, thumb


@app.on_message(filters.command(["queue", "playing"]) & filters.group & ~app.bl_users)
@lang.language()
async def _queue_func(_, m: types.Message):
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    _reply = await m.reply_text(m.lang["queue_fetching"])
    _queue = queue.get_queue(m.chat.id)
    _media = _queue[0]
    _thumb = (
        await thumb.generate(_media)
        if isinstance(_media, Track)
        else config.DEFAULT_THUMB
    )
    _text = m.lang["queue_curr"].format(
        _media.url,
        _media.title[:50],
        _media.duration,
        _media.user,
    )
    _queue.pop(0)

    if _queue:
        _text += "<blockquote expandable>"
        for i, media in enumerate(_queue, start=1):
            if i == 15:
                break
            _text += m.lang["queue_item"].format(
                i, media.title, media.duration  # Show 1, 2, 3... for queued songs
            )
        _text += "</blockquote>"

    _playing = await db.playing(m.chat.id)
    await _reply.edit_media(
        media=types.InputMediaPhoto(
            media=_thumb,
            caption=_text,
        ),
        reply_markup=buttons.queue_markup(
            m.chat.id,
            m.lang["playing"] if _playing else m.lang["paused"],
            _playing,
        ),
    )
Hasindu Lakshan:
# ==============================================================================
# radio.py - Live Radio Streaming Plugin
# ==============================================================================
# This plugin allows users to stream live radio stations in voice chats.
# Features:
# - 50+ international and local radio stations
# - Pagination for easy station selection
# - Live timer display during playback
# - Admin-only controls (skip, close)
# - Support for both regular and channel play modes
# ==============================================================================

import asyncio
import logging
import time

from pyrogram import enums, errors, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from HasiiMusic import tune, app, config, db, lang, queue
from HasiiMusic.helpers import buttons, utils

# Set up logging
LOGGER = logging.getLogger(name)

# Dictionary of radio stations with their stream URLs
RADIO_STATION = {
    # --- Sri Lankan Radio Stations ---
    "ꜱʟʙᴄ ʀᴀᴅɪᴏ": "http://220.247.227.20:8000/RSLstream",
    "ꜱɪʏᴀᴛʜᴀ ꜰᴍ": "https://srv01.onlineradio.voaplus.com/siyathafm",
    "ɪᴛɴ ꜰᴍ": "https://cp12.serverse.com/proxy/itnfm/stream",
    "ʀʜʏᴛʜᴍ ꜰᴍ": "https://srv01.onlineradio.voaplus.com/rhythmfm",
    "ᴋᴏᴛʜᴍᴀʟᴇ ꜰᴍ": "https://s46.myradiostream.com:11156/listen.mp3",
    "ᴄᴏʟᴏᴜʀ ʀᴀᴅɪᴏ": "https://stream.zeno.fm/uo3gmts0ilivv",
    "ꜰʀᴇᴇ ꜰᴍ": "https://stream.zeno.fm/1tcs4fbw7rquv",
    "ꜱᴇᴛʜ ꜰᴍ": "https://listen.radioking.com/radio/384487/stream/435781",
    "ᴠ ꜰᴍ": "https://dc1.serverse.com/proxy/fmlanka/stream",
    "ꜱɪʀᴀꜱᴀ ꜰᴍ": "http://live.trusl.com:1170/",                                          # ✅
    "ʜɪʀᴜ ꜰᴍ": "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden",         # ✅
    "ʏ ꜰᴍ": "http://live.trusl.com:1180/",                                               # ✅
    "ꜱʜᴀᴀ ꜰᴍ": "https://radio.lotustechnologieslk.net:2020/stream/shaafmgarden",         # ✅
    "ɢᴏʟᴅ ꜰᴍ": "https://radio.lotustechnologieslk.net:2020/stream/goldfmgarden",         # ✅
    "ꜱᴏᴏʀɪʏᴀɴ ꜰᴍ": "https://radio.lotustechnologieslk.net:2020/stream/sooriyanfmgarden",  # ✅
    "ʙᴇꜱᴛᴄᴏᴀꜱᴛ.ꜰᴍ": "https://streams.radio.co/sea5dddd6b/listen",                        # ✅
    "ʏᴇꜱ ꜰᴍ": "http://live.trusl.com:1160/",                                             # ✅
    "ꜱɪᴛʜᴀ ꜰᴍ": "https://stream.streamgenial.stream/cdzzrkrv0p8uv",                      # ✅
    "ʜɪʀᴜ ꜰᴍ ɢᴀʀᴅᴇɴ": "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden",  # ✅
    "ꜱᴜɴ ꜰᴍ": "https://radio.lotustechnologieslk.net:2020/stream/sunfmgarden",           # ✅
    "ꜱʜʀᴇᴇ ꜰᴍ": "https://streamingv2.shoutcast.com/shreefm945",                          # ✅
    "ʀᴇᴅ ꜰᴍ": "https://shaincast.caster.fm:47830/listen.mp3",                            # ✅
    "ʀᴀɴ ꜰᴍ": "https://207.148.74.192:7874/ran.mp3",                                     # ✅
    "ɴᴇᴛʜ ꜰᴍ": "https://cp11.serverse.com/proxy/nethfm/stream",                          # ✅
    "ᴋɪꜱꜱ ꜰᴍ": "https://srv01.onlineradio.voaplus.com/kissfm",                           # ✅
    "ʀᴀɴɢɪʀɪ ꜰᴍ": "https://stream.streamgenial.stream/hwafmr3f4p8uv",                    # ✅
    "ʟᴀᴋʜᴀɴᴅᴀ ʀᴀᴅɪᴏ": "https://cp12.serverse.com/proxy/itnfm?mp=/stream",               # ✅
    "ʜɪᴛᴢ ꜰᴍ": "https://stream-173.zeno.fm/uyx7eqengijtv",                               # ✅
    "ɴᴀ ᴅᴀʜᴀꜱᴀ ꜰᴍ": "https://stream-155.zeno.fm/z7q96fbw7rquv",                          # ✅
    "ᴘᴀʀᴀɴɪ ɢᴇᴇ": "http://cast2.citrus3.com:8288/",                                      # ✅
    "ᴅᴇᴇᴘ ʜᴏᴜꜱᴇ ᴍᴜꜱɪᴄ": "http://live.dancemusic.ro:7000/",                               # ✅
    "ʙᴀꜱᴇ ᴍᴜꜱɪᴄ": "https://base-music.stream.laut.fm/base-music",                        # ✅
    "ᴘᴜʟꜱᴇ ᴇᴅᴍ": "https://naxos.cdnstream.com/1373_128",                                 # ✅

}

async def _safe_edit_caption(message, caption, reply_markup=None):
    """Edit caption with FloodWait handling and silent failures."""
    try:
        await message.edit_caption(caption, reply_markup=reply_markup)
    except errors.FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        try:
            await message.edit_caption(caption, reply_markup=reply_markup)
        except Exception:
            pass
    except Exception:
        # Ignore MessageNotModified and other non-critical edit failures
        pass


def radio_buttons(page=0, per_page=10):
    """Generate pagination buttons for radio stations."""
    stations = sorted(RADIO_STATION.keys())
    total_pages = (len(stations) - 1) // per_page + 1
    start = page * per_page
    end = start + per_page
    current_stations = stations[start:end]

    # Create buttons in rows of 2
    buttons_list = []
    for i in range(0, len(current_stations), 2):
        row = []
        # Add first button in the row
        row.append(InlineKeyboardButton(
            current_stations[i], callback_data=f"station_{current_stations[i]}"))
        # Add second button if it exists
        if i + 1 < len(current_stations):
            row.append(InlineKeyboardButton(
                current_stations[i + 1], callback_data=f"station_{current_stations[i + 1]}"))
        buttons_list.append(row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            "◀️ ʙᴀᴄᴋ", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            "ɴᴇxᴛ ▶️", callback_data=f"page_{page+1}"))

    if nav_buttons:
        buttons_list.append(nav_buttons)

    buttons_list.append([InlineKeyboardButton(
        "ℹ️ ʜᴇʟᴘ", callback_data=f"radio_help_{page}")])

    return InlineKeyboardMarkup(buttons_list)


async def has_radio_control_permission(chat_id, user_id):
    """
    Check if user has permission to control radio.
    Allowed users:
    - Bot owner
    - Sudo users
    - Authorized users in the chat
    - Chat admins
    - Anonymous admins
    """
    # Check if anonymous admin
    if user_id == 1087968824:
        return True

    # Check if bot owner
    if user_id == config.OWNER_ID:
        return True

    # Check if sudo user
    if user_id in app.sudoers:
        return True

    # Check if authorized user in this chat
    if await db.is_auth(chat_id, user_id):
        return True

    # Check if chat admin
    try:
        member = await app.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except:
        return False


async def update_timer(chat_id, message_id, station_name, start_time):
    """Update the timer on the radio message."""
    last_timer = None
    update_count = 0
    while True:
        try:
            # Check if call is still active - if not, stop updating
            if not await db.get_call(chat_id):
                LOGGER.debug(f"Radio timer stopped for {chat_id} - call ended")
                break
            
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            timer = f"{mins:02d}:{secs:02d}"

# Only update every 5 seconds to reduce API calls and lag
            if timer != last_timer and update_count % 5 == 0:
                try:
                    await app.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=f"📻 ɴᴏᴡ ᴘʟᴀʏɪɴɢ: {station_name}\n⏱️ ᴛɪᴍᴇ: {timer}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(
                                f"🎵 {station_name}", callback_data="noop")],
                            [
                                InlineKeyboardButton(
                                    "🔀 ꜱᴛᴀᴛɪᴏɴꜱ", callback_data="skip_radio"),
                                InlineKeyboardButton(
                                    "❌ ᴄʟᴏꜱᴇ", callback_data="close_message")
                            ]
                        ])
                    )
                    last_timer = timer
                except errors.FloodWait as fw:
                    # If rate limited, wait and continue without updating
                    await asyncio.sleep(fw.value)
                except Exception:
                    # Skip this update if any error
                    pass
            
            update_count += 1
        except Exception as e:
            # Silently ignore MESSAGE_NOT_MODIFIED, message deleted, or chat ended errors
            error_str = str(e)
            if not any(err in error_str for err in ["MESSAGE_NOT_MODIFIED", "MESSAGE_DELETE", "MESSAGE_ID_INVALID", "CHAT_ADMIN_REQUIRED"]):
                LOGGER.debug(f"Timer update error: {e}")
            break
        await asyncio.sleep(1)


@app.on_message(
    filters.command(["radio", "cradio"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
async def radio_handler(_, m: Message) -> None:
    """Handle radio command."""
    chat_id = m.chat.id
    cplay = m.command[0] == "cradio"

    if cplay:
        channel_id = await db.get_cmode(m.chat.id)
        if channel_id is None:
            return await m.reply_text(
                "❌ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ɪꜱ ɴᴏᴛ ᴇɴᴀʙʟᴇᴅ.\n\n"
                "ᴛᴏ ᴇɴᴀʙʟᴇ ꜰᴏʀ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ:\n"
                "`/channelplay linked`\n\n"
                "ᴛᴏ ᴇɴᴀʙʟᴇ ꜰᴏʀ ᴀɴʏ ᴄʜᴀɴɴᴇʟ:\n"
                "/channelplay [channel_id]"
            )
        try:
            chat = await app.get_chat(channel_id)
            chat_id = chat.id
        except:
            return await m.reply_text(
                "❌ ᴄʜᴀɴɴᴇʟ ɴᴏᴛ ꜰᴏᴜɴᴅ ᴏʀ ʙᴏᴛ ɪꜱ ɴᴏᴛ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ.\n"
                "ᴘʟᴇᴀꜱᴇ ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɪᴅ ɪꜱ ᴄᴏʀʀᴇᴄᴛ ᴀɴᴅ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴅᴇᴅ ᴄᴏʀʀᴇᴄᴛʟʏ."
            )

    await m.reply_text(
        "📻 ꜱᴇʟᴇᴄᴛ ᴀ ʀᴀᴅɪᴏ ꜱᴛᴀᴛɪᴏɴ ᴛᴏ ᴘʟᴀʏ:",
        reply_markup=radio_buttons(page=0),
    )


@app.on_callback_query(filters.regex(r"^page_"))
async def on_page_change(_, callback_query):
    """Handle pagination."""
    await callback_query.answer()
    page = int(callback_query.data.split("_")[1])
    try:
        await callback_query.message.edit_reply_markup(radio_buttons(page=page))
    except errors.FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await callback_query.message.edit_reply_markup(radio_buttons(page=page))
        except errors.MessageNotModified:
            pass  # Message content is the same, ignore
    except errors.MessageNotModified:
        pass  # Message content is the same, ignore
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^station_"))
async def on_station_select(_, callback_query):
    """Handle station selection and start playback."""
    station_name = callback_query.data.split("station_")[1]
    RADIO_URL = RADIO_STATION.get(station_name)

    if not RADIO_URL:
        return await callback_query.answer("❌ ɪɴᴠᴀʟɪᴅ ꜱᴛᴀᴛɪᴏɴ ɴᴀᴍᴇ.", show_alert=True)

# Check if channel play is enabled for this group
    group_chat_id = callback_query.message.chat.id
    channel_id = await db.get_cmode(group_chat_id)
    
    # Use channel_id for playback if channel play is enabled, otherwise use group chat_id
    playback_chat_id = channel_id if channel_id else group_chat_id

    # Check if radio is already playing - only authorized users can switch
    if await db.get_call(playback_chat_id):
        # Check if user has permission
        if not await has_radio_control_permission(playback_chat_id, callback_query.from_user.id):
            return await callback_query.answer(
                "❌ ᴏɴʟʏ ᴀᴅᴍɪɴꜱ, ʙᴏᴛ ᴏᴡɴᴇʀ, ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ, ᴏʀ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴄʜᴀɴɢᴇ ᴛʜᴇ ꜱᴛᴀᴛɪᴏɴ ᴡʜɪʟᴇ ʀᴀᴅɪᴏ ɪꜱ ᴘʟᴀʏɪɴɢ.\n"
                "ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ ꜰᴏʀ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ꜱᴇꜱꜱɪᴏɴ ᴛᴏ ᴇɴᴅ.",
                show_alert=True
            )

    # If assistant is not in the group, invite them before playing
    if playback_chat_id not in db.active_calls:
        client = await db.get_client(playback_chat_id)
        try:
            member = await app.get_chat_member(playback_chat_id, client.id)
            if member.status in [
                enums.ChatMemberStatus.BANNED,
                enums.ChatMemberStatus.RESTRICTED,
            ]:
                try:
                    await app.unban_chat_member(chat_id=playback_chat_id, user_id=client.id)
                except:
                    return await callback_query.answer(
                        f"❌ ᴀꜱꜱɪꜱᴛᴀɴᴛ {client.mention} ɪꜱ ʙᴀɴɴᴇᴅ!\n"
                        f"ᴜɴʙᴀɴ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.",
                        show_alert=True
                    )
        except errors.ChatAdminRequired:
            return await callback_query.answer(
                "❌ ᴍᴀᴋᴇ ᴍᴇ ᴀᴅᴍɪɴ ᴛᴏ ɪɴᴠɪᴛᴇ ᴀꜱꜱɪꜱᴛᴀɴᴛ!",
                show_alert=True
            )
        except errors.UserNotParticipant:
            # Assistant not in group - invite them
            if callback_query.message.chat.username:
                invite_link = callback_query.message.chat.username
                try:
                    await client.resolve_peer(invite_link)
                except:
                    pass
            else:
                try:
                    invite_link = (await app.get_chat(playback_chat_id)).invite_link
                    if not invite_link:
                        invite_link = await app.export_chat_invite_link(playback_chat_id)
                except errors.ChatAdminRequired:
                    return await callback_query.answer(
                        "❌ ᴍᴀᴋᴇ ᴍᴇ ᴀᴅᴍɪɴ ᴛᴏ ɪɴᴠɪᴛᴇ ᴀꜱꜱɪꜱᴛᴀɴᴛ!",
                        show_alert=True
                    )
                except Exception as ex:
                    return await callback_query.answer(
                        f"❌ ᴇʀʀᴏʀ: {type(ex).name}",
                        show_alert=True
                    )

            await callback_query.answer("🔄 ɪɴᴠɪᴛɪɴɢ ᴀꜱꜱɪꜱᴛᴀɴᴛ...")
            await asyncio.sleep(1)

            try:
                await client.join_chat(invite_link)
            except errors.UserAlreadyParticipant:
                pass
            except errors.InviteRequestSent:
                try:
                    await app.approve_chat_join_request(playback_chat_id, client.id)
                except Exception as ex:
                    return await callback_query.answer(
                        f"❌ ᴇʀʀᴏʀ: {type(ex).name}",
                        show_alert=True
                    )
            except Exception as ex:
                return await callback_query.answer(
                    f"❌ ᴇʀʀᴏʀ: {type(ex).name}",
                    show_alert=True
                )

            await client.resolve_peer(playback_chat_id)

    # Answer callback immediately to prevent lag
    await callback_query.answer()

    mention = callback_query.from_user.mention if callback_query.from_user.id != 1087968824 else "ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ"

    # Keep the station selection message visible - don't delete it
    # Users can continue selecting stations from the same button list

# Send thumbnail to the group where the command was issued
    try:
        mystic = await app.send_photo(
            chat_id=group_chat_id,
            photo=config.RADIO_IMG,
            caption=f"📻 ɴᴏᴡ ᴘʟᴀʏɪɴɢ: {station_name}\n⏱️ ᴛɪᴍᴇ: 00:00",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🎵 {station_name}", callback_data="noop")],
                [
                    InlineKeyboardButton("🔀 ꜱᴛᴀᴛɪᴏɴꜱ", callback_data="skip_radio"),
                    InlineKeyboardButton("❌ ᴄʟᴏꜱᴇ", callback_data="close_message")
                ]
            ])
        )
    except errors.FloodWait as e:
        await asyncio.sleep(e.value)
        mystic = await app.send_photo(
            chat_id=group_chat_id,
            photo=config.RADIO_IMG,
            caption=f"📻 ɴᴏᴡ ᴘʟᴀʏɪɴɢ: {station_name}\n⏱️ ᴛɪᴍᴇ: 00:00",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🎵 {station_name}", callback_data="noop")],
                [
                    InlineKeyboardButton("🔀 ꜱᴛᴀᴛɪᴏɴꜱ", callback_data="skip_radio"),
                    InlineKeyboardButton("❌ ᴄʟᴏꜱᴇ", callback_data="close_message")
                ]
            ])
        )

    start_time = time.time()
    asyncio.create_task(update_timer(
        group_chat_id, mystic.id, station_name, start_time))

    # Create a file object for radio stream
    class RadioFile:
        def init(self, url, title):
            self.url = url
            self.title = title
            self.is_live = True
            self.duration = "ʟɪᴠᴇ ꜱᴛʀᴇᴀᴍ"
            self.duration_sec = 0
            self.file_path = url  # Use URL as file path for streaming
            self.id = url
            self.message_id = mystic.id
            self.user = mention
            self.thumb = config.RADIO_IMG
            self.video = False  # Audio only for radio

    file = RadioFile(RADIO_URL, f"📻 {station_name}")

    # Check if already playing - switch to new station immediately
    if await db.get_call(playback_chat_id):
        # Clear queue and stop current playback
        queue.clear(playback_chat_id)
        try:
            await tune.stop_stream(playback_chat_id)
        except:
            pass

    # Add new station to queue
    position = queue.add(playback_chat_id, file)

    # Play the stream (use playback_chat_id for actual audio)
    try:
        await tune.play_media(chat_id=playback_chat_id, message=mystic, media=file)
    except errors.FloodWait as fw:
        # Back off then retry once to avoid cascading FLOOD_WAIT
        await asyncio.sleep(fw.value + 1)
        try:
            await tune.play_media(chat_id=playback_chat_id, message=mystic, media=file)
        except Exception as retry_err:
            await _safe_edit_caption(
                mystic,
                f"❌ ᴇʀʀᴏʀ ᴘʟᴀʏɪɴɢ ʀᴀᴅɪᴏ:\n{retry_err}"
            )
            LOGGER.error(f"Radio play retry failed: {retry_err}")
            queue.clear(playback_chat_id)
            await db.remove_call(playback_chat_id)
    except Exception as e:
        await _safe_edit_caption(
            mystic,
            f"❌ ᴇʀʀᴏʀ ᴘʟᴀʏɪɴɢ ʀᴀᴅɪᴏ:\n{e}"
        )
        LOGGER.error(f"Radio play error: {e}")
        queue.clear(playback_chat_id)
        await db.remove_call(playback_chat_id)


@app.on_callback_query(filters.regex(r"^skip_radio"))
async def skip_radio_callback(_, callback_query):
    """Handle skip radio button - show station list."""
    # Anyone can browse stations, not just admins
    await callback_query.answer()
    
    try:
        await callback_query.message.reply_text(
            "📻 ꜱᴇʟᴇᴄᴛ ᴀɴᴏᴛʜᴇʀ ʀᴀᴅɪᴏ ꜱᴛᴀᴛɪᴏɴ:",
            reply_markup=radio_buttons(page=0)
        )
    except errors.FloodWait as e:
        await asyncio.sleep(e.value)
        await callback_query.message.reply_text(
            "📻 ꜱᴇʟᴇᴄᴛ ᴀɴᴏᴛʜᴇʀ ʀᴀᴅɪᴏ ꜱᴛᴀᴛɪᴏɴ:",
            reply_markup=radio_buttons(page=0)
        )
    except Exception:
        pass

@app.on_callback_query(filters.regex(r"^close_message"))
async def close_message_callback(_, callback_query):
    """Handle close button."""
    try:
        # Check if user has permission to delete
        if await has_radio_control_permission(callback_query.message.chat.id, callback_query.from_user.id):
            await callback_query.message.delete()
            await callback_query.answer()
        else:
            await callback_query.answer(
                "❌ ᴏɴʟʏ ᴀᴅᴍɪɴꜱ, ʙᴏᴛ ᴏᴡɴᴇʀ, ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ, ᴏʀ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴄʟᴏꜱᴇ ᴛʜɪꜱ ᴍᴇꜱꜱᴀɢᴇ.",
                show_alert=True
            )
    except Exception as e:
        await callback_query.answer(f"❌ ᴇʀʀᴏʀ: {str(e)}", show_alert=True)


@app.on_callback_query(filters.regex(r"^radio_help_"))
async def on_radio_help(_, callback_query):
    """Show help message."""
    await callback_query.answer()
    page = int(callback_query.data.split("_")[2])
    
    help_text = (
        "<blockquote>📻 ʀᴀᴅɪᴏ ᴘʟᴜɢɪɴ ʜᴇʟᴘ</blockquote>\n\n"
        "<blockquote><b>ᴇɴɢʟɪꜱʜ:</b>\n"
        "• ᴛʏᴘᴇ /radio ᴛᴏ ᴏᴘᴇɴ ꜱᴛᴀᴛɪᴏɴ ʟɪꜱᴛ\n"
        "• ᴜꜱᴇ /stop ᴛᴏ ꜱᴛᴏᴘ ᴘʟᴀʏʙᴀᴄᴋ </blockquote>"
        "<blockquote><b>සිංහල:</b>\n"
        "<b>• /radio ටයිප් කරලා ස්ටේෂන් එකක් තෝරගන්න.</b>\n"
        "<b>• අහලා ඉවරනම් /stop කරන්න.</b> </blockquote>"
    )
    
    await callback_query.message.edit_text(
        help_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ꜱᴛᴀᴛɪᴏɴꜱ",
                                  callback_data=f"back_to_stations_{page}")]
        ])
    )


@app.on_callback_query(filters.regex(r"^back_to_stations_"))
async def on_back_to_stations(_, callback_query):
    """Return to station list."""
    await callback_query.answer()
    page = int(callback_query.data.split("_")[-1])
    
    await callback_query.message.edit_text(
        "📻 ꜱᴇʟᴇᴄᴛ ᴀ ʀᴀᴅɪᴏ ꜱᴛᴀᴛɪᴏɴ ᴛᴏ ᴘʟᴀʏ:",
        reply_markup=radio_buttons(page=page)
    )


@app.on_callback_query(filters.regex(r"^noop"))
async def on_noop(_, callback_query):
    """Handle no-operation button."""
    await callback_query.answer("🎵 ᴇɴᴊᴏʏɪɴɢ ᴛʜᴇ ᴍᴜꜱɪᴄ!", show_alert=False)
# ==============================================================================
# resume.py - Resume Playback Command
# ==============================================================================
# This plugin handles resuming paused voice chat playback.
#
# Commands:
# - /resume - Resume paused playback
#
# Requirements:
# - User must be admin or authorized user
# - Music must be currently paused
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import tune, app, db, lang
from HasiiMusic.helpers import buttons, can_manage_vc


@app.on_message(filters.command(["resume"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _resume(_, m: types.Message):
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    if await db.playing(m.chat.id):
        return await m.reply_text(m.lang["play_not_paused"])

    await tune.resume(m.chat.id)
    await m.reply_text(
        text=m.lang["play_resumed"].format(m.from_user.mention),
        reply_markup=buttons.controls(m.chat.id),
    )
# ==============================================================================
# seek.py - Seek to Timestamp Command
# ==============================================================================
# This plugin allows seeking to a specific timestamp in the current track.
#
# Commands:
# - /seek <seconds> - Seek forward to timestamp
# - /seekback <seconds> - Seek backward to timestamp
#
# Requirements:
# - User must be admin or authorized user
# - Music must be playing (not paused)
# - Track must have a known duration (not live streams)
# - Minimum seek: 10 seconds
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import tune, app, db, lang, queue
from HasiiMusic.helpers import can_manage_vc


@app.on_message(filters.command(["seek", "seekback"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _seek(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(m.lang["play_seek_usage"].format(m.command[0]))

    try:
        to_seek = int(m.command[1])
    except ValueError:
        return await m.reply_text(m.lang["play_seek_usage"].format(m.command[0]))
    if to_seek < 10:
        return await m.reply_text(m.lang["play_seek_min"])

    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    if not await db.playing(m.chat.id):
        return await m.reply_text(m.lang["play_already_paused"])

    media = queue.get_current(m.chat.id)
    if not media.duration_sec:
        return await m.reply_text(m.lang["play_seek_no_dur"])

    sent = await m.reply_text(m.lang["play_seeking"])
    
    current_time = getattr(media, 'time', 0)
    if m.command[0] == "seekback":
        stype = m.lang["backward"]
        start_from = max(1, current_time - to_seek)
    else:
        stype = m.lang["forward"]
        start_from = min(current_time + to_seek, media.duration_sec - 5)

    # Use the new seek_stream method
    success = await tune.seek_stream(m.chat.id, int(start_from))
    
    if success:
        await sent.edit_text(
            m.lang["play_seeked"].format(stype, start_from, m.from_user.mention)
        )
    else:
        await sent.edit_text("❌ Failed to seek!")
# ==============================================================================
# shuffle.py - Shuffle Queue Command
# ==============================================================================
# This plugin handles shuffling the playback queue.
#
# Commands:
# - /shuffle - Shuffle the current queue
#
# Requirements:
# - User must be admin or authorized user
# - Queue must have at least 2 tracks
# ==============================================================================

import random
from pyrogram import filters, types

from HasiiMusic import app, db, lang, queue
from HasiiMusic.helpers import can_manage_vc


@app.on_message(filters.command(["shuffle"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _shuffle(_, m: types.Message):
    items = queue.get_all(m.chat.id)
    
    if not items or len(items) <= 1:
        return await m.reply_text("⚠️ Queue is empty or has only one track!")
    
    # Get current track and remaining items
    current = items[0] if items else None
    remaining = items[1:] if len(items) > 1 else []
    
    if not remaining:
        return await m.reply_text("⚠️ No tracks to shuffle!")
    
    # Shuffle remaining tracks
    random.shuffle(remaining)
    
    # Rebuild queue with current track first
    queue.clear(m.chat.id)
    if current:
        queue.add(m.chat.id, current)
    for item in remaining:
        queue.add(m.chat.id, item)
    
    await m.reply_text(f"🔀 Queue **shuffled**! ({len(remaining)} tracks randomized)")
# ==============================================================================
# skip.py - Skip Track Command
# ==============================================================================
# This plugin handles skipping to the next track in the queue.
#
# Commands:
# - /skip - Skip current track and play next
# - /next - Same as /skip
#
# Requirements:
# - User must be admin or authorized user
# - Music must be playing
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import tune, app, db, lang
from HasiiMusic.helpers import can_manage_vc


@app.on_message(filters.command(["skip", "next"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _skip(_, m: types.Message):
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    await tune.play_next(m.chat.id)
    await m.reply_text(m.lang["play_skipped"].format(m.from_user.mention))
# ==============================================================================
# stop.py - Stop Playback Command
# ==============================================================================
# This plugin handles stopping voice chat playback and clearing the queue.
#
# Commands:
# - /stop - Stop playback and clear queue
# - /end - Same as /stop
#
# Requirements:
# - User must be admin or authorized user
# - Music must be playing
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import tune, app, db, lang
from HasiiMusic.helpers import can_manage_vc


@app.on_message(filters.command(["end", "stop"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _stop(_, m: types.Message):
    if len(m.command) > 1:
        return
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    await tune.stop(m.chat.id)
    await m.reply_text(m.lang["play_stopped"].format(m.from_user.mention))
# ==============================================================================
# auth.py - Authorization Management Commands
# ==============================================================================
# This plugin manages authorized users who can control music playback.
# Authorized users can use playback commands even if they're not admins.
#
# Commands:
# - /auth <user> - Grant playback control permissions to user
# - /unauth <user> - Revoke playback control permissions from user
# - /admincache - Reload admin list cache for current chat
# - /reload - Same as /admincache
#
# Only group admins can add/remove authorized users.
# ==============================================================================

import time

from pyrogram import filters, types

from HasiiMusic import app, db, lang
from HasiiMusic.helpers import admin_check, is_admin, utils


@app.on_message(filters.command(["auth", "unauth"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def _auth(_, m: types.Message):
    user = await utils.extract_user(m)
    if not user:
        return await m.reply_text(m.lang["user_not_found"])

    if m.command[0] == "auth":
        if await is_admin(m.chat.id, user.id):
            return await m.reply_text(m.lang["auth_is_admin"])

        await db.add_auth(m.chat.id, user.id)
        await m.reply_text(m.lang["auth_added"].format(user.mention))
    else:
        await db.rm_auth(m.chat.id, user.id)
        await m.reply_text(m.lang["auth_removed"].format(user.mention))


rel_hist = {}


@app.on_message(filters.command(["admincache", "reload"]) & filters.group & ~app.bl_users)
@lang.language()
async def _admincache(_, m: types.Message):
    # Check if message is from anonymous admin
    if not m.from_user:
        return
    
    if m.from_user.id in rel_hist:
        if time.time() < rel_hist[m.from_user.id]:
            return await m.reply_text(m.lang["admin_cache_wait"])

    rel_hist[m.from_user.id] = time.time() + 600
    sent = await m.reply_text(m.lang["admin_cache_reloading"])
    await db.get_admins(m.chat.id, reload=True)
    await sent.edit_text(m.lang["admin_cache_reloaded"])
# ==============================================================================
# blacklist.py - User/Chat Blacklist Commands (Sudo Only)
# ==============================================================================
# This plugin manages the bot blacklist to block abusive users/chats.
# Blacklisted entities cannot use the bot.
#
# Commands:
# - /blacklist <user_id|chat_id|@username> - Add to blacklist
# - /unblacklist <user_id|chat_id|@username> - Remove from blacklist
# - /whitelist <user_id|chat_id|@username> - Same as /unblacklist
#
# Only sudo users can manage the blacklist.
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import app, db, lang


@app.on_message(filters.command(["blacklist", "unblacklist", "whitelist"]) & app.sudo_filter)
@lang.language()
async def _blacklist(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(m.lang["bl_usage"].format(m.command[0]))

    try:
        chat_id = m.command[1]
        if not str(chat_id).startswith("@"):
            chat_id = int(chat_id)
        else:
            chat_id = (await app.get_chat(chat_id)).id
    except:
        return await m.reply_text(m.lang["bl_invalid"])

    if m.command[0] == "blacklist":
        if chat_id in db.blacklisted or chat_id in app.bl_users:
            return await m.reply_text(m.lang["bl_already"])
        if not str(chat_id).startswith("-"):
            app.bl_users.add(chat_id)
        await db.add_blacklist(chat_id)
        await m.reply_text(m.lang["bl_added"])
    else:
        if chat_id not in db.blacklisted and chat_id not in app.bl_users:
            return await m.reply_text(m.lang["bl_not"])
        if not str(chat_id).startswith("-"):
            app.bl_users.discard(chat_id)
        await db.del_blacklist(chat_id)
        await m.reply_text(m.lang["bl_removed"])
# ==============================================================================
# channelplay.py - Channel Play Mode Configuration
# ==============================================================================
# This plugin enables playing music in linked channels instead of the group voice chat.
# Useful for groups with linked channels.
#
# Commands:
# - /channelplay linked - Enable for linked channel
# - /channelplay <channel_id> - Enable for specific channel
# - /channelplay disable - Disable channel play mode
#
# Requirements:
# - User must be admin
# - Bot must be admin in the channel
# - For "linked" mode, channel must be linked to the group
# ==============================================================================

from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ChatMemberStatus, ChatType
from pyrogram.types import Message

from HasiiMusic import app, config, db


@app.on_message(filters.command(["channelplay"]) & filters.group & ~app.bl_users)
async def channelplay_command(_, m: Message):
    """Enable or disable channel play mode."""
    # Check if from_user exists (not sent by channel/anonymous admin)
    if not m.from_user:
        return await m.reply_text("❌ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ᴄᴀɴɴᴏᴛ ʙᴇ ᴜꜱᴇᴅ ʙʏ ᴄʜᴀɴɴᴇʟꜱ ᴏʀ ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴꜱ.")
    
    # Check if user is admin
    member = await app.get_chat_member(m.chat.id, m.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        return await m.reply_text("❌ ᴏɴʟʏ ᴀᴅᴍɪɴꜱ ᴄᴀɴ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ.")

    if len(m.command) < 2:
        return await m.reply_text(
            f"ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {m.chat.title}\n\n"
            "ᴛᴏ ᴇɴᴀʙʟᴇ ꜰᴏʀ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ:\n"
            "`/channelplay linked`\n\n"
            "ᴛᴏ ᴇɴᴀʙʟᴇ ꜰᴏʀ ᴀɴʏ ᴄʜᴀɴɴᴇʟ:\n"
            "`/channelplay [channel_id]`\n\n"
            "ᴛᴏ ᴅɪꜱᴀʙʟᴇ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ:\n"
            "`/channelplay disable`"
        )

    query = m.text.split(None, 1)[1].strip()

    # Disable channel play
    if query.lower() == "disable":
        await db.set_cmode(m.chat.id, None)
        return await m.reply_text("✅ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ᴅɪꜱᴀʙʟᴇᴅ.")

    # Enable for linked channel
    elif query.lower() == "linked":
        chat = await app.get_chat(m.chat.id)
        if chat.linked_chat:
            channel_id = chat.linked_chat.id
            await db.set_cmode(m.chat.id, channel_id)
            return await m.reply_text(
                f"✅ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ᴇɴᴀʙʟᴇᴅ ꜰᴏʀ: {chat.linked_chat.title}\n"
                f"ᴄʜᴀɴɴᴇʟ ɪᴅ: `{chat.linked_chat.id}`"
            )
        else:
            return await m.reply_text("❌ ᴛʜɪꜱ ᴄʜᴀᴛ ᴅᴏᴇꜱɴ'ᴛ ʜᴀᴠᴇ ᴀ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ.")

    # Enable for specific channel
    else:
        # Handle numeric channel IDs
        if query.lstrip("-").isdigit():
            channel_id = int(query)
        else:
            channel_id = query  # Username or invite link

        try:
            chat = await app.get_chat(channel_id)
        except Exception as e:
            return await m.reply_text(
                f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ ᴄʜᴀɴɴᴇʟ.\n\n"
                f"ᴇʀʀᴏʀ: `{type(e).__name__}`\n\n"
                "ᴍᴀᴋᴇ ꜱᴜʀᴇ ʏᴏᴜ'ᴠᴇ ᴀᴅᴅᴇᴅ ᴛʜᴇ ʙᴏᴛ ᴀꜱ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ᴘʀᴏᴍᴏᴛᴇᴅ ɪᴛ ᴀꜱ ᴀᴅᴍɪɴ.\n\n"
                "ꜰᴏʀ ɴᴜᴍᴇʀɪᴄ ɪᴅꜱ: ᴜꜱᴇ ᴛʜᴇ ꜰᴜʟʟ ɪᴅ ɪɴᴄʟᴜᴅɪɴɢ `-100` ᴘʀᴇꜰɪx\n"
                "ᴇxᴀᴍᴘʟᴇ: `/channelplay -1001234567890`"
            )

        if chat.type != ChatType.CHANNEL:
            return await m.reply_text("❌ ᴏɴʟʏ ᴄʜᴀɴɴᴇʟꜱ ᴀʀᴇ ꜱᴜᴘᴘᴏʀᴛᴇᴅ.")

        # Check if user is owner of the channel
        owner_username = None
        owner_id = None
        try:
            async for user in app.get_chat_members(
                chat.id, filter=ChatMembersFilter.ADMINISTRATORS
            ):
                if user.status == ChatMemberStatus.OWNER:
                    owner_username = user.user.username or "Unknown"
                    owner_id = user.user.id
                    break
        except Exception as e:
            return await m.reply_text(
                f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀꜱ.\n\n"
                f"ᴇʀʀᴏʀ: `{type(e).__name__}`\n\n"
                "ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ."
            )

        if not owner_id:
            return await m.reply_text(
                "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ꜰɪɴᴅ ᴄʜᴀɴɴᴇʟ ᴏᴡɴᴇʀ.\n\n"
                "ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ʜᴀꜱ ᴘᴇʀᴍɪꜱꜱɪᴏɴ ᴛᴏ ᴠɪᴇᴡ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴꜱ."
            )

        if owner_id != m.from_user.id:
            return await m.reply_text(
                f"❌ ʏᴏᴜ ɴᴇᴇᴅ ᴛᴏ ʙᴇ ᴛʜᴇ ᴏᴡɴᴇʀ ᴏꜰ ᴄʜᴀɴɴᴇʟ {chat.title} ᴛᴏ ᴄᴏɴɴᴇᴄᴛ ɪᴛ ᴡɪᴛʜ ᴛʜɪꜱ ɢʀᴏᴜᴘ.\n\n"
                f"ᴄʜᴀɴɴᴇʟ'ꜱ ᴏᴡɴᴇʀ: @{owner_username}\n\n"
                "ᴀʟᴛᴇʀɴᴀᴛɪᴠᴇʟʏ, ʏᴏᴜ ᴄᴀɴ ʟɪɴᴋ ʏᴏᴜʀ ᴄʜᴀᴛ'ꜱ ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ᴄᴏɴɴᴇᴄᴛ ᴡɪᴛʜ `/channelplay linked`"
            )

        await db.set_cmode(m.chat.id, chat.id)
        return await m.reply_text(
            f"✅ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ᴇɴᴀʙʟᴇᴅ ꜰᴏʀ: {chat.title}\n"
            f"ᴄʜᴀɴɴᴇʟ ɪᴅ: `{chat.id}`"
        )
# README.md
<div align="center">
  <img src="https://files.catbox.moe/und0yt.jpg" alt="Hasii Music Bot" width="400"/>
  
  # 🎵 Hasii Music Bot
  
  <p><b>A Powerful Telegram Music Player Bot</b></p>
  
  [![Telegram](https://img.shields.io/badge/Telegram-Channel-blue?style=for-the-badge&logo=telegram)](https://t.me/TheInfinityAI)
  [![Telegram](https://img.shields.io/badge/Telegram-Support-blue?style=for-the-badge&logo=telegram)](https://t.me/Hasindu_Lakshan)
  
</div>

---

## ✨ Features

- 🎵 **High Quality Music Streaming** - Crystal clear audio with STUDIO quality
- 📻 **Live Radio Streaming** - 50+ international and local radio stations
- 🎧 **YouTube Support** - Play music from YouTube links or search
- 📝 **Queue System** - Manage multiple songs in queue
- ⚡ **Fast & Reliable** - Built with Pyrogram and PyTgCalls
- 🎛 **Admin Controls** - Pause, resume, skip, and stop controls
- 🌐 **Multi-Language** - Supports English and Sinhala
- 👥 **User Authorization** - Authorized users can control playback
- 📊 **Statistics** - Track bot usage and performance
- 🔄 **Auto-Leave** - Automatically leaves inactive voice chats

---

## 🚀 Deployment

### ✔️ Prerequisites

- Python 3.10+ installed
- Deno & FFmpeg installed on your system
- Required variables mentioned in sample.env

### Requirements

- Python 3.12+
- MongoDB Database
- Telegram Bot Token
- Telegram API ID & Hash
- Pyrogram String Session

### Environment Variables

Create a `.env` file with the following variables:

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
MONGO_DB_URI=your_mongodb_uri
LOGGER_ID=your_logger_group_id
OWNER_ID=your_user_id
STRING_SESSION=your_pyrogram_session
COOKIE_URL=youtube_cookies_url (optional)
```

### Installation

1. **Clone the repository**

```bash
git clone https://github.com/hasindu-nagolla/HasiiMusicBot
cd HasiiMusicBot
```

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Set up environment variables**

```bash
cp sample.env .env
# Edit .env with your values
```

4. **Run the bot**

```bash
bash start
```

---

## 📖 Commands

### User Commands

- `/play` - Play a song (YouTube URL or search query)
- `/radio` - Browse and play live radio stations
- `/queue` - View current queue
- `/ping` - Check bot status
- `/help` - Show help menu
- `/lang` - Change language

### Admin Commands

- `/pause` - Pause current stream
- `/resume` - Resume paused stream
- `/skip` - Skip current track
- `/stop` - Stop playing and clear queue
- `/seek` - Seek to specific timestamp
- `/reload` - Reload admin cache

### Sudo Commands

- `/stats` - View bot statistics
- `/broadcast` - Broadcast message to all chats
- `/addsudo` - Add sudo user
- `/rmsudo` - Remove sudo user
- `/restart` - Restart the bot
- `/logs` - Get bot logs

---

## 🛠 Configuration

### Audio Quality Settings

The bot streams audio at **STUDIO** quality (highest available) with:

- **Codec**: Opus (best quality for music)
- **Format**: WebM container for audio downloads
- **Sample Rate**: 48kHz
- **Channels**: Stereo
- **Optimization**: 16 concurrent downloads, 1MB chunks

### Customization

- Modify language files in `HasiiMusic/locales/`
- Customize thumbnails and images in `config.py`
- Adjust queue limits and duration in `config.py`

---

## 📞 Support & Contact

- **Developer**: Hasindu Nagolla
- **Telegram Channel**: [@TheInfinityAI](https://t.me/TheInfinityAI)
- **Support Group**: [@Hasindu_Lakshan](https://t.me/Hasindu_Lakshan)
- **GitHub**: [hasindu-nagolla](https://github.com/hasindu-nagolla)

---

## 📝 Notes

- Make sure your bot is admin in both the group and logger group
- The assistant account will auto-join groups when needed for playback
- Keep your `.env` file secure and never share it publicly
- For YouTube downloads, cookies may be required for some videos
- Radio streams are live - no duration limits or downloads needed

---

## 🙏 Credits

Special thanks to [AnonymousX1025](https://github.com/AnonymousX1025) for the original inspiration.

---

<div align="center">
  
  ### Made with ❤️ by Hasindu Nagolla
  
  **© 2025 Hasii Music Bot. All rights reserved.**
  
</div>
