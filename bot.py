"""Post ten imported practice questions daily using Discord's REST API."""
import json
import re
import hashlib
import os
import sqlite3
import time
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def quiz_database():
    return database_path().with_name("quiz-tests.sqlite3")


def initialize_quiz_db(db):
    db.execute("CREATE TABLE IF NOT EXISTS quiz_runs (channel_id TEXT, run INTEGER, "
               "parent_id TEXT, thread_id TEXT, PRIMARY KEY(channel_id, run))")
    db.execute("CREATE TABLE IF NOT EXISTS quiz_messages (channel_id TEXT, run INTEGER, "
               "question_key TEXT, question_json TEXT, message_id TEXT, "
               "PRIMARY KEY(channel_id, run, question_key))")
    db.execute("CREATE TABLE IF NOT EXISTS quiz_answers (channel_id TEXT, run INTEGER, "
               "user_id TEXT, question_key TEXT, selected_json TEXT, correct INTEGER, "
               "PRIMARY KEY(channel_id, run, user_id, question_key))")
    columns = {row[1] for row in db.execute("PRAGMA table_info(quiz_messages)")}
    if "question_number" not in columns:
        db.execute("ALTER TABLE quiz_messages ADD COLUMN question_number INTEGER")
    db.commit()


def record_quiz_answer(channel_id, run, user_id, key, question, selected):
    """Atomically preserve the first answer, including concurrent clicks and restarts."""
    with sqlite3.connect(quiz_database()) as db:
        initialize_quiz_db(db)
        db.execute("BEGIN IMMEDIATE")
        inserted = db.execute(
            "INSERT OR IGNORE INTO quiz_answers VALUES (?, ?, ?, ?, ?, ?)",
            (str(channel_id), int(run), str(user_id), key, json.dumps(sorted(selected)),
             int(set(selected) == answer_letters(question)))).rowcount
        stored = db.execute(
            "SELECT selected_json FROM quiz_answers WHERE channel_id = ? AND run = ? "
            "AND user_id = ? AND question_key = ?",
            (str(channel_id), int(run), str(user_id), key)).fetchone()
        rows = db.execute(
            "SELECT a.correct, m.question_number FROM quiz_answers a JOIN quiz_messages m "
            "ON a.channel_id = m.channel_id AND a.run = m.run AND a.question_key = m.question_key "
            "WHERE a.channel_id = ? AND a.run = ? AND a.user_id = ? ORDER BY m.question_number",
            (str(channel_id), int(run), str(user_id))).fetchall()
        db.commit()
    feedback = private_feedback(question, json.loads(stored[0]))
    if not inserted:
        feedback = "**Your first answer is locked. This is your saved result—there is no undo.**\n\n" + feedback
    answered = len(rows)
    if answered >= 10:
        score = sum(row[0] for row in rows)
        missed = [f"#{row[1]}" for row in rows if not row[0]]
        feedback += (f"\n\n🎉 **Practice complete! Score: {score}/10 · {score * 10}%**\n"
                     + ("**Review missed questions:** " + ", ".join(missed) if missed
                        else "You answered every question correctly!")
                     + "\nYour score is private. First answers are final for this set.")
    else:
        feedback += f"\n\n**Progress: {answered}/10 answered.** Complete all 10 to see your score."
    return feedback


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
            "\n\n**Think carefully before pressing: your first answer is final. There is no undo.** "
            "Your choice and feedback are private. "
            "For multiple answers, select every choice before confirming the dropdown.")


def inline_answer_components(question, channel_id, run, key, protocol="v2"):
    base = f"quiz:{protocol}:{channel_id}:{run}:{key}"
    letters = [choice.split(".", 1)[0] for choice in question["choices"]]
    if len(answer_letters(question)) > 1:
        return [{"type": 1, "components": [{
            "type": 3, "custom_id": base + ":select",
            "placeholder": "Select all that apply — confirmation is final",
            "min_values": 1, "max_values": len(letters),
            "options": [{"label": choice[:100], "value": letter}
                        for choice, letter in zip(question["choices"], letters)],
        }]}]
    buttons = [{"type": 2, "style": 1, "label": letter, "custom_id": base + ":" + letter}
               for letter in letters]
    return [{"type": 1, "components": buttons[i:i + 5]}
            for i in range(0, len(buttons), 5)]


