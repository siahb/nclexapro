# NCLEXapro

Daily NCLEX practice for the Los Medanos College RN program.

## Daily threads

### Ten test runs

For the configured test channel `1555389202533716009`, set `TEST_REPLAY=1` and redeploy
to immediately replay the first ten questions in a dated `Test 1` thread. Set it to `2`
and redeploy for `Test 2`, then continue up to `10`. Each replay is recorded on the persistent volume and will not
repeat after a restart, even on a later date. Partial deliveries resume in the same thread.
Set `TEST_ARCHIVE_MINUTES=60` for a one-hour automatic archive test, or `1440` for a
24-hour inactivity test. This setting applies only to newly created test threads; production
threads always use 24 hours. Leave the test thread without new messages for that period.
The bot logs thread archive events when connected; Discord performs the automatic archiving.
In Discord, verify Threads → Archived/Closed preserves questions, spoilers, and checkmarks.
Manual closing tests the archived view but does not verify the automatic timer.

Test replays do not mention the alerts role. Deleting test messages does not reset a replay.

At launch, set `TEST_REPLAY=0` and change `DISCORD_CHANNEL_ID` to the real question channel.
Its separate posting history allows the starter questions there once, then prevents repeats.
Reaction subscriptions continue in the configured announcement channel. Keep `TEST_CHANNEL_ID`
pointing to the test channel; replay attempts in another channel are rejected.
Never delete the persistent volume to reset a test.

The question channel receives one daily parent post, with the opt-in alerts role mentioned
once. Questions are posted inside its thread, named `NCLEXapro • October 1, 2026` using
the configured `BOT_TIMEZONE` (Pacific by default). Each question supports manual ✅ reactions.
Questions use teal embeds with the prompt and choices. Each has a spoiler reply for the
answer and reasoning for every option. The bot adds a ✅ reaction to each question so
members can click it directly. Reply notifications are disabled. Threads
auto-archive after 24 hours of inactivity; content is not deleted.

In the question channel, grant the bot **View Channel**, **Send Messages**,
**Read Message History**, **Create Public Threads**, **Send Messages in Threads**, **Embed Links**,
and **Add Reactions**.
Members need **View Channel**, **Read Message History**, **Send Messages in Threads**,
and **Add Reactions** to discuss and mark questions complete.

Existing posting history is preserved. Questions already posted as ordinary messages are
not reposted or moved; dated threads begin with the next unused questions. A day with no
unused questions produces no thread or ping. Keep the persistent volume attached.

The subscription announcement and daily thread posts can share one channel. Set
`ANNOUNCEMENT_CHANNEL_ID` and `DISCORD_CHANNEL_ID` to the same channel ID. Pin the
subscription announcement manually so it remains easy to find. Use the first announcement's
💊 reaction to subscribe and each question's ✅ reaction to mark personal completion.

![NCLEXapro icon](assets/icon.png)

**Status:** Ready for initial hosting setup with ten original questions included. A private bot token and Discord permissions are required. Deployment has not been verified.

### Railway

The Dockerfile installs dependencies and starts `python reaction_roles.py`.
Attach one persistent volume at `/data`, set `BOT_DATA_DIR=/data` and
`QUESTIONS_FILE=/data/questions.json`, and add the Discord variables from `.env.example`
in Railway's Variables panel. Enter your token privately there.

On first startup, a missing question bank is initialized with the ten original mixed
questions in `starter-questions.json`. Existing banks are never overwritten.
Those original questions are public; imported publisher questions remain excluded from
the repository and container image. After these ten questions have been used, add more
questions to the bank and restart. Used-question history stays on the volume.

The subscription announcement is posted automatically on first successful startup.
If startup occurs after the configured daily time, questions for today are posted immediately.
Deploy one replica only. Railway may charge for compute and persistent storage.

