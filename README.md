# Girls Agent 💖

[![CI](https://github.com/tlavrova/girls-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/tlavrova/girls-agent/actions/workflows/ci.yml)

A Telegram bot that organizes meetups for a group of friends: it runs a monthly date poll, reminds people who haven't voted, suggests what to do, and keeps a shared agenda so plans don't get lost in the chat.

The bot talks to the group in Russian; the code and docs are in English.

## Features (MVP)

| Step | What happens |
|---|---|
| 1. Date poll | On the **last Saturday of each month** at 11:00 the bot posts a multi-choice poll with every weekend day (Sat & Sun) of next month. If a meetup for next month already exists, it skips. |
| 2. Reminders | Every day at 18:00 it mentions members who haven't voted yet. |
| 3. Date picked | After 3 days (or on `/close`) the date with the most votes wins; ties go to the earlier date. |
| 4. Activity | It offers 3 options of different kinds (restaurant / party / event), preferring ideas the group saved itself, then a starter set. A second poll runs for 48 hours. |
| 5. Agenda | The final plan is saved, pinned in the chat (if the bot is an admin), and listed by `/agenda`. |

### Commands

| Command | Action |
|---|---|
| `/join`, `/leave` | Opt in / out of reminders |
| `/poll` | Start a date poll now |
| `/remind` | Remind non-voters now |
| `/close` | Close the current poll early and announce the result |
| `/idea ресторан: Name — notes` | Save an idea (kinds: `ресторан`, `вечеринка`, `мероприятие`; anything else is a generic idea) |
| `/ideas` | List the idea bank |
| `/agenda` | Upcoming meetups |
| `/cancel` | Cancel the next meetup |

## Quick start

1. In Telegram, open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Still in BotFather: `/setprivacy` → pick your bot → **Disable**, so the bot sees regular messages and learns who's in the chat (otherwise everyone has to send `/join`).
3. Install and run (Python 3.9+):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # paste BOT_TOKEN
python -m girls_agent.bot
```

4. Add the bot to your group, send `/start`, then `/join`. To skip waiting until the end of the month, use `/poll`.

Run the tests: `python -m unittest discover tests`

## Design

```
girls_agent/
  bot.py         Telegram handlers, meetup flow, scheduled jobs
  planner.py     pure logic: dates, vote tally, idea selection (no Telegram, fully unit-tested)
  db.py          SQLite storage: chats, members, polls, votes, ideas, meetups
  config.py      settings from environment / .env
  seed_ideas.py  starter activity ideas
```

Notes:
- Built on [python-telegram-bot](https://python-telegram-bot.org/) v21 with its JobQueue for scheduling.
- Polls are **non-anonymous**: that's the only way Telegram tells the bot who voted, which reminders depend on.
- Telegram doesn't give bots the member list of a group, so the bot remembers anyone who wrote, voted or sent `/join`.
- State lives in a single SQLite file and survives restarts. Closing a poll is atomic, so a manual `/close` and the scheduled job can't both process it.
- Everything is configurable via `.env` (see `.env.example`): time zone, meetup weekdays, poll day and time, poll durations.

## Running 24/7

The bot runs as long as its process runs, so for always-on operation it needs a server. A `Dockerfile` is included.

**Free options:**
- **Oracle Cloud "Always Free"**: a small VM that stays free indefinitely. A bank card is needed for identity verification but isn't charged.
- **Google Cloud free tier**: one `e2-micro` VM in selected US regions. Also requires a card.
- **Any always-on machine at home** (old laptop, Raspberry Pi).

On a fresh Ubuntu VM, one command installs Docker, asks for the token and starts the bot (run it again to update):

```bash
curl -fsSL https://raw.githubusercontent.com/tlavrova/girls-agent/main/deploy/setup.sh -o setup.sh && bash setup.sh
```

Or manually, with Docker installed, using the prebuilt image:

```bash
docker run -d --name girls-agent --restart unless-stopped --env-file .env -v girls-agent-data:/data ghcr.io/tlavrova/girls-agent:latest
```

**Paid but simplest:** Railway (~$5/month): deploy from the GitHub repo, set `BOT_TOKEN` and `TIMEZONE` in Variables, attach a volume at `/data`.

Only one copy of the bot can run per token: stop the local one before starting it on a server.

## CI/CD

[GitHub Actions](.github/workflows/ci.yml) runs on every push and pull request:

1. **test**: runs the unit tests.
2. **docker**: builds a multi-arch image (`linux/amd64` + `linux/arm64`) and pushes it to GitHub Container Registry as `ghcr.io/tlavrova/girls-agent`. Pull requests only build, they don't push.

Tags: `latest` for `main`, `sha-<commit>` for every build, and `1.2.3` for git tags like `v1.2.3`.

To update a running server, run `deploy/setup.sh` again: it pulls the latest image and restarts the container.

## Roadmap: AI assistant

1. **Understand chat messages**: use an LLM to spot suggestions like "let's go to this café" and offer to save them to the idea bank.
2. **Links and screenshots**: extract name, address, date and price from a restaurant link, event page or screenshot.
3. **Smarter suggestions**: factor in date, weather, budget and past meetups when picking the 2-3 options (replacing `planner.pick_ideas`).
4. **Delegation**: "book a table for 6", "remind everyone the day before".
