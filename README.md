# KiranaCrew

**Voice-first bookkeeping for the corner shop. Speak a sale into Telegram, and the books are done.**

Every kirana store in India runs on trust and memory. "Ramesh took five kilos of rice on credit" lives in the
shopkeeper's head, or in a paper diary that nobody adds up until it is too late. Accounting apps exist, but they
demand typing, English, menus, and time that a shopkeeper serving ten customers at once does not have.

KiranaCrew removes all of that. The shopkeeper sends a voice note in Hindi or Hinglish to a Telegram bot:

> *"Ramesh Kumar ne paanch kilo chawal udhaar pe liya"*

Seconds later the bot replies with what it understood, stock is reduced by 5 kg, ₹250 is added to Ramesh's
credit account, and the shopkeeper is back to serving customers. If something is unclear (two customers called
Ramesh? a product it has never heard of?), the bot asks with a tap-able button instead of guessing.

It runs on a plain laptop, costs ₹0 per month, and works offline for everything except Telegram itself.

---

## Why it stands out

- **Zero-typing input.** Voice notes in the language people actually speak: Hindi, Hinglish, numbers like
  *"dhai sau"* (250) and *"paanch kilo"* (5 kg).
- **It never guesses with money.** The AI is kept away from arithmetic and IDs. Every rupee is computed by
  ordinary code in integer paise. Across 185 evaluation sentences, the number of wrong entries silently written
  to the books is **zero**.
- **Undo, always.** Every booking comes with an Undo button. Mistakes become reversal entries, never deletions,
  so the ledger is always auditable.
- **Duplicate-proof.** Telegram may deliver the same message twice; the phone may retry; the worker may crash
  mid-way. Five independent safety layers make sure one voice note becomes exactly one entry.
- **The catalog teaches itself.** Say a product the bot does not know, tap *➕ Naya item*, answer one or two
  questions, and from then on the word you used is an alias for it.
- **It runs the shop while the shopkeeper sleeps.** Four n8n workflows handle the boring-but-vital routine: the
  9 pm daily summary, low-stock alerts every two hours, a health alert if a voice note gets stuck, and the nightly
  forecast/insights jobs. All of it read-only, none of it able to touch a rupee.
- **Fully local.** Speech recognition, language understanding, database, dashboard, automation: everything runs
  in Docker and Python on the shopkeeper's own machine. No subscription, no data leaving the shop.

---

## How a voice note becomes a ledger entry

Think of it as an assembly line with six stations. Each station has one job and hands its result to the next.

<img width="1920" height="819" alt="ChatGPT Image Sep 19, 2026, 01_21_30 AM" src="https://github.com/user-attachments/assets/d50442d1-95cf-4eab-8534-19fbbbb7dfe0" />


1. **Listen: Whisper turns speech into text.** The audio clip is transcribed on the laptop's CPU by
   faster-whisper. Hindi, Hinglish and code-switching mid-sentence are all fine.

2. **Tidy up: the normalizer makes the text boring.** Spoken numbers become digits (*"paanch"* → 5,
   *"dhai sau"* → 250), units are standardised (*"kilo"*, *"kg"*, *"kilogram"* → kg), and Hindi/English spellings
   of the same word are unified. Deterministic, unit-tested, and it makes the next step far easier.

3. **Understand: rules first, AI for the leftovers.** A hand-written rules parser handles the everyday sentence
   shapes (*X ne Y kilo Z udhaar liya*, *X ne 200 diya*, *Z ka stock aaya*) instantly and predictably. Only when
   the rules come back incomplete does a small local language model (Ollama running Qwen 2.5) step in, and even
   then it is only allowed to point at words that are already in the transcript. If it "invents" a number, the
   result is thrown away.

4. **Match: names become records.** *"Ramesh"* is matched against the shop's customers, *"chawal"* against the
   product catalog and its aliases. Exact match, fuzzy match, or a short list of candidates for the shopkeeper
   to pick from.

5. **Decide: commit, ask, or reject.** If everything is unambiguous, book it. If something is missing or
   ambiguous, send Telegram buttons (*Ramesh Kumar or Ramesh Verma? · ➕ Naya customer*). If the sentence is not a
   transaction at all, say so politely.