- [Bot overview](https://siahverse.cc/nclexapro/)
- [Terms of Service](https://siahverse.cc/nclexapro/terms/)
- [Privacy Policy](https://siahverse.cc/nclexapro/privacy/)

Posts up to ten previously unused questions daily, with answers and rationales hidden behind Discord spoilers. Requires Python 3.11 or newer; no additional packages. eWorld integration is pending identification of its API or export format.

## Setup

1. Create an application named **NCLEXapro** at https://discord.com/developers/applications and get its bot token. Set the bot's Discord display name to **NCLEXapro**. Keep the token private.
2. Invite the bot to your server using the `bot` scope and grant View Channel and Send Messages in the destination channel. No privileged intents or administrator access are needed.
3. Enable Developer Mode in Discord and copy the destination channel ID.
4. Import questions you have permission to share into `questions.json`, using the format below. Include at least ten unique questions. The bot does not scrape a paid question bank.
5. Set configuration and run:

```bash
export DISCORD_BOT_TOKEN='your-bot-token'
export DISCORD_CHANNEL_ID='1401745644175229061'
export BOT_TIMEZONE='America/Los_Angeles'
export POST_TIME='09:00'
python bot.py
```

Choose your actual IANA time zone. The process must stay running on a computer or server. A restart after the scheduled time posts the remaining questions for the current day; missed previous days are not backfilled. Restart after changing the question file. Preserve `progress.sqlite3` to retain posting history, and run only one instance per channel.

## Opt-in reaction alerts

To run both the daily scheduler and reaction subscriptions, install dependencies with
`pip install -r requirements.txt`, then use `python reaction_roles.py` as the start command.
Do not also run `python bot.py`; that would start a second scheduler.

Set `ALERTS_ROLE_ID=1555385673828147340` and
`ANNOUNCEMENT_CHANNEL_ID=1401740870428135594` alongside the other environment variables.
Use `BOT_DATA_DIR` for a persistent disk directory so announcements, alerts, and used-question
history survive deployments. Keep one running instance.

In Discord, grant the bot **Manage Roles**, and move its role above **NCLEXapro Alerts**.
For the announcement channel, allow **View Channel**, **Send Messages**,
**Read Message History**, and **Add Reactions** for the bot. Members need **View Channel**,
**Read Message History**, and **Add Reactions** to subscribe. They do not need Send Messages.
Enable **Allow anyone to mention this role** on the alerts role so the daily ping works
without granting the bot broad permission to mention everyone. Limit who can send messages
if you want to prevent other users tagging the alerts role. No privileged Gateway intents are required.

On its first successful startup the bot posts the subscription announcement without a ping,
then adds 💊. Members gain the role when they react and lose it when they remove their reaction.
The scheduler tags only that role, once per daily batch after posting questions.
It sends no subscriber ping when there are no new questions.

Role assignment requires the bot to be online. Existing reactions are reapplied on restart;
if someone removed their reaction while the bot was offline, they should add and remove it
again once it is online, or ask a moderator to remove the role. Moderators should not clear
all reactions from the announcement; bulk removal does not unsubscribe everyone automatically.

Reaction events supply a Discord user ID, which is used to look up the member and modify
their alerts role. Subscription membership is held by Discord, not an additional local
student database. The local database stores the announcement channel/message IDs and daily
alert message IDs. Personal notification and mute settings can override role notifications.

## Question format

The following is a format illustration, not clinical study content:

```json
[
  {
    "id": "source-question-001",
    "question": "Your imported question text",
    "choices": ["A. First choice", "B. Second choice", "C. Third choice", "D. Fourth choice"],
    "answer": "B. Second choice",
    "rationale": "Your imported explanation"
  }
]
```

Each question and explanation must fit Discord's 2,000-character message limit. When the bank runs out, the bot stops posting new questions until you import more. Posting records survive restarts. Discord nonce deduplication protects brief retries, but a crash after Discord accepts a message and before the database commit can still cause a duplicate on a later restart.
