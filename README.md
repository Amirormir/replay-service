# Nexus League — Replay Stats Microservice

Parse League of Legends `.rofl` replay files and return scoreboard stats
shaped to feed Prisma `PlayerMatchStats` rows. Built for the Nexus League
admin flow: instead of typing game stats by hand, upload the replay and let
the service extract them.

> The encrypted payload (chunks/keyframes) is **not** decoded — we only read
> the JSON metadata blob, which is enough for the scoreboard. See
> [`../scrapper.md`](../scrapper.md) for the full design notes.

## Install

```bash
cd replay-service
python -m venv .venv
source .venv/Scripts/activate    # Windows bash; on Unix: source .venv/bin/activate
pip install -e ".[dev]"
```

Python 3.11+ required.

## CLI

```bash
# Pretty-print enriched JSON to stdout
lol-stats parse "C:/Users/<you>/Documents/League of Legends/Replays/EUW1-1234567.rofl"

# Raw Riot metadata (no derived stats, no role assignment)
lol-stats parse game.rofl --raw

# Write to a file
lol-stats parse game.rofl -o out.json

# Launch the HTTP API on http://127.0.0.1:8000
lol-stats serve --port 8000 --reload
```

## HTTP API

```bash
lol-stats serve --port 8000

# In another terminal:
curl -F "file=@game.rofl" http://localhost:8000/replays | jq
curl http://localhost:8000/health
```

`GET /health` returns `{ "status": "ok", "version": "..." }`.
`POST /replays` accepts a multipart `file` (≤100 MB) and returns a
`ParsedReplay`.

## Output shape

```jsonc
{
  "game": {
    "duration_seconds": 1834,
    "duration_minutes": 30.57,
    "game_version": "14.11.589.1234",
    "game_mode": null,
    "rofl_version": "ROFL2"
  },
  "teams": [
    { "side": "BLUE", "result": "WIN",  "total_kills": 20, "total_gold": 60000, ... },
    { "side": "RED",  "result": "LOSS", "total_kills": 10, ... }
  ],
  "players": [
    {
      "position_in_team": 0,
      "role": "TOP",
      "side": "BLUE",
      "riot_name": "Blue_Top",
      "champion_internal": "Sett",
      "champion_display": null,
      "prisma": {
        "champion": "Sett", "kills": 5, "deaths": 2, "assists": 5,
        "cs": 220, "gold": 12000, "damage": 15000, "visionScore": 0,
        "side": "BLUE", "result": "WIN"
      },
      "enriched": {
        "kda": 5.0, "cs_per_min": 7.33, "gold_per_min": 400.0,
        "damage_per_min": 500.0, "kill_participation": 0.5,
        "damage_share": 0.1667, "gold_share": 0.2, ...
      }
    },
    /* ...9 more, ordered: BLUE 0..4 then RED 0..4... */
  ]
}
```

### Role-by-position convention

Within each side, the 10 players are returned in the order they appear in
the replay metadata. Position 0 = `TOP`, 1 = `JUNGLE`, 2 = `MID`, 3 = `ADC`,
4 = `SUPPORT` — for **both** teams. The admin selector should be filtered
by `role` so each champion lines up with the expected player slot.

Since `.rofl` files don't carry Riot IDs for custom games, the admin still
picks `playerId` per row. The `prisma` sub-object is exactly the data to
write into `PlayerMatchStats` (alongside the chosen `playerId`,
`matchGameId`, `teamId`).

## Integration with Nexus League (Next.js side)

In the admin flow:

1. The admin clicks **"Importer .rofl"** on a game row in the matches
   manager. The browser POSTs the file to the Next.js route handler
   `/api/admin/replays/parse` (admin-guarded), which proxies it to this
   service.
2. The Route Handler returns a validated `ParsedReplay` JSON.
3. The UI fills the 10 rows (5 BLUE / 5 RED): champion, K/D/A, CS, gold,
   damage, vision, plus game duration and the game winner. The admin still
   picks the `playerId` per row (custom games have no Riot ID).
4. On submit, the existing `match.recordResult` tRPC mutation writes the
   `MatchGame` + 10 `PlayerMatchStats` in a single `prisma.$transaction`.

### Run order locally

```bash
# Terminal 1 — the microservice (this folder)
lol-stats serve --port 8000

# Terminal 2 — Nexus League Next.js (thegardenTM/)
pnpm dev
```

The Next.js app reads `REPLAY_SERVICE_URL` from its environment
(defaulting to `http://127.0.0.1:8000` if unset).

## Tests

```bash
pytest -q
```

Unit tests in `tests/test_stats.py` use synthetic metadata — no real `.rofl`
is required to validate the enrichment logic. For end-to-end parser tests,
drop a real replay in `tests/fixtures/` (gitignored).

## What's intentionally not in the MVP

- Champion ID → display name mapping (Data Dragon) — out of scope for now.
- Persistence (SQLite/Postgres). The service is stateless; storage lives in
  the Nexus League Postgres.
- Multi-replay batch endpoint.
- Riot Tournament API.
- Decoding the encrypted payload (positions, wards, pathing). This needs an
  emulator and changes every patch — see `scrapper.md`.

## Layout reference

Header offsets for ROFL / ROFL2 were lifted from
[`fraxiinus/roflxd.cs`](https://github.com/fraxiinus/roflxd.cs), specifically:

- `Rofl.Extract.Data/Readers/RoflReader.cs` (288-byte header, lengths at 262)
- `Rofl.Extract.Data/Readers/Rofl2Reader.cs` (game version at offset 15,
  metadata at end of file)
- `Rofl.Extract.Data/Models/Rofl/Lengths.cs` (the 26-byte length struct)