def private_feedback(question, selected):
    correct = answer_letters(question)
    result = "✅ Correct!" if set(selected) == correct else "📖 Let's review."
    return (f"**{result}**\nYour answer: {', '.join(sorted(selected))}\n"
            f"**Answer:** {', '.join(sorted(correct))}\n\n"
            f"**Rationale:**\n{question['rationale']}\n\n"
            "Only you can see this feedback. Your first answer is saved privately for scoring.")


def posting_allowed(now, hour, minute, start_date=None):
    return (start_date is None or now.date() >= start_date) and (now.hour, now.minute) >= (hour, minute)


def scheduled_post_time(now, channel, replay=0):
    # LMC launch recovery: October 2 only at 3:15 PM Pacific, then noon daily.
    if not replay and channel == "1401745644175229061":
        pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
        is_recovery_day = pacific.date() == date(2026, 10, 2)
        target_hour = 15 if is_recovery_day else 12
        target_minute = 15 if is_recovery_day else 0
        target = pacific.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        local_target = target.astimezone(now.tzinfo)
        return local_target.hour, local_target.minute
    return tuple(map(int, os.getenv("POST_TIME", "12:00").strip().split(":")))


def launch_info():
    value = os.getenv("POST_START_DATE", "2026-10-02")
    if not value:
        return ""
    start = date.fromisoformat(value)
    hour, minute = map(int, os.getenv("POST_TIME", "09:00").split(":"))
    display_time = datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p").lstrip("0")
    zone = os.getenv("BOT_TIMEZONE", "UTC")
    display_zone = "Pacific" if zone == "America/Los_Angeles" else zone
    return (f"**First questions: {start:%B} {start.day}, {start.year} at {display_time} {display_zone}.**\n\n")


def database_path():
    directory = Path(os.getenv("BOT_DATA_DIR", str(ROOT)))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "progress.sqlite3"


def scheduler_database_path(channel, replay):
    base = database_path()
    test_channel = str(int(os.getenv("TEST_CHANNEL_ID", "1555389202533716009").strip()))
    if not 0 <= replay <= 10:
        raise ValueError("TEST_REPLAY must be 0 (normal posting) or a test number from 1 to 10.")
    if replay:
        if channel != test_channel:
            raise ValueError("TEST_REPLAY is only allowed in TEST_CHANNEL_ID; set it to 0 for launch.")
        return base.with_name(f"test-replay-{channel}-{replay}.sqlite3")
    # Retain the existing test history; production channels each have their own history.
    if channel == test_channel:
        return base
    return base.with_name(f"progress-{channel}.sqlite3")


def load_questions(path):
    questions = json.loads(Path(path).read_text())
    return validate_questions(questions)


def validate_questions(questions):
    if not isinstance(questions, list) or len(questions) < 10:
        raise ValueError("Import at least 10 questions as a JSON array.")
    ids = set()
    for q in questions:
        if not isinstance(q, dict):
            raise ValueError("Each question must be an object.")
        for field in ("id", "question", "answer", "rationale"):
            if not isinstance(q.get(field), str) or not q[field].strip():
                raise ValueError(f"Every question needs a nonempty {field} string.")
        if q["id"] in ids:
            raise ValueError(f"Duplicate question ID: {q['id']}")
        ids.add(q["id"])
        if not isinstance(q.get("choices"), list) or not q["choices"] or any(
            not isinstance(c, str) or not c.strip() for c in q["choices"]
        ):
            raise ValueError(f"Question {q['id']} needs a list of choices.")
        if len(render(q)) > 2000:
            raise ValueError(f"Question {q['id']} exceeds Discord's message limit.")
    return questions