6. **Book it: one transaction, every table.** Stock movement, credit ledger and the transaction record are
   written together or not at all. Balances are derived from the ledger, never stored and edited, so they cannot
   drift.

The shopkeeper sees only a friendly reply with an Undo button. The whole trip typically takes a few seconds.

---

## The tools, and what each one is for

### Telegram: the entire user interface
No app to install, no login screen, no training. Shopkeepers already use Telegram or WhatsApp-style chat, and
voice notes are one long-press away. Inline buttons give us a form UI without building one. Commands like
`/add_product Maggi packet 14`, `/products`, `/customers` and `/dashboard` cover the rest.

### Whisper (faster-whisper): ears
OpenAI's Whisper model, run locally through the faster-whisper runtime. It is the reason the bot understands
*"paanch kilo chawal"* spoken in a noisy shop. It loads once inside the worker and transcribes on CPU in a couple
of seconds per clip. If the laptop is struggling, a single environment variable switches speech recognition to
Groq's hosted Whisper instead.

### Ollama + Qwen 2.5 (3B): the careful reader
A small language model that runs on the laptop. It is used *only* for sentences the rules parser could not fully
handle, and its answer is checked against the transcript before it is trusted. Because it is local, there is no
per-call cost and no customer data leaves the machine. Groq and Gemini are wired in as optional fallbacks.

### PostgreSQL: the source of truth
One database holds shops, customers, products, aliases, stock movements, the credit ledger, every message and
every AI call made. Row-level locking (`FOR UPDATE SKIP LOCKED`) is what lets the job queue live inside Postgres
with no extra broker, and unique constraints are what make duplicate deliveries harmless.

### n8n: the shop's night-shift assistant
Bookkeeping is only half the job. The other half is remembering to check the books: what sold today, what is
about to run out, whether anything broke. That half is handed to **n8n**, a visual, self-hosted automation tool
that runs in the same Docker stack. It gives KiranaCrew a scheduler, retries, an execution log and a drag-and-drop
editor for free, so none of that had to be written by hand, and the shopkeeper (or whoever operates the stack) can
change a schedule or a message without touching Python.

Four workflows ship as version-controlled JSON in [`workflows/`](workflows/), ready to import:

| Workflow | Runs | What it does |
|---|---|---|
| **Daily summary** | every day, 9 pm | Asks the API for the day's numbers and sends the owner a Telegram digest: sales, cash vs udhaar, and who owes what |
| **Low-stock watch** | every 2 hours | Runs one `SELECT` against a read-only view; anything under its reorder level becomes a Telegram alert |
| **Failed-message alert** | every 15 minutes | Polls `/dev/stats`; if a voice note is stuck or the worker heartbeat is stale, the operator hears about it before the shopkeeper does |
| **Nightly jobs** | every day, 2 am (+ Sundays) | Enqueues the demand-forecast job nightly and the CrewAI weekly-insights job on Sundays |

Each workflow is a straight line of three or four nodes (schedule → HTTP or Postgres → condition → Telegram), and
each carries a sticky note stating the one rule it must obey:

> **n8n is read-only by design.** Its Postgres credential is a role that can only `SELECT`. The API endpoints it
> is allowed to call can only *read* stats, *send* a summary, or *enqueue* a job for the worker. There is no path
> from n8n to `transactions`, so a misconfigured workflow can wake you up at 3 am, but it can never alter the
> ledger. This is a deliberate decision, recorded in
> [ADR-004](docs/decisions/ADR-004-n8n-scope.md).

Start it with `.\scripts\start_demo.ps1 -Tools`, open `http://localhost:5678`, import the four JSON files and
add three credentials (admin key, bot token, read-only Postgres). Full steps in
[`workflows/README.md`](workflows/README.md).

### CrewAI: the weekly advisor
Once a week, KiranaCrew writes the shopkeeper a short advisory note in Hinglish. This is the one place where AI
agents are genuinely useful, so it is the one place they are used. Three CrewAI agents collaborate:

- a **Credit-risk analyst** looks at whose balance is climbing and who has stopped paying,
- an **Inventory analyst** spots what is overstocked and what keeps running out,
- a **Shop advisor** turns those findings into five plain, actionable lines.

The agents never compute anything. Plain SQL gathers every fact first (weekly sales, top balances, slow stock),
the agents only rank, explain and phrase, and the note is saved as *unapproved* until a human okays it. Every fact
the agents were given is stored alongside the note, so any claim can be verified. If CrewAI is not installed, the
same three prompts run one after another through the normal LLM chain.

