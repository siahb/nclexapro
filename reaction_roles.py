"""Gateway client for opt-in alerts; also starts the daily question scheduler."""
import asyncio
import hashlib
import json
import re
import logging
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from bot import ROOT, database_path, load_questions, study_instructions, archive_duration, launch_info, nonce_for, discord_request, main as daily_scheduler

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


QUIZ_TEST_CHANNEL = 1555717804936667196


def quiz_database():
    return database_path().with_name("quiz-tests.sqlite3")


def initialize_quiz_db(db):
    db.execute("CREATE TABLE IF NOT EXISTS quiz_runs (channel_id TEXT, run INTEGER, "
               "parent_id TEXT, thread_id TEXT, PRIMARY KEY(channel_id, run))")
    db.execute("CREATE TABLE IF NOT EXISTS quiz_messages (channel_id TEXT, run INTEGER, "
               "question_key TEXT, question_json TEXT, message_id TEXT, "
               "PRIMARY KEY(channel_id, run, question_key))")
    db.commit()


def answer_letters(question):
    # Imported answers may be 'B. ...'; generated answers use 'B' or 'A, C, E'.
    answer = question["answer"].strip()
    if re.match(r"^[A-Z]\.", answer):
        letters = {answer[0]}
    else:
        letters = set(re.split(r"[\s,]+", answer))
    valid = {choice.split(".", 1)[0] for choice in question["choices"]}
    if not letters or not letters <= valid:
        raise ValueError(f"Invalid correct answer for {question['id']}")
    return letters


def quiz_prompt(question, number=None):
    heading = f"Question {number:02d} / 10" if number is not None else "Your private practice"
    return (f"💊 **{heading} · {question.get('topic', 'NCLEX practice')}**\n\n"
            + question["question"] + "\n\n" + "\n".join(question["choices"]) +
            "\n\nChoose your answer privately below. Nothing is submitted to the class.")


def private_feedback(question, selected):
    correct = answer_letters(question)
    result = "✅ Correct!" if set(selected) == correct else "📖 Let's review."
    return (f"**{result}**\nYour answer: {', '.join(sorted(selected))}\n"
            f"**Answer:** {', '.join(sorted(correct))}\n\n"
            f"**Rationale:**\n{question['rationale']}\n\n"
            "Only you can see this feedback. Individual answers are not saved.")


