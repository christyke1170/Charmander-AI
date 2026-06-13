"""Discord bot entrypoint."""

from __future__ import annotations

import asyncio
import logging

import discord

from bot.commands import build_contextual_mention_response, build_mention_response, register_commands
from bot.dynamax_cache import dynamax_cache_manager
from bot.egg_cache import egg_cache_manager
from bot.raid_attacker_cache import raid_attacker_cache_manager
from config import (
    DISCORD_BOT_TOKEN,
    DISCORD_OWNER_ID,
    DYNAMAX_AUTO_UPDATE_CHECK_HOURS,
    EGG_AUTO_UPDATE_CHECK_HOURS,
    RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS,
    configure_logging,
)
from database.cache_metadata import init_cache_metadata_table
from database.db import init_db
from database.dynamax_attackers_db import init_dynamax_attacker_tables
from database.egg_pool_db import init_egg_pool_tables
from database.pokemon_db import init_pokemon_tables
from database.raid_attackers_db import init_raid_attacker_tables


logger = logging.getLogger(__name__)


class PokemonGoBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        self._raid_attacker_background_task: asyncio.Task[None] | None = None
        self._dynamax_background_task: asyncio.Task[None] | None = None
        self._egg_background_task: asyncio.Task[None] | None = None
        self._startup_raid_cache_checked = False
        self._startup_dynamax_cache_checked = False
        self._startup_egg_cache_checked = False

    async def setup_hook(self) -> None:
        register_commands(self.tree, DISCORD_OWNER_ID, raid_attacker_cache_manager, egg_cache_manager, dynamax_cache_manager)
        synced = await self.tree.sync()
        logger.info("Synced %d slash command(s).", len(synced))
        self._raid_attacker_background_task = asyncio.create_task(self._raid_attacker_background_loop())
        self._raid_attacker_background_task.add_done_callback(self._log_background_task_result)
        self._dynamax_background_task = asyncio.create_task(self._dynamax_background_loop())
        self._dynamax_background_task.add_done_callback(self._log_background_task_result)
        self._egg_background_task = asyncio.create_task(self._egg_background_loop())
        self._egg_background_task.add_done_callback(self._log_background_task_result)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "unknown")
        raid_already_checked = self._startup_raid_cache_checked
        if raid_already_checked:
            logger.info("Startup raid attacker cache status already checked for this process; skipping duplicate on_ready check.")
        else:
            self._startup_raid_cache_checked = True
            try:
                stale = raid_attacker_cache_manager.is_stale()
            except Exception:
                logger.exception("Failed to check raid attacker cache status on startup.")
            else:
                if stale:
                    logger.info("Startup raid attacker cache status: stale.")
                    asyncio.create_task(raid_attacker_cache_manager.refresh_if_stale("startup"))
                else:
                    logger.info("Startup raid attacker cache status: fresh.")

        await self._check_startup_egg_cache()
        await self._check_startup_dynamax_cache()

    async def _check_startup_dynamax_cache(self) -> None:
        if self._startup_dynamax_cache_checked:
            logger.info("Startup Dynamax cache status already checked for this process; skipping duplicate startup check.")
            return
        self._startup_dynamax_cache_checked = True
        try:
            stale = dynamax_cache_manager.is_stale()
        except Exception:
            logger.exception("Failed to check Dynamax cache status on startup.")
            return
        if stale:
            logger.info("Startup Dynamax cache status: stale.")
            asyncio.create_task(dynamax_cache_manager.refresh_if_stale("startup"))
        else:
            logger.info("Startup Dynamax cache status: fresh.")

    async def _check_startup_egg_cache(self) -> None:
        if self._startup_egg_cache_checked:
            logger.info("Startup egg pool cache status already checked for this process; skipping duplicate startup check.")
            return
        self._startup_egg_cache_checked = True
        try:
            stale = egg_cache_manager.is_stale()
        except Exception:
            logger.exception("Failed to check egg pool cache status on startup.")
            return
        if stale:
            logger.info("Startup egg pool cache status: stale.")
            asyncio.create_task(egg_cache_manager.refresh_if_stale("startup"))
        else:
            logger.info("Startup egg pool cache status: fresh.")

    async def _raid_attacker_background_loop(self) -> None:
        check_seconds = max(RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS, 1) * 60 * 60
        while not self.is_closed():
            await asyncio.sleep(check_seconds)
            logger.info("Running scheduled raid attacker cache freshness check.")
            result = await raid_attacker_cache_manager.refresh_if_stale("scheduled")
            logger.info(
                "Scheduled raid attacker cache check finished: attempted=%s updated=%s count=%d reason=%s",
                result.attempted,
                result.updated,
                result.count,
                result.reason,
            )

    async def _dynamax_background_loop(self) -> None:
        check_seconds = max(DYNAMAX_AUTO_UPDATE_CHECK_HOURS, 1) * 60 * 60
        while not self.is_closed():
            await asyncio.sleep(check_seconds)
            logger.info("Running scheduled Dynamax cache freshness check.")
            result = await dynamax_cache_manager.refresh_if_stale("scheduled")
            logger.info(
                "Scheduled Dynamax cache check finished: attempted=%s updated=%s count=%d reason=%s",
                result.attempted,
                result.updated,
                result.count,
                result.reason,
            )

    async def _egg_background_loop(self) -> None:
        check_seconds = max(EGG_AUTO_UPDATE_CHECK_HOURS, 1) * 60 * 60
        while not self.is_closed():
            await asyncio.sleep(check_seconds)
            logger.info("Running scheduled egg pool cache freshness check.")
            result = await egg_cache_manager.refresh_if_stale("scheduled")
            logger.info(
                "Scheduled egg pool cache check finished: attempted=%s updated=%s count=%d reason=%s",
                result.attempted,
                result.updated,
                result.count,
                result.reason,
            )

    def _log_background_task_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("Background cache task stopped unexpectedly.")

    async def _get_referenced_bot_message(self, message: discord.Message) -> discord.Message | None:
        """Return the referenced message when this message replies to this bot."""

        if self.user is None or message.reference is None:
            return None

        referenced = message.reference.resolved
        if isinstance(referenced, discord.Message):
            if referenced.author.id == self.user.id:
                return referenced
            return None

        if message.reference.message_id is None:
            return None

        try:
            fetched = await message.channel.fetch_message(message.reference.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("Could not fetch referenced message for reply context", exc_info=True)
            return None
        if fetched.author.id == self.user.id:
            return fetched
        return None

    async def on_message(self, message: discord.Message) -> None:
        """Respond conversationally when the bot is mentioned.

        This is intentionally rule-based for now and uses only local SQLite data.
        TODO: Add an LLM/RAG layer after retrieving relevant local event rows.
        """

        if message.author.bot:
            return
        if self.user is None:
            return

        referenced_bot_message = await self._get_referenced_bot_message(message)
        is_mentioned = self.user in message.mentions
        if not is_mentioned and referenced_bot_message is None:
            return

        query = message.content
        for mention in (self.user.mention, f"<@!{self.user.id}>"):
            query = query.replace(mention, "")
        query = query.strip()

        logger.info(
            "Mention/reply question received from user_id=%s reply_context=%s: %r",
            message.author.id,
            referenced_bot_message is not None,
            query,
        )
        if not query:
            await message.reply(
                "Hi! Ask me about Pokémon GO events, raids, Community Day, shiny hunting, or what is active right now.",
                suppress_embeds=True,
            )
            return

        if referenced_bot_message is not None:
            response, route, count = await asyncio.to_thread(
                build_contextual_mention_response,
                query,
                referenced_bot_message.content,
                raid_attacker_cache_manager.is_update_running,
                dynamax_cache_manager.is_update_running,
            )
        else:
            response, route, count = await asyncio.to_thread(
                build_mention_response,
                query,
                raid_attacker_cache_manager.is_update_running,
                dynamax_cache_manager.is_update_running,
            )
        logger.info("Mention route used: %s; returned %d event(s)", route, count)
        await message.reply(response, mention_author=False, suppress_embeds=True)


def main() -> None:
    configure_logging()
    init_db()
    init_cache_metadata_table()
    init_raid_attacker_tables()
    init_dynamax_attacker_tables()
    init_egg_pool_tables()
    init_pokemon_tables()
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and add your token.")
    if DISCORD_OWNER_ID is None:
        logger.warning("DISCORD_OWNER_ID is not set or invalid. /update will be unavailable.")

    bot = PokemonGoBot()
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
