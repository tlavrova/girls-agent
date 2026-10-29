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
| 6. Morning | Off by default. With `MORNING_TIME=07:00`, every day at that time a short message: a wish for the day, the most interesting science or tech story (BBC Science and Technology RSS, picked and retold by the LLM; it doesn't have to be from today) and the weather (Open-Meteo, free, no key). Composed once for all chats, so it costs one LLM request a day. City and feeds are configurable. |
| 7. Good night | Off by default. With `GOODNIGHT_TIME=23:00`, every evening at that time a playful good-night message from a list of 40 (`goodnight.py`), no repeats until the list runs out. |
| 8. Chat | Mention Gigi or reply to one of her messages and she answers via an LLM. She can also save, move and delete plans ("запомни, в субботу в 19:00 идём в кафе X", "перенеси на 20:30", "удали кино"; listed in `/agenda` and used to answer "какие у нас планы?" with facts; she points out a clash with another plan that day, and plans are forgotten a week after their date), save a place sent to her as a meetup idea (no `/idea` needed) and keep friends' recommendations of things and services (cosmetics, clothes, lingerie, food, shops, beauty, kids, home: "что советовали из косметики?"), create a poll from plain language ("сделай опрос, куда идём в пятницу: кино, бар или боулинг") and tell the weather for any city up to 15 days ahead (Open-Meteo), via function calling (Google Gemini by default, free tier; any OpenAI-compatible API via `LLM_BASE_URL` / `LLM_MODELS`, a fallback list tried in order when a model is overloaded or out of quota; the free tier allows only a few dozen requests per model per day, so lite models with higher limits go first). She remembers what people tell her about themselves ("запомни, я не люблю суши", "забудь про суши", `/memory`); only the author's own facts go into the prompt, and only facts people tell about themselves are saved. She is to the point, jokes only when asked or when it fits, and answers serious questions with care and practical advice, and knows who the group is from `group.txt` (see below). Only that message and the one it replies to are sent, never the whole chat; at most `LLM_DAILY_LIMIT` answers per chat per day. Off unless `LLM_API_KEY` is set. |

### Commands

| Command | Action |
|---|---|
| `/join`, `/leave` | Opt in / out of reminders |
| `/poll` | Start a date poll now |
| `/remind` | Remind non-voters now |
| `/close` | Close the current poll early and announce the result |
| `/idea ресторан: Name — notes` | Save an idea (kinds: `ресторан`, `вечеринка`, `мероприятие`; anything else is a generic idea) |
| `/ideas` | List the idea bank |
| `/unidea 12` | Delete an idea |
| `/recs` | Recommendations by category |
| `/unrec 5` | Delete a recommendation |
| `/memory` | What Gigi remembers about you |
| `/forget 3` | Forget one of those facts |
| `/agenda` | Upcoming meetups and saved plans |
| `/cancel` | Cancel the next meetup |
| `/unplan 12` | Delete a saved plan (number from `/agenda`) |

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
5. Optional: `cp group.example.txt group.txt` and describe the group: where you live and a line about each member. Gigi adds it to every prompt, so her advice fits your life (the Netherlands, not Russia). It's about real people, so `group.txt` is in `.gitignore`; edits apply without a restart. On a server, put it next to `.env` and run `deploy/setup.sh` again to mount it.

Run the tests: `python -m unittest discover tests`

## Design

```
girls_agent/
  bot.py         Telegram handlers, meetup flow, scheduled jobs
  planner.py     pure logic: dates, vote tally, idea selection (no Telegram, fully unit-tested)
  db.py          SQLite storage: chats, members, polls, votes, ideas, meetups, plans
  config.py      settings from environment / .env
  seed_ideas.py  starter activity ideas
  goodnight.py   good-night messages
  morning.py     morning message: news headlines and the LLM prompt
  weather.py     Open-Meteo forecast and city lookup
  llm.py         chat assistant: persona prompt and the OpenAI-compatible API call
```

Notes:
- Built on [python-telegram-bot](https://python-telegram-bot.org/) v21 with its JobQueue for scheduling.
- Polls are **non-anonymous**: that's the only way Telegram tells the bot who voted, which reminders depend on.
- Telegram doesn't give bots the member list of a group, so the bot remembers anyone who wrote, voted or sent `/join`.
- State lives in a single SQLite file and survives restarts. Closing a poll is atomic, so a manual `/close` and the scheduled job can't both process it.
- Everything is configurable via `.env` (see `.env.example`): time zone, meetup weekdays, poll day of the month (`POLL_DAY`, 1-28) and time, poll durations.
- **Whitelist**: a bot with a username can't be hidden from Telegram search, but with `ALLOWED_CHATS` set it ignores strangers. Updates from other chats get no reply at all (not even `/start`), other groups are left right away, scheduled messages go only to the listed chats, and private chats work only for members of those groups. Without it, the startup log lists the known chat ids to copy. Also switch off "Allow Groups" in BotFather so nobody can add the bot anywhere.
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

Group feedback (2026-10-29): several members find the scheduled posts clutter the chat and want Gigi to "just do the job". They don't like the good-night jokes ("like forwarded memes from a relative") or the bad news in the morning message, and they read news themselves. So:

1. ~~**Scheduled posts off by default**~~ (done): `GOODNIGHT_TIME` and `MORNING_TIME` are off unless set. Gigi speaks only when asked, plus the meetup flow.
2. **Ask before adding new ones**: the Friday picks below go out only if the group wants them (a quick poll first).

All times are in the configured time zone (Europe/Amsterdam by default), each message can be switched off in `.env`, and the city is configurable.

1. **Weekend picks, Fridays**: every Friday around lunch (~12:30) a short message with plans for the coming weekend, in two blocks:
   - **For us**: 3 picks: parties, clubs, concerts, stand-up, bars.
   - **With kids**: 2 family picks that work for children: shows, museums with kids' programs, workshops, festivals, petting farms.

   Each pick has a one-line description, date and time, location and a link. The events come from real web search (see "Real places" below) and are checked to fall on that weekend, so nothing is made up.

   On hold until the group says it wants a scheduled message at all (see the feedback above).

## Roadmap: real places

Everything below needs Gigi to find real, current places and events on the web instead of making them up. They share one component: web search. Every result must carry a source link, date and address.

Findings (2026-10-28):
- Gemini's Google Search grounding doesn't work through the OpenAI-compatible API ("Invalid tool type"); it needs the native `generateContent` API.
- On the free tier it isn't available for Gemini 3 models at all (HTTP 429 at once), and the 2.5 models that had free searches are closed to new keys.
- **Option A, Gemini paid tier**: turn on billing for the key. 5,000 searches a month are free (shared across Gemini 3 models), then $14 per 1,000. Tokens are paid too (3.5 Flash-Lite: $0.30 / $2.50 per 1M input / output), which comes to cents a month for one group. Bonus: on the paid tier Google doesn't use the prompts to improve its products, which also covers `group.txt`.
- **Option B, a separate search API** called as a function tool, keeping the free Gemini tier: Tavily (1,000 searches a month free, no card) or Brave ($5 of credit a month, about 1,000 searches, card required). Gemini then writes the answer from the results. More code, and it's one more key and service.
- **Chosen for now: option B with Tavily**, to stay free. It needs no card, and it returns cleaned page text with URLs, made for LLMs. About 30 searches a day is plenty for one group, weekend picks included. Its key goes into the sealed secret like the others.

1. **Weekend picks**: the Friday message above, for adults and for families with kids.
2. **Real meetup suggestions**: after the date poll, offer 3 real places (restaurant, bar, party or event) for that exact date and city, instead of the generic starter ideas. It factors in the weather, budget, what people dislike (from the facts they told Gigi) and past meetups, and still prefers ideas the group saved itself. Replaces `planner.pick_ideas` for this step.
3. **Search in chat**: "@Gigi найди итальянский ресторан в Утрехте на субботу", "что интересного в эти выходные?", "дай ссылку на сайт ресторана X", "Moeders ещё работает?". She searches the web and answers with 2-3 real options, each with an address, a link and, for events, the date. Today she answers these from the model's memory, which can be outdated or made up, and can't give links at all. A found place can be saved as an idea right away ("сохрани второй"). Searches go through the same daily limit as other answers, and the free tier's search quota needs checking.
4. **Addresses with a map link**: "какой адрес у Moeders?" today gets no answer. Answer with the address and a Google Maps link (`https://www.google.com/maps/search/?api=1&query=<name>+<city>`), and add the same link to plans with a place in `/agenda` and in "Записала 📌". The link alone works even before search, since Maps finds the place by name.
5. ~~**No made-up places**~~ (done in the prompt; search is the real fix): asked to recommend a restaurant, she suggested "Herenzicht", which couldn't be found and probably doesn't exist. Until search works, she must not name specific places from memory: offer the group's saved ideas or say she can't check yet.
6. **Facts only from sources**: once search works, questions about specific places, products and opening hours ("как тебе Moeders?", "какой крем от сухости?") go through search too, so the answer rests on real reviews and current info and carries a link, instead of a confident answer from memory.

## Roadmap: AI assistant

Rule for everything below: Gigi only sees messages that mention her or reply to her. She never reads the whole chat, for privacy and to keep token use low.

1. **Names, not "я", in plans**: "запомни, в субботу ужин в Moeders, я иду" is saved as "Moeders (я)", which nobody in the group can read. Replace "я" with the author's name (from `group.txt` or Telegram), and "мы" with the author plus the people named, before saving `who`.
2. ~~**Less joking, more work**~~ (done): the group prefers a useful assistant over a funny one. Make the default tone short and to the point, with a joke only when it fits; the item below is about quality, not quantity.
3. **Wittier humor**: today's jokes feel flat. Rework the persona prompt with examples of the wanted tone (sharp, ironic, situational, never cringe or generic), forbid stock jokes (Mondays, coffee), and try a stronger model first in `LLM_MODELS`. Collect a few real answers from the chat and compare before and after.
4. **Reply threads**: follow a reply chain 3-5 messages back instead of one, so a conversation with Gigi feels natural while still never reading the rest of the chat.
5. **Fresh good-night wishes**: dropped for now, the group doesn't want the good-night message (see "Daily and weekly messages").
6. **Links and screenshots**: extract name, address, date and price from a restaurant link, event page or screenshot.
7. **Delegation**: "book a table for 6", "remind everyone the day before".
