"""Gateway client for opt-in alerts; also starts the daily question scheduler."""
import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from bot import ROOT, database_path, load_questions, study_instructions, archive_duration, launch_info, main as daily_scheduler

log = logging.getLogger("nclexapro")
EMOJI = "💊"


def prepare_questions():
    """Seed a missing bank with original questions without replacing an existing bank."""
    path = Path(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
    if not path.exists():
        source = ROOT / "starter-questions.json"
        load_questions(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x") as destination:
                destination.write(source.read_text())
            log.info("Initialized the question bank with 10 original practice questions.")
        except FileExistsError:
            pass
    return load_questions(path)


def announcement_launch_time():
    if int(os.getenv("TEST_REPLAY", "0")):
        return None
    value = os.getenv("ANNOUNCEMENT_START_AT", "2026-10-02T09:00")
    if not value:
        return None
    launch = datetime.fromisoformat(value)
    if launch.tzinfo is None:
        launch = launch.replace(tzinfo=ZoneInfo(os.getenv("BOT_TIMEZONE", "America/Los_Angeles")))
    return launch


async def wait_for_announcement():
    launch = announcement_launch_time()
    if launch is None:
        return
    remaining = (launch - datetime.now(launch.tzinfo)).total_seconds()
    if remaining > 0:
        log.info("Announcement scheduled for %s; waiting without posting.", launch.isoformat())
    while remaining > 0:
        await asyncio.sleep(min(remaining, 60))
        remaining = (launch - datetime.now(launch.tzinfo)).total_seconds()


def first_question_notice():
    value = os.getenv("POST_START_DATE", "2026-10-02")
    if not value:
        return ""
    start = datetime.fromisoformat(value)
    return f"**First questions: {start:%B} {start.day}, {start.year} at 12:00 PM Pacific.**\n\n"


def daily_posting_notice():
    return "Daily questions post at **12:00 PM Pacific**. Personal Discord notification settings still apply."


def announcement_content():
    minutes = archive_duration(int(os.getenv("TEST_REPLAY", "0")))
    return (
        "💊 **Meet NCLEXapro — your daily dose of NCLEX practice!**\n\n"
        "**Created by Siah.**\n\n"
        + first_question_notice()
        +         f"Find the daily dated threads in <#{int(os.environ['DISCORD_CHANNEL_ID'])}>.\n\n"
        "**Reminders:** React 💊 here to subscribe; remove it to unsubscribe. "
        "Subscribers get one daily role ping. "
        "Check your Discord notification settings if alerts are muted.\n\n"
        "**Questions:** NCLEX practice questions with answers and rationales.\n\n"
        "🔒 **Please do not copy, screenshot, forward, or share these questions "
        "anywhere outside this private Discord server.**\n\n"
        + daily_posting_notice()
    )


class NCLEXapro(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_reactions = True
        super().__init__(intents=intents)
        self.role_id = int(os.environ["ALERTS_ROLE_ID"])
        self.channel_id = int(os.environ["ANNOUNCEMENT_CHANNEL_ID"])
        self.message_id = None
        self.worker = None

    async def setup_hook(self):
        self.worker = asyncio.create_task(self.start_services())

    async def start_services(self):
        await self.wait_until_ready()
        try:
            await wait_for_announcement()
            channel = await self.fetch_channel(self.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("Announcement channel must be a server text channel.")
            role = channel.guild.get_role(self.role_id)
            me = channel.guild.me
            if role is None or role.is_default() or role.managed:
                raise ValueError("Alerts role must be an ordinary assignable role in this server.")
            if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
                raise ValueError("Grant Manage Roles and move the bot role above the alerts role.")
            if not role.mentionable:
                raise ValueError("Enable 'Allow anyone to mention this role' for the alerts role.")
            with sqlite3.connect(database_path()) as db:
                db.execute("CREATE TABLE IF NOT EXISTS subscriptions_config "
                           "(channel_id TEXT PRIMARY KEY, message_id TEXT)")
                row = db.execute("SELECT message_id FROM subscriptions_config WHERE channel_id = ?",
                                 (str(self.channel_id),)).fetchone()
                if row:
                    try:
                        message = await channel.fetch_message(int(row[0]))
                    except discord.NotFound as error:
                        if error.code != 10008:
                            raise
                        log.warning("Saved subscription announcement was deleted; recreating it without a ping.")
                        row = None
                    else:
                        if message.content != announcement_content():
                            await message.edit(content=announcement_content(),
                                               allowed_mentions=discord.AllowedMentions.none())
                if not row:
                    message = await channel.send(
                        announcement_content(), allowed_mentions=discord.AllowedMentions.none())
                    db.execute("INSERT OR REPLACE INTO subscriptions_config VALUES (?, ?)",
                               (str(self.channel_id), str(message.id)))
                    db.commit()
                self.message_id = message.id
            await message.add_reaction(EMOJI)
            # Restore subscriptions from existing reactions after a restart.
            # A removal while offline must be repeated after the bot reconnects.
            message = await channel.fetch_message(self.message_id)
            reaction = next((r for r in message.reactions if str(r.emoji) == EMOJI), None)
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        member = await channel.guild.fetch_member(user.id)
                        await member.add_roles(role, reason="NCLEXapro alert subscription")
            log.info("Subscriptions ready; starting daily posting scheduler.")
            await asyncio.to_thread(daily_scheduler)
        except Exception:
            log.exception("NCLEXapro setup or scheduler failed. Fix configuration and restart.")
            await self.close()

    async def apply_reaction(self, payload, subscribe):
        if (payload.message_id != self.message_id or payload.channel_id != self.channel_id
                or str(payload.emoji) != EMOJI or payload.user_id == self.user.id):
            return
        guild = self.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(self.role_id)
        if role is None:
            return
        try:
            member = await guild.fetch_member(payload.user_id)
            if member.bot:
                return
            if subscribe:
                await member.add_roles(role, reason="Subscribed to NCLEXapro alerts")
            else:
                await member.remove_roles(role, reason="Unsubscribed from NCLEXapro alerts")
        except discord.HTTPException:
            log.exception("Could not update alert subscription; check role permissions.")

    async def on_raw_reaction_add(self, payload):
        await self.apply_reaction(payload, True)

    async def on_raw_reaction_remove(self, payload):
        await self.apply_reaction(payload, False)

    async def on_thread_update(self, before, after):
        if after.parent_id == int(os.environ["DISCORD_CHANNEL_ID"]) and not before.archived and after.archived:
            log.info("Thread archived: %s (ID %s). Questions remain available in Threads → Archived/Closed.",
                     after.name, after.id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    prepare_questions()
    NCLEXapro().run(os.environ["DISCORD_BOT_TOKEN"])