### StatsForecast: demand forecasting that has to earn its place
A nightly job fits a statistical model (AutoETS) to each product's sales history and compares it against the
simplest possible baseline ("next week looks like last week"). The forecast is shown on the dashboard **only if
it beats the baseline** on a rolling test. No forecast is better than a misleading one.

### Flutter web dashboard: a glance at the shop
A read-only web dashboard for today's sales, outstanding credit per customer, stock levels and recent
transactions. Type `/dashboard` in the bot and it replies with a link that is valid for 24 hours. No passwords
to remember.

### Docker: one command to run it all
Postgres, the API, the worker and n8n are all described in one compose file. `.\scripts\start_demo.ps1` brings
the whole stack up from cold, migrates the schema, seeds a demo shop with 12 weeks of history, and connects the
Telegram bot.

---

## Results

### The bot in action
<!-- Voice note → reply with buttons → Undo. Drop screenshots here. -->
|                                            Voice note booked                                            |                                        Ambiguity becomes a button                                       |
| :-----------------------------------------------------------------------------------------------------: | :-----------------------------------------------------------------------------------------------------: |
|  <img src="https://github.com/user-attachments/assets/9e67fd95-603b-4529-b1ce-64bd7fdebafa" width="360">|<img src="https://github.com/user-attachments/assets/e5bcf2d3-4097-46bb-9bd0-d73081fea0c9" width="500">|


### The dashboard
<!-- Aaj · Customers · Stock · Transactions. Desktop and phone. -->

|                                                   Aaj                                                   |                                                Customers                                                |
| :-----------------------------------------------------------------------------------------------------: | :-----------------------------------------------------------------------------------------------------: |
| <img src="https://github.com/user-attachments/assets/cfac543d-f6d1-449f-8e0b-8a68d76cf533" width="400"> | <img src="https://github.com/user-attachments/assets/41c2904e-d918-49f8-a8fd-23bc88068441" width="400"> |

|                                                  Stock                                                  |                                               Transactions                                              |
| :-----------------------------------------------------------------------------------------------------: | :-----------------------------------------------------------------------------------------------------: |
| <img src="https://github.com/user-attachments/assets/68f94029-3b11-418b-a750-b48d3796ce6a" width="400"> | <img src="https://github.com/user-attachments/assets/826a86ee-0a7d-4a73-b599-8e206fbef553" width="400"> |

### n8n workflows
<!-- The editor view of one workflow and a Telegram alert it produced. -->

| Workflow in the n8n editor | Alert on Telegram |
|:---:|:---:|
| ![n8n workflow](docs/img/n8n-workflow.png) | ![n8n alert](docs/img/n8n-alert.png) |

---

## Numbers we are proud of

| | |
|---|---|
| Evaluation sentences | 185 (130 development, 55 held out and written blind) |
| Correct parses on the held-out set | 100 % (rules) · 100 % (hybrid) |
| Wrong entries silently written to the books | **0** |
| Rules-only parse latency | 0 ms (instant) |
| Monthly running cost | ₹0 |
| Cloud services required | Telegram only |

Full methodology and per-category tables: [docs/eval_report.md](docs/eval_report.md).

---

## Try it in five minutes

```powershell
copy .env.example .env          # paste your BotFather token into TELEGRAM_BOT_TOKEN
.\scripts\start_demo.ps1        # Docker stack + migrate + seed + tunnel + webhook
```

Then send `/start` to your bot, register yourself with `python -m app.cli.register_shop --telegram-user-id <id>`,
and say *"Ramesh ne 2 kilo cheeni udhaar liya"*.

No Telegram handy? The built-in simulator runs the exact same pipeline from a curl command.

Everything else, from the full setup guide to the folder map, tests, demo-day switches and the
architecture decision records, lives in **[docs/REFERENCE.md](docs/REFERENCE.md)** and
**[docs/architecture.md](docs/architecture.md)**.

---

## Design principles in one breath

Rules before AI. AI never near arithmetic. Ask, never guess. Undo, never delete. Derive balances, never store
them. Automation reads, humans approve, code writes. Runs on a laptop, costs nothing, keeps the shop's data in the
shop.
