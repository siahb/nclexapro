"""Post ten imported practice questions daily using Discord's REST API."""
import json
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


def send(token, channel, content, nonce, role_id=None):
    allowed_mentions = {"parse": []}
    if role_id:
        allowed_mentions["roles"] = [str(role_id)]
    payload = json.dumps({"content": content, "allowed_mentions": allowed_mentions,
                          "nonce": str(nonce), "enforce_nonce": True}).encode()
    for attempt in range(5):
        request = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel}/messages",
            data=payload, headers={"Authorization": f"Bot {token}",
                                   "Content-Type": "application/json",
                                   "User-Agent": "NCLEXapro/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)["id"]
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            delay = float(json.loads(error.read()).get("retry_after", 5))
            time.sleep(max(delay, 1))
    raise RuntimeError("Discord rate limit persisted; retry on the next scheduler pass.")


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel = os.environ["DISCORD_CHANNEL_ID"]
    zone = ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC"))
    hour, minute = map(int, os.getenv("POST_TIME", "09:00").split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("POST_TIME must be HH:MM in 24-hour time.")
    questions = load_questions(os.getenv("QUESTIONS_FILE", str(ROOT / "questions.json")))
    with sqlite3.connect(database_path()) as db:
        db.execute("CREATE TABLE IF NOT EXISTS posts (day TEXT, question_id TEXT, "
                   "message_id TEXT, PRIMARY KEY(day, question_id))")
        db.execute("CREATE TABLE IF NOT EXISTS daily_alerts (day TEXT PRIMARY KEY, message_id TEXT)")
        while True:
            now = datetime.now(zone)
            day = now.date().isoformat()
            if (now.hour, now.minute) >= (hour, minute):
                posted = {row[0] for row in db.execute(
                    "SELECT question_id FROM posts WHERE day = ?", (day,))}
                used = {row[0] for row in db.execute("SELECT question_id FROM posts")}
                candidates = [q for q in questions if q["id"] not in used]
                for q in candidates[:max(0, 10 - len(posted))]:
                    try:
                        import hashlib
                        nonce = int.from_bytes(hashlib.sha256(
                            f"{channel}:{day}:{q['id']}".encode()).digest()[:8], "big")
                        message_id = send(token, channel, render(q), nonce)
                        db.execute("INSERT INTO posts VALUES (?, ?, ?)",
                                   (day, q["id"], message_id))
                        db.commit()
                        posted.add(q["id"])
                        time.sleep(1)
                    except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                        print(f"Post failed ({type(error).__name__}); retrying in 60 seconds.", flush=True)
                        break
                if len(posted) < 10 and not candidates:
                    print("Question bank exhausted. Import more questions and restart.", flush=True)
                role_id = os.getenv("ALERTS_ROLE_ID")
                if posted and role_id and not db.execute(
                    "SELECT 1 FROM daily_alerts WHERE day = ?", (day,)
                ).fetchone():
                    try:
                        import hashlib
                        nonce = int.from_bytes(hashlib.sha256(
                            f"{channel}:{day}:alert".encode()).digest()[:8], "big")
                        message_id = send(token, channel,
                            f"<@&{role_id}> 💊 Today's NCLEXapro questions are above! "
                            "Try them before revealing the answers and rationales.",
                            nonce, role_id=role_id)
                        db.execute("INSERT INTO daily_alerts VALUES (?, ?)", (day, message_id))
                        db.commit()
                    except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                        print(f"Alert failed ({type(error).__name__}); retrying in 60 seconds.", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