class PrivateAnswerView(discord.ui.View):
    def __init__(self, question, user_id):
        super().__init__(timeout=600)
        self.question = question
        self.user_id = user_id
        self.selected = []
        self.submitted = False
        multiple = len(answer_letters(question)) > 1
        options = [discord.SelectOption(label=choice[:100], value=choice.split('.', 1)[0])
                   for choice in question["choices"]]
        select = discord.ui.Select(placeholder="Select all that apply" if multiple else "Choose one answer",
                                   options=options, min_values=1,
                                   max_values=len(options) if multiple else 1)
        async def choose(interaction):
            self.selected = list(select.values)
            await interaction.response.defer()
        select.callback = choose
        self.add_item(select)
        submit = discord.ui.Button(label="Submit answer", style=discord.ButtonStyle.success)
        submit.callback = self.submit
        self.add_item(submit)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own private answer panel.", ephemeral=True)
            return False
        return True

    async def submit(self, interaction):
        if self.submitted:
            await interaction.response.send_message("Already submitted. Open the question again to retry.", ephemeral=True)
            return
        if not self.selected:
            await interaction.response.send_message("Choose an answer from the menu first.", ephemeral=True)
            return
        self.submitted = True
        feedback = private_feedback(self.question, self.selected)
        # Existing validated questions fit the feedback limit; split defensively for imports.
        chunks = [feedback[i:i + 1900] for i in range(0, len(feedback), 1900)]
        await interaction.response.edit_message(content=chunks[0], view=None, allowed_mentions=discord.AllowedMentions.none())
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        self.stop()



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
        self.quiz_worker = None

    async def setup_hook(self):
        self.worker = asyncio.create_task(self.start_services())
        self.quiz_worker = asyncio.create_task(self.start_quiz_tests())

    async def start_quiz_tests(self):
        await self.wait_until_ready()
        try:
            run = int(os.getenv("QUIZ_TEST_RUN", "0"))
            if not 0 <= run <= 10:
                raise ValueError("QUIZ_TEST_RUN must be 0 (off) or 1 through 10.")
            if not run:
                return
            channel_id = int(os.getenv("QUIZ_TEST_CHANNEL_ID", str(QUIZ_TEST_CHANNEL)).strip())
            if channel_id != QUIZ_TEST_CHANNEL or channel_id == int(os.environ["DISCORD_CHANNEL_ID"]):
                raise ValueError("Interactive tests must use the separate #test-bot channel 1555717804936667196.")
            channel = await self.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("Quiz test channel must be a server text channel.")
            questions = await asyncio.to_thread(load_questions,
                os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
            questions = questions[:10]
            for number, q in enumerate(questions, 1):
                answer_letters(q)
                if len(quiz_prompt(q, number)) > 2000 or len(q["choices"]) > 25:
                    raise ValueError("Question exceeds interactive display limits.")
            with sqlite3.connect(quiz_database()) as db:
                initialize_quiz_db(db)
                row = db.execute("SELECT parent_id, thread_id FROM quiz_runs WHERE channel_id = ? AND run = ?",
                                 (str(channel_id), run)).fetchone()
                if row is None:
                    payload = {
                        "content": f"🧪 **NCLEXapro private quiz • Test {run}**\n"
                                   "Open the attached thread for 10 practice questions. Tap **Answer privately**, "
                                   "choose your answer(s), then **Submit answer**. Only you see your choices and feedback. "
                                   "No individual scores are stored. You can reopen a question to retry. "
                                   "If a private panel expires after 10 minutes or a bot restart, reopen it. "
                                   "This is a test; there is no subscriber ping. Threads archive after 24 hours of inactivity.",
                        "allowed_mentions": {"parse": []},
                        "nonce": str(nonce_for(f"quiz-test:{channel_id}:{run}:parent")), "enforce_nonce": True}
                    parent = await asyncio.to_thread(discord_request, os.environ["DISCORD_BOT_TOKEN"],
                                                     f"channels/{channel_id}/messages", payload)
                    db.execute("INSERT INTO quiz_runs VALUES (?, ?, ?, NULL)",
                               (str(channel_id), run, str(parent["id"])))
                    db.commit()
                    row = (str(parent["id"]), None)
                parent_id, thread_id = row
                if thread_id is None:
                    parent = await channel.fetch_message(int(parent_id))
                    thread = parent.thread
                    if thread is None:
                        now = datetime.now(ZoneInfo("America/Los_Angeles"))
                        thread = await parent.create_thread(
                            name=f"NCLEXapro • {now:%B %d, %Y} • Private Test {run}", auto_archive_duration=1440)
                    thread_id = str(thread.id)
                    db.execute("UPDATE quiz_runs SET thread_id = ? WHERE channel_id = ? AND run = ?",
                               (thread_id, str(channel_id), run))
                    db.commit()
                for number, q in enumerate(questions, 1):
                    key = hashlib.sha256(q["id"].encode()).hexdigest()[:16]
                    record = db.execute("SELECT message_id, question_json FROM quiz_messages "
                                        "WHERE channel_id = ? AND run = ? AND question_key = ?",
                                        (str(channel_id), run, key)).fetchone()
                    if record and record[0]:
                        continue
                    if record:
                        q = json.loads(record[1])
                    else:
                        db.execute("INSERT INTO quiz_messages VALUES (?, ?, ?, ?, NULL)",
                                   (str(channel_id), run, key, json.dumps(q)))
                        db.commit()
                    payload = {
                        "content": quiz_prompt(q, number), "allowed_mentions": {"parse": []},
                        "nonce": str(nonce_for(f"quiz-test:{channel_id}:{run}:{key}")), "enforce_nonce": True,
                        "components": [{"type": 1, "components": [{"type": 2, "style": 1,
                            "label": "Answer privately", "custom_id": f"quiz:v1:{channel_id}:{run}:{key}"}]}]}
                    message = await asyncio.to_thread(discord_request, os.environ["DISCORD_BOT_TOKEN"],
                                                      f"channels/{thread_id}/messages", payload)
                    db.execute("UPDATE quiz_messages SET message_id = ? "
                               "WHERE channel_id = ? AND run = ? AND question_key = ?",
                               (str(message["id"]), str(channel_id), run, key))
                    db.commit()
            log.info("Private quiz test %s ready in #test-bot. Production schedule unchanged.", run)
        except Exception:
            log.exception("Private quiz test failed; production scheduler continues. Check test-channel permissions.")

    async def on_interaction(self, interaction):
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("quiz:v1:"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            _, _, channel_id, run, key = custom_id.split(":")
            if int(channel_id) != QUIZ_TEST_CHANNEL:
                raise ValueError("Unknown quiz channel.")
            with sqlite3.connect(quiz_database()) as db:
                initialize_quiz_db(db)
                row = db.execute("SELECT question_json, message_id FROM quiz_messages "
                                 "WHERE channel_id = ? AND run = ? AND question_key = ?",
                                 (channel_id, int(run), key)).fetchone()
            if row is None or interaction.message is None or str(interaction.message.id) != row[1]:
                raise ValueError("This test question is no longer available.")
            question = json.loads(row[0])
            await interaction.followup.send(quiz_prompt(question), ephemeral=True,
                view=PrivateAnswerView(question, interaction.user.id),
                allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            log.exception("Could not open private quiz panel.")
            await interaction.followup.send("This question could not be opened. Please ask Siah to check the bot.",
                                            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


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

