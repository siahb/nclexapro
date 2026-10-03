# NCLEXapro

Daily NCLEX practice for the Los Medanos College RN program.

## Daily question feed

The ChatGPT automation generates ten original mixed NCLEX-style questions at 8 AM Pacific
and appends them to `generated-questions.json` in this public repository. Only original
generated content belongs in that feed; do not upload imported publisher questions or secrets.

With `TEST_REPLAY=0`, the Railway bot refreshes the feed every five minutes, appends new
question IDs to `/data/questions.json`, and reloads the bank without a restart. Exact duplicate
prompts are skipped; existing question IDs cannot be changed by the feed. The generator also
compares earlier questions to avoid paraphrased repeats. Imported private questions are preserved.
Feed failures keep the current bank intact; an empty bank stops posts rather than repeating them.

The LMC schedule posts up to ten unused questions at noon Pacific (October 2, 2026 uses a one-time 3:15 PM recovery post). Generation failures
or delays can affect readiness; check the scheduled task and Railway logs before launch.
Test replays remain isolated and do not refresh the live feed.

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
In Discord, verify Threads → Archived/Closed preserves questions, spoilers, and self-check reactions.
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
Each question is one formatted message containing the prompt, choices, hidden answer,
and hidden rationale. The bot adds ✅ Done, ❓ Unsure, and ❌ Missed reactions to that
same message so members can choose a self-check marker. These are not automatic grades.
Members should remove an old marker before selecting another; multiple markers are not
automatically cleared. Threads
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
💊 reaction to subscribe and each question's ✅ / ❓ / ❌ reactions to mark personal progress.

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

## Private quiz pilot in #test-bot

The interactive pilot runs alongside the production scheduler, in the separate test channel
`1555717804936667196`. Leave production `DISCORD_CHANNEL_ID`, `ANNOUNCEMENT_CHANNEL_ID`,
and `TEST_REPLAY=0` unchanged. Set these Railway variables and deploy:

```env
QUIZ_TEST_CHANNEL_ID=1555717804936667196
QUIZ_TEST_RUN=1
```

Each run from 1 through 10 creates a separate dated test thread with the first ten bank
questions. No alerts role is mentioned, no questions are marked used in production, and
normal daily posts continue. A completed run will not repeat on restart; change the run
number to create another test. Set `QUIZ_TEST_RUN=0` to disable new test posting.
Keep one replica and the persistent volume attached.

Each public question has answer-letter buttons, or a select-all-that-apply dropdown, with no
public answer or rationale. Tap a letter to submit a single answer. For multiple answers,
select every applicable choice and confirm the dropdown to submit. Only the interacting student sees
their selection, result, and option-by-option rationale. The first submission is final for
that run: there is no undo. After all ten answers, the bot privately shows the score, percentage,
and missed question numbers. SATA requires an exact match; there is no partial credit.
The private Railway database now stores Discord user IDs, first selections, and correctness
to preserve scores across restarts. Classmates cannot query another student’s progress.
Discord still processes the interactions; this is privacy from classmates, not anonymity from Discord.

The volume stores question snapshots and test thread/message IDs so public buttons work
after restarts. Redeploying upgrades the current run's saved question messages to inline
controls without posting another set. Older unmodified buttons retain their private-panel
fallback; older panel buttons ask the user to use the updated controls. Repeated clicks show the saved
first result and never change the score.
Discord places private feedback at the bottom of the thread; selections are made beside the question. Test threads archive
after 24 hours of inactivity; find them under **Threads → Archived/Closed**. Reopen the thread
in Discord if archived controls cannot be used. Deleting test messages does not reset a run.

In #test-bot grant the bot **View Channel**, **Send Messages**, **Read Message History**,
**Create Public Threads**, and **Send Messages in Threads**. Members need **View Channel**
and **Read Message History**; allow **Send Messages in Threads** for discussion.
No administrator or privileged intents are needed.

Before migrating the production question format, verify on desktop and mobile:
single-choice correct/incorrect results; SATA exact-match grading; two users answering
the same question independently; no answers appearing in the public thread; restart and
reopen; completed-run deduplication; and archived-thread access. The existing production
question format and native weekly poll remain unchanged during this pilot.

### Scored pilot update

Every question warns: **Think carefully before pressing: your first answer is final. There is no undo.**
Dropdown confirmation submits all selected choices immediately. Existing test instructions and
question controls are updated on redeploy for the configured run. Scoring begins with new
submissions after the update; previous unsaved answers cannot be recovered. Use the next
`QUIZ_TEST_RUN` number for a fresh test set and a separate score. Earlier scores persist.
Live production posts remain in their existing format while the pilot is tested.

## Live scored quizzes — October 3, 2026 onward

The production channel `1401745644175229061` now posts a complete set of ten unused
questions at **12 PM America/Los_Angeles** using the private-answer format tested in
#test-bot. Deploy the latest main branch before noon; the scheduler waits until noon.
Keep `TEST_REPLAY=0`. Set `QUIZ_TEST_RUN=0` when finished with the separate pilot.
Do not change `DISCORD_CHANNEL_ID` to the test channel or reset the volume/history.

Live sets have single-choice letter buttons or a multi-choice dropdown directly below
the prompt. Public messages contain no correct answer or rationale. Every question warns:
**Think carefully before pressing: your first answer is final. There is no undo.**
Selections, results, rationales, progress, and the final score are sent only as ephemeral
Discord responses. The bot privately stores Discord IDs and first answers for scoring;
public buttons stay visible for other students. Select-all-that-apply uses exact-match grading.
After all ten, the student sees a score out of ten, percentage, and missed question numbers.
Repeated clicks display the saved result. Scores survive restarts and are separate for each
Pacific calendar date, user, channel, and test run.

Existing used-question history, subscriptions, older messages, test scores, and 24-hour
thread archiving are preserved. There is still one subscriber role ping per live daily set.
If fewer than ten unused questions remain, no new partial scored set or alert is sent; the
scheduler waits for the original-question feed. A partially delivered set resumes without
repeating completed deliveries. Never run more than one replica. Check deployment logs for
`Live private quizzes enabled: first answers locked; private score after 10.`

The public feed remains original content only and is imported automatically. Generated
questions do not require a code redeployment. Do not run `python bot.py` alone for scored
quizzes: the `python reaction_roles.py` Gateway client is required to handle answer controls.
The earlier spoiler/reaction format described above applies to older sets and legacy replays.
