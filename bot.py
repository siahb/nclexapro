"""Post ten imported practice questions daily using Discord's REST API."""
import json
import hashlib
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def database_path():
    directory = Path(os.getenv("BOT_DATA_DIR", str(ROOT)))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "progress.sqlite3"


def scheduler_database_path(channel, replay):
    base = database_path()
    test_channel = os.getenv("TEST_CHANNEL_ID", "1555389202533716009")
    if replay not in (0, 1, 2):
        raise ValueError("TEST_REPLAY must be 0, 1, or 2.")
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


def render(q):
    choices = "\n".join(q["choices"])
    # Spoilers let students attempt the question before revealing the explanation.
    answer = q["answer"].replace("||", "")
    rationale = q["rationale"].replace("||", "")
    return (f"**NCLEXapro · NCLEX practice**\n{q['question']}\n\n{choices}\n\n"
            f"**Answer:** ||{answer}||\n**Rationale:** ||{rationale}||")


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


def question_embed(q, number):
    return {
        "title": f"💊 Question {number} · {q.get('topic', 'NCLEX practice')}"[:256],
        "description": q["question"] + "\n\n" + "\n".join(q["choices"]),
        "color": 0x14B8A6,
        "footer": {"text": "React ✅ when done • Reveal the spoiler reply to review"},
    }


def explanation(q):
    answer = q["answer"].replace("||", "")
    rationale = q["rationale"].replace("||", "")
    return f"**Answer:** ||{answer}||\n**Why each option is right or wrong:**\n||{rationale}||"


def initialize_history(db):
    db.execute("CREATE TABLE IF NOT EXISTS posts (day TEXT, question_id TEXT, "
               "message_id TEXT, PRIMARY KEY(day, question_id))")
    db.execute("CREATE TABLE IF NOT EXISTS daily_threads (day TEXT, channel_id TEXT, "
               "parent_id TEXT, thread_id TEXT, PRIMARY KEY(day, channel_id))")
    db.execute("CREATE TABLE IF NOT EXISTS question_deliveries (question_id TEXT PRIMARY KEY, "
               "day TEXT, thread_id TEXT, message_id TEXT)")
    db.commit()


def daily_thread(db, token, channel, now, replay=0):
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
            "Today's practice questions are in the thread attached to this post. "
            "React ✅ to each question when you're done; reveal the hidden answers to review.",
            nonce_for(f"{channel}:{day}:thread-parent:replay-{replay}"), role_id=role_id)
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
                {"name": f"NCLEXapro • {label}", "auto_archive_duration": 1440})
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
        delivery = db.execute("SELECT day, thread_id, message_id FROM question_deliveries "
                              "WHERE question_id = ?", (q["id"],)).fetchone()
        if delivery is None:
            message_id = send(token, thread_id, "", nonce_for(f"{channel}:{day}:{q['id']}:embed:replay-{replay}"),
                              embeds=[question_embed(q, (len(used) if replay else len(posted)) + count + 1)])
            db.execute("INSERT INTO question_deliveries VALUES (?, ?, ?, ?)",
                       (q["id"], day, thread_id, message_id))
            db.commit()
            delivery = (day, thread_id, message_id)
        delivery_day, delivery_thread, message_id = delivery
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
    zone = ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC"))
    hour, minute = map(int, os.getenv("POST_TIME", "09:00").split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("POST_TIME must be HH:MM in 24-hour time.")
    questions = load_questions(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
    with sqlite3.connect(history) as db:
        initialize_history(db)
        while True:
            now = datetime.now(zone)
            day = now.date().isoformat()
            if replay or (now.hour, now.minute) >= (hour, minute):
                try:
                    count = post_daily_batch(db, token, channel, questions, now, replay=replay)
                    if count:
                        print(f"Posted {count} questions to today's thread.", flush=True)
                    if replay:
                        print(f"Test replay {replay} complete; change TEST_REPLAY for the next test.", flush=True)
                        return
                except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                    print(f"Post failed ({type(error).__name__}: {error}); retrying in 60 seconds.", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
