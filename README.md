# LazyTrack

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CLI](https://img.shields.io/badge/cli-lazytrack-informational)](#install)

> Worklogs are a tax. This is tax evasion, but legal.

AI-assisted Jira worklog CLI. Describe work in natural language or run commands; a planner builds a preview; you confirm; LazyTrack writes the worklogs.

**The model never calls Jira itself.**

## Features

- Natural-language **chat** or explicit CLI commands
- Deterministic planner with **preview-then-confirm** before any Jira write
- Leave, holidays, weekly status, undo, and audit
- Does **not** create, delete, or transition issues, or edit handmade worklogs

## Table of contents

- [Requirements](#requirements)
- [Install](#install)
- [Configure](#configure)
- [Quick start](#quick-start)
- [Chat](#chat)
- [Safety model](#safety-model)
- [Development](#development)
- [License](#license)

## Requirements

- Python 3.11+
- A Jira Cloud site and [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
- For `lazytrack chat`, an API key for Gemini, DeepSeek, MiniMax, OpenAI, Claude, or OpenCode

`tzdata` is a declared dependency so IANA names such as `UTC` and `America/New_York` resolve on Windows.

## Install

```bash
git clone https://github.com/YOUR_USER/lazytrack.git
cd lazytrack
uv venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
lazytrack version
```

Without uv: `pip install -e ".[dev]"`.

Run `lazytrack` or `lazytrack -h` for the command list. Every group (`config`, `leave`, `holiday`, `plan`) prints help if you omit a subcommand.

## Configure

Copy the templates. **`.env` and `config.toml` are gitignored** and must not be committed.

```bash
cp .env.example .env
cp config.example.toml config.toml
```

Fill `.env`:

```env
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-token
JIRA_PROJECT_KEY=ABC  # optional

GEMINI_API_KEY=
DEEPSEEK_API_KEY=
MINIMAX_API_KEY=
OPENAI_API_KEY=
CLAUDE_API_KEY=
# CLAUDE_API_KEY is preferred; ANTHROPIC_API_KEY is used if CLAUDE_API_KEY is empty
OPENCODE_API_KEY=
```

Edit `config.toml`. Important `[work]` and `[jira]` keys (see `config.example.toml` for the full template):

```toml
[jira]
base_url = "https://your-domain.atlassian.net"
project = "ABC"           # optional filter
include_done = false

[work]
hours_per_day = 8
weekly_target = 40
working_days = ["mon", "tue", "wed", "thu", "fri"]
timezone = "UTC"
day_start = "10:00"

[ai]
provider = "gemini"

[safety]
require_confirmation = true
```

Set `[ai] provider` to `gemini`, `deepseek`, `minimax`, `openai`, `claude`, or `opencode`. Optional `model` overrides that provider's default.

- `timezone` is the clock you plan in (relative words like `today` and weekday names use this zone).
- `day_start` is when the first worklog of a day begins (`HH:MM`, default `10:00`).
- Hours per worklog still come from the allocate request, not from `day_start`.

`lazytrack config show` prints the loaded (redacted) settings.

### How start times appear in Tempo / Jira

Jira stores an instant and renders it in **your Jira account timezone** (Profile → language and region), which can differ from `[work] timezone`.

When writing worklogs, LazyTrack keeps the planned wall clock (`10:00`, `13:00`, …) and stamps the offset from `/rest/api/3/myself` `timeZone`. A plan of 10:00 with `day_start = "10:00"` therefore shows as 10:00 in Tempo even if your Jira profile is `America/New_York` rather than `[work] timezone` (`UTC` in the example config).

If that profile zone cannot be read, LazyTrack falls back to `[work] timezone`. After chat apply, if the two zones differ, the success line names the Jira profile timezone that was used.

## Quick start

Sync once before listing issues or chatting about keys:

```bash
lazytrack sync
lazytrack issues
lazytrack status
lazytrack status --week 2026-W36
```

Useful flags: `lazytrack sync -d` (include Done), `-v` (verbose JQL), `--no-worklogs`.

`lazytrack sync` rebuilds local worklog hours from Jira for the current week plus `worklog_lookback_weeks` (default 4). Rows in that window that no longer exist in Jira are dropped; older cache rows are left alone. Allocate, apply, and undo refuse dates before that window. Restart chat after sync so `/status` reloads the cache.

Calendar and plans:

```bash
lazytrack leave add 2026-09-10
lazytrack holiday add 2026-09-17 "Holiday Name"

lazytrack plan list
lazytrack plan show PL-20260903-001
lazytrack apply PL-20260903-001
lazytrack apply PL-20260903-001 --dry-run
lazytrack undo PL-20260903-001
lazytrack audit
```

## Chat

```bash
lazytrack chat
lazytrack chat --debug
lazytrack chat --help
```

Natural language covers assigned issues, this week’s hours, allocating time, leave, and holidays. Worklog requests always show an exact **preview** first. Reply `yes` to apply it, `no` to cancel it, or describe a correction to receive a revised preview. Chat never writes a previewed plan without that explicit next-turn confirmation. Leave and holidays apply immediately and are saved locally (several dates or a range in one leave request is fine). Chat cannot run `lazytrack sync`; do that in another terminal, then restart chat so `/status` reloads the cache.

Weekly status uses ISO week IDs and the same Monday–Sunday table in CLI and chat, including a Note column (`Leave`, `Holiday`, `Weekend`). For example, `lazytrack status --week 2026-W36`, `/status 2026-W36`, and “show my status for 2026-W36” all display `Week 2026-W36: 2026-08-31 - 2026-09-06`. Two ranges in one question can render two week tables.

If `issues_cache` is empty, chat warns you to run `lazytrack sync` and still lets you use `/status` and calendar commands. Up/down recalls previous chat lines; they are saved in `~/.lazytrack/chat_history`.

### Slash commands (no AI)

| Command | Effect |
| --- | --- |
| `/help`, `help`, `?` | What chat can do |
| `/status [YYYY-Www]` | Current or selected week’s hours |
| `/issues` | Assigned issues from cache |
| `/clear` | Drop conversation history |
| `/exit`, `/quit`, `exit`, `quit`, `q` | Leave the REPL |

Anything else goes through the model, then deterministic resolution and planning. Relative dates use the configured work timezone, `remaining` durations are calculated from the stated total, and same-day issue entries are scheduled sequentially. Missing or conflicting details produce one concise clarification instead of guessed values. Timeouts retry once; unknown intents come back as a clarification instead of a crash.

### Example

```text
allocate me 8 hours on ABC-101 for today with a gap 12:00-13:00 for lunch
```

With `timezone = "UTC"` and `day_start = "10:00"`:

| Start | End | Duration |
| --- | --- | --- |
| 10:00 | 12:00 | 2h |
| 13:00 | 19:00 | 6h |

`today` and weekday names resolve in `[work] timezone`. A gap splits the day’s hours around the break.

A multi-day, multi-issue request with start times uses the same preview-then-confirm flow:

```text
> - on monday (this week), start at 10:00 UTC logged me 2 hours for ABC-101
 and logged me 8 hours for ABC-102.
- on yesterday, start at 09:00 UTC, logged me 7 hours for ABC-102 and 5 hours for ABC-103
Proposed plan preview:
Plan: PL-20260316-001

DATE         ISSUE      START-END     HOURS  TIMEZONE
--------------------------------------------------------------------
2026-03-16  ABC-101    10:00-12:00   2h    UTC
2026-03-16  ABC-102    12:00-20:00   8h    UTC
2026-03-17  ABC-102    09:00-16:00   7h    UTC
2026-03-17  ABC-103    16:00-21:00   5h    UTC
--------------------------------------------------------------------
Total: 22h
Overtime to register: 2026-03-16  2.0h
Overtime to register: 2026-03-17  4.0h

No Jira changes have been made. Reply yes to apply, no to cancel,
or describe a correction.
> yes
Applied PL-20260316-001: 4 worklog(s) written. Times are written in America/New_York, your Jira profile timezone, so they display as planned.
```

That session shows:

- Relative dates (`monday this week`, `yesterday`) resolve in `[work] timezone` inside the current Monday–Sunday week.
- One start time on a day anchors the first entry; later same-day entries continue from where the previous one ended.
- Requested hours are always planned. Extra weekday hours (and all weekend or holiday hours) show as internal overtime in the preview and are registered only after apply.
- Nothing is written until the next-turn `yes`.

## Safety model

```
User input → AI JSON → normalize/validate intent → planner → preview → confirm → Jira worklog API
```

- The model only produces structured intent; it does not call Jira.
- Chat apply uses the same `PlanExecutor` path as `lazytrack apply`.
- Manual worklogs are not modified.
- Applied plans can be undone; writes are audited.

LazyTrack does **not** create, delete, or transition issues; edit worklogs you created by hand; change issue fields; or skip confirmation when `require_confirmation` is on (default).

## Development

```bash
python -m pytest
```

Layout: `lazytrack/` is the package (CLI, planner, Jira client, chat UI); `tests/` is the pytest suite. Install with `.[dev]` so pytest is available.

## License

[MIT](LICENSE) — Copyright (c) 2026 Hafizh Naufal
