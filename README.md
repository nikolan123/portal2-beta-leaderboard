# Portal Series Beta Speedrun Leaderboard

A leaderboard for Portal series beta speedruns.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/nikolan123/portal2-beta-leaderboard
cd portal2-beta-leaderboard
uv sync
```

Then copy .env.example to .env and configure it.

```sh
uv run uvicorn app.main:app
```

There is also a docker compose file that pulls from ghcr.
