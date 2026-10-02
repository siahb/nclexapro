"""Gateway client for opt-in alerts; also starts the daily question scheduler."""
import asyncio
import logging
import os
import sqlite3
from pathlib import Path

import discord

from bot import ROOT, database_path, load_questions, main as daily_scheduler

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


def announcement_content():
    return (
        "💊 **Meet NCLEXapro — your daily dose of NCLEX practice!**\n\n"
        "**Created by Siah.**\n\n"
        f"Daily practice questions are posted in dated threads in <#{os.environ['DISCORD_CHANNEL_ID']}> "
        "with hidden answers, rationales, and explanations for every choice.\n\n"
        "**Question sources:** The current starter set contains original NCLEX-style questions. "
        "UWorld questions supplied with permission may also be included for this private study group. "
        "NCLEXapro is not affiliated with UWorld.\n\n"
        "🔒 **Please do not copy, screenshot, forward, or share these questions anywhere outside "
        "this private Discord server.**\n\n"
        "React with 💊 below to subscribe to **NCLEXapro Alerts**. "
        "Remove your reaction to unsubscribe. Only subscribers are tagged once per daily batch.\n\n"
        "The default posting time is 9 AM Pacific; your server administrator can confirm "
        "the configured schedule. Personal Discord notification settings still apply."
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
                    # Do not silently recreate a deleted announcement and lose subscriptions.
                    message = await channel.fetch_message(int(row[0]))
                    if message.content != announcement_content():
                        await message.edit(content=announcement_content(),
                                           allowed_mentions=discord.AllowedMentions.none())
                else:
                    message = await channel.send(
                        announcement_content(), allowed_mentions=discord.AllowedMentions.none())
                    db.execute("INSERT INTO subscriptions_config VALUES (?, ?)",
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    prepare_questions()
    NCLEXapro().run(os.environ["DISCORD_BOT_TOKEN"])
