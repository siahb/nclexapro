"""Post ten imported practice questions daily using Discord's REST API."""
import json
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


def posting_allowed(now, hour, minute, start_date=None):
    return (start_date is None or now.date() >= start_date) and (now.hour, now.minute) >= (hour, minute)


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
    test_channel = os.getenv("TEST_CHANNEL_ID", "1555389202533716009")
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


def send(token, channel, content, nonce, role_id=None, embeds=None, reply_to=None):
    allowed_mentions = {"parse": [], "replied_user": False}
    if role_id:
        allowed_mentions["roles"] = [str(role_id)]
    payload = {
        "content": content, "allowed_mentions": allowed_mentions,
        "nonce": str(nonce), "enforce_nonce": True}
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


def study_instructions(minutes=1440):
    hours = minutes // 60
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
        role_id = os.getenv("ALERTS_ROLE_ID") if not replay else None
        mention = f"<@&{role_id}> " if role_id else ""
        parent_id = send(token, channel,
            f"{mention}💊 **NCLEXapro • {label}**\n"
            "Open the attached thread for today's 10-question practice set.",
            nonce_for(f"{channel}:{day}:thread-parent:replay-{replay}"), role_id=role_id,
            embeds=[{"title": "Your daily dose of practice", "description": study_instructions(minutes),
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


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel = os.environ["DISCORD_CHANNEL_ID"]
    replay = int(os.getenv("TEST_REPLAY", "0"))
    history = scheduler_database_path(channel, replay)
    start_value = os.getenv("POST_START_DATE", "")
    start_date = date.fromisoformat(start_value) if start_value else None
    if start_date and not replay:
        print(f"Questions held until {start_date} at {os.getenv('POST_TIME', '09:00')} {os.getenv('BOT_TIMEZONE', 'UTC')}; subscriptions remain open.", flush=True)
    zone = ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC"))
    hour, minute = map(int, os.getenv("POST_TIME", "09:00").split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("POST_TIME must be HH:MM in 24-hour time.")
    questions = load_questions(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
    last_refresh = None
    with sqlite3.connect(history) as db:
        initialize_history(db)
        while True:
            now = datetime.now(zone)
            day = now.date().isoformat()
            if not replay and (last_refresh is None or time.monotonic() - last_refresh >= 300):
                last_refresh = time.monotonic()
                try:
                    added = refresh_question_feed()
                    questions = load_questions(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
                    if added:
                        print(f"Imported {added} new original questions from the daily feed.", flush=True)
                except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
                    print(f"Question feed refresh failed ({error}); retaining the current bank.", flush=True)
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