def refresh_question_feed():
    """Append new original questions without replacing private imported questions."""
    url = os.getenv("QUESTION_FEED_URL",
        "https://raw.githubusercontent.com/siahb/nclexapro/main/generated-questions.json")
    if not url:
        return 0
    request = urllib.request.Request(url, headers={"User-Agent": "NCLEXapro/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Question feed exceeds the 8 MiB size limit.")
    incoming = json.loads(raw)
    if not isinstance(incoming, list):
        raise ValueError("Question feed must be a JSON array.")
    path = Path(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
    existing = load_questions(path)
    by_id = {q["id"]: q for q in existing}
    normalize = lambda value: " ".join(value.casefold().split())
    prompts = {normalize(q["question"]) for q in existing}
    added = []
    for q in incoming:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("question"), str):
            raise ValueError("Malformed question in feed.")
        if q["id"] in by_id:
            if q != by_id[q["id"]]:
                raise ValueError(f"Feed attempts to change existing question {q['id']}.")
            continue
        prompt = normalize(q["question"])
        if prompt in prompts:
            continue
        added.append(q)
        by_id[q["id"]] = q
        prompts.add(prompt)
    if not added:
        return 0
    merged = validate_questions(existing + added)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as output:
            temporary = Path(output.name)
            json.dump(merged, output, indent=2, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return len(added)


def render(q, number=1):
    choices = "\n".join(q["choices"])
    # Spoilers let students attempt the question before revealing the explanation.
    answer = q["answer"].replace("||", "")
    rationale = q["rationale"].replace("||", "")
    return (f"💊 **Question {number:02d} / 10 · {q.get('topic', 'NCLEX practice')}**\n\n"
            f"{q['question']}\n\n{choices}\n\n"
            f"**Answer:** ||{answer}||\n\n**Rationale:**\n||{rationale}||\n\n"
            "-# ✅ Done · ❓ Unsure · ❌ Missed — choose one below; tap again to undo.")


def discord_request(token, path, payload=None, method=None):
    body = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(5):
        request = urllib.request.Request(
            f"https://discord.com/api/v10/{path}",
            data=body, method=method, headers={"Authorization": f"Bot {token}",
                                   "Content-Type": "application/json",
                                   "User-Agent": "NCLEXapro/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status == 204:
                    return {}
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            delay = float(json.loads(error.read()).get("retry_after", 5))
            time.sleep(max(delay, 1))
    raise RuntimeError("Discord rate limit persisted; retry on the next scheduler pass.")


def nonce_for(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")


def send(token, channel, content, nonce, role_id=None, embeds=None, reply_to=None, components=None):
    allowed_mentions = {"parse": [], "replied_user": False}
    if role_id:
        allowed_mentions["roles"] = [str(role_id)]
    payload = {
        "content": content, "allowed_mentions": allowed_mentions,
        "nonce": str(nonce), "enforce_nonce": True}
    if components:
        payload["components"] = components
    if embeds:
        payload["embeds"] = embeds
    if reply_to:
        payload["message_reference"] = {"message_id": str(reply_to), "fail_if_not_exists": True}
    return discord_request(token, f"channels/{channel}/messages", payload)["id"]


def archive_duration(replay):
    if not replay:
        return 1440
    minutes = int(os.getenv("TEST_ARCHIVE_MINUTES", "1440"))
    if minutes not in (60, 1440):
        raise ValueError("TEST_ARCHIVE_MINUTES must be 60 (1 hour) or 1440 (24 hours).")
    return minutes


def study_instructions(minutes=1440, interactive=False):
    hours = minutes // 60
    if interactive:
        return (
            "**How to study**\n"
            "1. Open the dated thread attached to this post.\n"
            "2. Read each question carefully, then tap an answer-letter button or use the dropdown.\n"
            "**Think carefully before pressing: your first answer is final. There is no undo.**\n"
            "3. For select-all-that-apply, select every choice before confirming the dropdown.\n"
            "4. Your result and option-by-option rationale appear privately at the bottom of the thread.\n"
            "5. Answer all 10 to see your private score and missed-question numbers. "
            "Repeat clicks show the saved result; they do not change your score.\n\n"
            "**Privacy**\n"
            "Classmates cannot see your selections or score. The bot stores your Discord ID and first answers "
            "on its private volume to preserve progress after restarts.\n\n"
            "**Older sets**\n"
            f"Threads archive after {hours} hours of inactivity; questions are kept. "
            "Open this channel → Threads → Archived/Closed, then choose the date. "
            "Reopen the thread if archived controls cannot be used."
        )
    return (
        "**How to study**\n"
        "1. Click or tap the dated thread attached to the daily post.\n"
        "2. Read a question and choose your answer before revealing anything. "
        "For select-all-that-apply, choose every correct option.\n"
        "3. Reveal the hidden **Answer** and **Rationale** in the same question message.\n"
        "4. After reviewing, choose a reaction on that message: ✅ Done, ❓ Unsure, or ❌ Missed. "
        "Click again to undo or remove your old reaction before choosing another. "
        "These are self-check markers, not graded or submitted answers.\n"
        "5. Discuss answers inside the thread to keep the main channel tidy.\n\n"
        "**Find older questions**\n"
        f"Threads automatically archive after {hours} hour{'s' if hours != 1 else ''} of inactivity; "
        "questions and reactions are kept. New messages reset the timer.\n"
        "Open this channel → **Threads** (the thread icon or channel menu) → "
        "**Archived** or **Closed**, then select the date. You can also use Discord search "
        "to find a question. Some versions label archived threads as closed."
    )


def explanation(q):
    answer = q["answer"].replace("||", "")
    rationale = q["rationale"].replace("||", "")
    return f"**Answer:** ||{answer}||\n**Rationale:**\n||{rationale}||"


def initialize_history(db):
    db.execute("CREATE TABLE IF NOT EXISTS posts (day TEXT, question_id TEXT, "
               "message_id TEXT, PRIMARY KEY(day, question_id))")
    db.execute("CREATE TABLE IF NOT EXISTS daily_threads (day TEXT, channel_id TEXT, "
               "parent_id TEXT, thread_id TEXT, PRIMARY KEY(day, channel_id))")
    db.execute("CREATE TABLE IF NOT EXISTS question_deliveries (question_id TEXT PRIMARY KEY, "
               "day TEXT, thread_id TEXT, message_id TEXT)")
    columns = {row[1] for row in db.execute("PRAGMA table_info(question_deliveries)")}
    if "format" not in columns:
        db.execute("ALTER TABLE question_deliveries ADD COLUMN format TEXT DEFAULT 'legacy'")
    db.commit()


def daily_thread(db, token, channel, now, replay=0):
    minutes = archive_duration(replay)
    day = now.date().isoformat()
    if replay:
        # Reuse this replay's original thread even after a date change or restart.
        previous = db.execute("SELECT day FROM daily_threads WHERE channel_id = ? LIMIT 1",
                              (channel,)).fetchone()
        if previous:
            day = previous[0]
    row = db.execute("SELECT parent_id, thread_id FROM daily_threads "
                     "WHERE day = ? AND channel_id = ?", (day, channel)).fetchone()
    label = f"{now:%B} {now.day}, {now.year}"
    if replay:
        label += f" • Test {replay}"
    if row is None:
        role_id = str(int(os.environ["ALERTS_ROLE_ID"].strip())) if not replay and os.getenv("ALERTS_ROLE_ID") else None
        mention = f"<@&{role_id}> " if role_id else ""
        recovery_notice = ("Sorry, a bug prevented today\'s questions from posting at 12 PM. "
                           "Here is today\'s set at 3:15 PM! Starting tomorrow, questions will post "
                           "at 12 PM Pacific every day.\n\n") if (
                               not replay and channel == "1401745644175229061"
                               and day == "2026-10-02") else ""
        parent_id = send(token, channel,
            f"{mention}💊 **NCLEXapro • {label}**\n"
            f"{recovery_notice}Open the attached thread for today's 10-question practice set.",
            nonce_for(f"{channel}:{day}:thread-parent:replay-{replay}"), role_id=role_id,
            embeds=[{"title": "Your daily dose of practice", "description": study_instructions(minutes, live_quiz_enabled(channel, now, replay)),
                     "color": 0x14B8A6, "footer": {"text": "Created by Siah • Keep questions in this private server"}}])
        db.execute("INSERT INTO daily_threads VALUES (?, ?, ?, NULL)",
                   (day, channel, parent_id))
        db.commit()
        thread_id = None
    else:
        parent_id, thread_id = row
    if thread_id is None:
        # Recover a successful thread creation if the process stopped before saving its ID.
        message = discord_request(token, f"channels/{channel}/messages/{parent_id}")
        thread = message.get("thread")
        if thread is None:
            thread = discord_request(token, f"channels/{channel}/messages/{parent_id}/threads",
                {"name": f"NCLEXapro • {label}", "auto_archive_duration": minutes})
        thread_id = str(thread["id"])
        db.execute("UPDATE daily_threads SET thread_id = ? WHERE day = ? AND channel_id = ?",
                   (thread_id, day, channel))
        db.commit()
    return thread_id


def live_quiz_enabled(channel, now, replay=0):
    return (not replay and str(channel) == "1401745644175229061"
            and now.astimezone(ZoneInfo("America/Los_Angeles")).date() >= date(2026, 10, 3))


def post_interactive_batch(db, token, channel, candidates, now, posted):
    """Deliver new live questions, preserving snapshots and existing posting history."""
    day = now.date().isoformat()
    run = int(now.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y%m%d"))
    # Validate the whole set before creating a thread or sending its alert.
    for number, question in enumerate(candidates, len(posted) + 1):
        answer_letters(question)
        if len(quiz_prompt(question, number)) > 2000 or len(question["choices"]) > 25:
            raise ValueError("Question exceeds interactive display limits.")
    thread_id = daily_thread(db, token, channel, now)
    count = 0
    for number, q in enumerate(candidates, len(posted) + 1):
        key = hashlib.sha256(q["id"].encode()).hexdigest()[:16]
        with sqlite3.connect(quiz_database()) as quiz_db:
            initialize_quiz_db(quiz_db)
            snapshot = quiz_db.execute(
                "SELECT question_json, message_id, question_number FROM quiz_messages "
                "WHERE channel_id = ? AND run = ? AND question_key = ?",
                (str(channel), run, key)).fetchone()
            if snapshot:
                q = json.loads(snapshot[0])
                number = snapshot[2]
            else:
                quiz_db.execute(
                    "INSERT INTO quiz_messages "
                    "(channel_id, run, question_key, question_json, message_id, question_number) "
                    "VALUES (?, ?, ?, ?, NULL, ?)",
                    (str(channel), run, key, json.dumps(q), number))
                quiz_db.commit()
        delivery = db.execute(
            "SELECT day, thread_id, message_id, format FROM question_deliveries "
            "WHERE question_id = ?", (q["id"],)).fetchone()
        if delivery is None:
            message_id = send(token, thread_id, quiz_prompt(q, number),
                nonce_for(f"{channel}:{day}:{q['id']}:live-quiz"),
                components=inline_answer_components(q, channel, run, key, protocol="v3"))
            db.execute(
                "INSERT INTO question_deliveries (question_id, day, thread_id, message_id, format) "
                "VALUES (?, ?, ?, ?, 'interactive')", (q["id"], day, thread_id, message_id))
            db.commit()
        else:
            # Finish an interrupted delivery without posting the question again.
            message_id = delivery[2]
        with sqlite3.connect(quiz_database()) as quiz_db:
            quiz_db.execute(
                "UPDATE quiz_messages SET message_id = ? WHERE channel_id = ? AND run = ? AND question_key = ?",
                (str(message_id), str(channel), run, key))
            quiz_db.commit()
        db.execute("INSERT INTO posts VALUES (?, ?, ?)", (day, q["id"], message_id))
        db.commit()
        count += 1
        time.sleep(1)
    return count


def post_daily_batch(db, token, channel, questions, now, replay=0):
    day = now.date().isoformat()
    posted = {row[0] for row in db.execute(
        "SELECT question_id FROM posts WHERE day = ?", (day,))}
    used = {row[0] for row in db.execute("SELECT question_id FROM posts")}
    remaining = max(0, 10 - (len(used) if replay else len(posted)))
    bank = questions[:10] if replay else questions
    candidates = [q for q in bank if q["id"] not in used][:remaining]
    if not candidates:
        return 0
    if live_quiz_enabled(channel, now, replay):
        if len(candidates) < remaining:
            print("Waiting for enough unused questions to deliver a complete 10-question scored set.", flush=True)
            return 0
        return post_interactive_batch(db, token, channel, candidates, now, posted)
    thread_id = daily_thread(db, token, channel, now, replay=replay)
    count = 0
    for q in candidates:
        delivery = db.execute("SELECT day, thread_id, message_id, format FROM question_deliveries "
                              "WHERE question_id = ?", (q["id"],)).fetchone()
        if delivery is None:
            number = (len(used) if replay else len(posted)) + count + 1
            message_id = send(token, thread_id, render(q, number),
                              nonce_for(f"{channel}:{day}:{q['id']}:combined:replay-{replay}"))
            db.execute("INSERT INTO question_deliveries (question_id, day, thread_id, message_id, format) "
                       "VALUES (?, ?, ?, ?, 'combined')", (q["id"], day, thread_id, message_id))
            db.commit()
            delivery = (day, thread_id, message_id, "combined")
        delivery_day, delivery_thread, message_id, message_format = delivery
        # Discord's PUT is idempotent: retries keep one bot reaction on the question.
        for emoji in ("%E2%9C%85", "%E2%9D%93", "%E2%9D%8C"):
            discord_request(token,
                f"channels/{delivery_thread}/messages/{message_id}/reactions/{emoji}/@me", method="PUT")
        if message_format == "legacy":
            # Finish an interrupted delivery created by the older two-message format.
            send(token, delivery_thread, explanation(q),
                 nonce_for(f"{channel}:{delivery_day}:{q['id']}:explanation:replay-{replay}"), reply_to=message_id)
        db.execute("INSERT INTO posts VALUES (?, ?, ?)", (delivery_day, q["id"], message_id))
        db.commit()
        count += 1
        time.sleep(1)
    return count


def post_weekly_poll(db, token, channel, now, replay=0):
    """One native topic poll on Sunday at noon Pacific, with no role ping."""
    pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
    if replay or pacific.weekday() != 6 or pacific.hour < 12:
        return False
    week = pacific.date().isoformat()
    db.execute("CREATE TABLE IF NOT EXISTS weekly_polls "
               "(week TEXT, channel_id TEXT, message_id TEXT, PRIMARY KEY(week, channel_id))")
    if db.execute("SELECT 1 FROM weekly_polls WHERE week = ? AND channel_id = ?",
                  (week, channel)).fetchone():
        return False
    topics = ["Pharmacology", "Adult med-surg", "Maternity & pediatrics",
              "Mental health", "Prioritization & delegation"]
    payload = {
        "content": "💊 **What would you like more practice with next week?**\n"
                   "Vote for one topic below! Voting closes in 24 hours. "
                   "Daily practice sets stay mixed. Discord polls are not anonymous.",
        "allowed_mentions": {"parse": []},
        "nonce": str(nonce_for(f"{channel}:{week}:weekly-topic-poll")),
        "enforce_nonce": True,
        "poll": {
            "question": {"text": "Which topic should we practice more?"},
            "answers": [{"poll_media": {"text": topic}} for topic in topics],
            "duration": 24,
            "allow_multiselect": False,
            "layout_type": 1,
        },
    }
    message = discord_request(token, f"channels/{channel}/messages", payload)
    db.execute("INSERT INTO weekly_polls VALUES (?, ?, ?)", (week, channel, str(message["id"])))
    db.commit()
    return True


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel = str(int(os.environ["DISCORD_CHANNEL_ID"].strip()))
    replay = int(os.getenv("TEST_REPLAY", "0"))
    history = scheduler_database_path(channel, replay)
    start_value = os.getenv("POST_START_DATE", "")
    start_date = date.fromisoformat(start_value) if start_value else None
    zone = ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC"))
    hour, minute = scheduled_post_time(datetime.now(zone), channel, replay)
    print(f"Posting schedule: {hour:02d}:{minute:02d} {zone}; channel {channel}; start date {start_date}; replay {replay}.", flush=True)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("POST_TIME must be HH:MM in 24-hour time.")
    questions = load_questions(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
    if live_quiz_enabled(channel, datetime.now(zone), replay):
        print("Live private quizzes enabled: first answers locked; private score after 10.", flush=True)
    last_refresh = None
    with sqlite3.connect(history) as db:
        initialize_history(db)
        while True:
            now = datetime.now(zone)
            day = now.date().isoformat()
            hour, minute = scheduled_post_time(now, channel, replay)
            if not replay and (last_refresh is None or time.monotonic() - last_refresh >= 300):
                last_refresh = time.monotonic()
                try:
                    added = refresh_question_feed()
                    questions = load_questions(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
                    if added:
                        print(f"Imported {added} new original questions from the daily feed.", flush=True)
                except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
                    print(f"Question feed refresh failed ({error}); retaining the current bank.", flush=True)
            if not replay and (start_date is None or now.date() >= start_date):
                try:
                    if post_weekly_poll(db, token, channel, now):
                        print("Weekly topic poll posted without an Alerts ping.", flush=True)
                except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                    print(f"Weekly poll failed ({error}); daily questions continue.", flush=True)
            if replay or posting_allowed(now, hour, minute, start_date):
                try:
                    count = post_daily_batch(db, token, channel, questions, now, replay=replay)
                    if count:
                        print(f"Posted {count} questions to today's thread.", flush=True)
                    elif not replay and not db.execute("SELECT 1 FROM posts WHERE day = ?", (day,)).fetchone():
                        print("No unused questions available; waiting for the daily feed without repeating questions.", flush=True)
                    if replay:
                        print(f"Test replay {replay} complete; change TEST_REPLAY for the next test.", flush=True)
                        return
                except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                    print(f"Post failed ({type(error).__name__}: {error}); retrying in 60 seconds.", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
