# Gigi, the Girls Agent 💖

[![CI](https://github.com/tlavrova/girls-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/tlavrova/girls-agent/actions/workflows/ci.yml)

Gigi is a Telegram bot that organizes meetups for a group of friends: it runs a monthly date poll, reminds people who haven't voted, suggests what to do, and keeps a shared agenda so plans don't get lost in the chat.

The bot talks to the group in Russian; the code and docs are in English.

## Features (MVP)

| Step | What happens |
|---|---|
| 1. Date poll | On the **20th of each month** at 11:00 the bot posts a multi-choice poll with every weekend day (Sat & Sun) of next month. If a meetup for next month already exists, it skips. |
| 2. Reminders | Every day at 18:00 it mentions members who haven't voted yet. |
| 3. Date picked | After 3 days (or on `/close`) the date with the most votes wins; ties go to the earlier date. |
| 4. Activity | It offers 3 options of different kinds (restaurant / party / event), preferring ideas the group saved itself, then a starter set. A second poll runs for 48 hours. |
| 5. Agenda | The final plan is saved, pinned in the chat (if the bot is an admin), and listed by `/agenda`. |
| 6. Good night | Every evening at 23:00 a playful good-night message from a list of 40 (`goodnight.py`), no repeats until the list runs out. Set `GOODNIGHT_TIME=off` to disable. |
| 7. Chat | Mention Gigi or reply to one of her messages and she answers via an LLM (Google Gemini by default, free tier; any OpenAI-compatible API via `LLM_BASE_URL` / `LLM_MODELS`, a fallback list tried in order when a model is overloaded). Only that message and the one it replies to are sent, never the whole chat; at most `LLM_DAILY_LIMIT` answers per chat per day. Off unless `LLM_API_KEY` is set. |

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

4. Add the bot to your group, send `/start`, then `/join`. To skip waiting until the 20th, use `/poll`.

Run the tests: `python -m unittest discover tests`

## Design

```
girls_agent/
  bot.py         Telegram handlers, meetup flow, scheduled jobs
  planner.py     pure logic: dates, vote tally, idea selection (no Telegram, fully unit-tested)
  db.py          SQLite storage: chats, members, polls, votes, ideas, meetups
  config.py      settings from environment / .env
  seed_ideas.py  starter activity ideas
  goodnight.py   good-night messages
  llm.py         chat assistant: persona prompt and the OpenAI-compatible API call
```

Notes:
- Built on [python-telegram-bot](https://python-telegram-bot.org/) v21 with its JobQueue for scheduling.
- Polls are **non-anonymous**: that's the only way Telegram tells the bot who voted, which reminders depend on.
- Telegram doesn't give bots the member list of a group, so the bot remembers anyone who wrote, voted or sent `/join`.
- State lives in a single SQLite file and survives restarts. Closing a poll is atomic, so a manual `/close` and the scheduled job can't both process it.
- Everything is configurable via `.env` (see `.env.example`): time zone, meetup weekdays, poll day of the month (`POLL_DAY`, 1-28) and time, poll durations.
- On startup the bot sets its own name, profile descriptions and command menu (`set_profile` in `bot.py`), so there's nothing to fill in by hand in BotFather.

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

## Roadmap: daily and weekly messages

All times are in the configured time zone (Europe/Amsterdam by default), each message can be switched off in `.env`, and the city is configurable.

1. **Weekend picks, Mondays**: every Monday a message with **3** interesting events for the coming weekend: parties, clubs, concerts, stand-up, and something for the whole family. Each with a short description, date and time, and location. Found via an LLM with web search (or an events API) and checked to fall on that weekend.
2. **Good morning, daily at 07:00**: a greeting with a kind wish for the day plus the day's top world news in one or two sentences (from a news API or RSS feed, summarized by an LLM).

## Roadmap: AI assistant

Rule for everything below: Gigi only sees messages that mention her or reply to her. She never reads the whole chat, for privacy and to keep token use low.

1. **Memory on request**: "@Gigi запомни, я не люблю суши" saves a short fact for that person (SQLite); "@Gigi забудь про суши" removes it, `/memory` lists what she remembers about you. When someone talks to Gigi, only their own facts are added to the prompt, and meetup suggestions skip what people dislike.
2. **Reply threads**: follow a reply chain 3-5 messages back instead of one, so a conversation with Gigi feels natural while still never reading the rest of the chat.
3. **Real-time weather**: "@Gigi какая погода завтра?" or "...в Роттердаме в субботу?" gets the current weather and forecast from [Open-Meteo](https://open-meteo.com) (free, no key). The default city is configurable (Amsterdam). Gigi asks the API only when the question is about weather, and the LLM phrases the answer in her style ("бери зонт, и не спорь").
4. **Save ideas by tag**: "@Gigi сохрани идею: кафе X" (or a link) adds it to the idea bank, so nobody has to remember the `/idea` syntax.
5. **Fresh good-night wishes**: once the LLM is connected, let it write each day's good-night message, keeping the list in `goodnight.py` as a fallback.
6. **Links and screenshots**: extract name, address, date and price from a restaurant link, event page or screenshot.
7. **Smarter suggestions**: factor in date, weather, budget and past meetups when picking the 2-3 options (replacing `planner.pick_ideas`).
8. **Delegation**: "book a table for 6", "remind everyone the day before".
