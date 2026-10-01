# NCLEXapro

Daily NCLEX practice for the Los Medanos College RN program.

![NCLEXapro icon](assets/icon.png)

**Status:** Starter bot; not deployed. Hosting, a private bot token, and at least ten imported questions are still required.

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
