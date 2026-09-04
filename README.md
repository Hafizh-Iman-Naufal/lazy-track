# LazyTrack

AI-assisted Jira worklog management CLI with deterministic scheduling and safety-first design.

## Features

- Read Jira issues assigned to the current user
- Calculate expected daily and weekly working hours
- Account for leave, holidays, weekends, and overtime
- Plan worklog allocations with full preview before any changes
- Conversational CLI with AI assistance (Gemini, DeepSeek, MiniMax)
- Audit trail and undo support
- Safety-first: AI never directly mutates Jira

## What LazyTrack Does NOT Do

- Create/delete issues
- Modify manually-created worklogs
- Transition issues or change fields
- Access Jira beyond worklogs
- Skip user confirmation for any write operation

## Installation

### 1. Create virtual environment with uv

```bash
uv venv .venv
source .venv/bin/activate  # Linux/macOS
# Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
uv pip install -e ".[dev]"
# or without uv:
pip install -e ".[dev]"
```

### 3. Verify

```bash
lazytrack version
```

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-token
JIRA_PROJECT_KEY=SP  # optional, filters by project key

GEMINI_API_KEY=
DEEPSEEK_API_KEY=
MINIMAX_API_KEY=
```

### Jira Credentials

- **JIRA_BASE_URL** — Your Atlassian cloud domain (e.g. `https://your-domain.atlassian.net`)
- **JIRA_EMAIL** — Email used to log into your Atlassian account
- **JIRA_API_TOKEN** — Generate at [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens): click **Create API token**, label it (e.g. `lazytrack`), copy the value

Copy `config.example.toml` to `config.toml` for additional settings. Key `[jira]` options:

```toml
[jira]
base_url = "https://nucleusbi.atlassian.net"
project = "SP"          # filters by project key (optional)
include_done = false     # include Done status issues in sync (optional)
```

## Quick Start

```bash
# Show version
lazytrack version

# Show configuration
lazytrack config show

# Sync Jira issues
lazytrack sync
lazytrack sync -d              # include Done status issues
lazytrack sync -v              # verbose (print JQL and results)
lazytrack sync --no-worklogs   # skip worklog sync

# List assigned issues
lazytrack issues

# Show weekly status
lazytrack status
lazytrack status --week 2026-W36

# Add leave/holiday/overtime
lazytrack leave add 2026-09-10
lazytrack holiday add 2026-09-17 "Holiday Name"
lazytrack overtime add 2026-09-04 2

# List plans
lazytrack plan list

# Show plan details
lazytrack plan show PL-20260903-001

# Apply plan to Jira
lazytrack apply PL-20260903-001

# Dry run (no Jira changes)
lazytrack apply PL-20260903-001 --dry-run

# Undo applied plan
lazytrack undo PL-20260903-001

# View audit log
lazytrack audit

# Chat with AI assistant
lazytrack chat
```

## Safety Model

```
User Input -> AI Parser -> Intent Schema -> Planner -> Validator -> Preview -> User Confirm -> Jira Gateway -> Jira
```

- AI interprets natural language only
- Planner validates all constraints deterministically
- User always confirms before writes
- Manual worklogs never touched
- Full audit trail maintained

## Testing

```bash
python -m pytest
```

## License

MIT
