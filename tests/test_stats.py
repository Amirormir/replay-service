"""Unit tests for the enrichment layer (no real .rofl needed)."""

from __future__ import annotations

import pytest

from lol_replay_stats.models import MatchResult, PlayerRole, TeamSide
from lol_replay_stats.stats import enrich


def test_enrich_returns_10_players_with_role_by_position(raw_metadata_30min):
    parsed = enrich(raw_metadata_30min)

    assert len(parsed.players) == 10
    assert len(parsed.teams) == 2

    # BLUE side, ordered by position_in_team
    blue = [p for p in parsed.players if p.side == TeamSide.BLUE]
    blue.sort(key=lambda p: p.position_in_team)
    assert [p.role for p in blue] == [
        PlayerRole.TOP,
        PlayerRole.JUNGLE,
        PlayerRole.MID,
        PlayerRole.ADC,
        PlayerRole.SUPPORT,
    ]
    # The synthetic data puts Blue_Top in position 0 → role TOP
    assert blue[0].riot_name == "Blue_Top"
    assert blue[0].champion_internal == "Sett"

    # Same for RED side — first red player (Red_Top) is TOP, etc.
    red = [p for p in parsed.players if p.side == TeamSide.RED]
    red.sort(key=lambda p: p.position_in_team)
    assert [p.role for p in red] == [
        PlayerRole.TOP,
        PlayerRole.JUNGLE,
        PlayerRole.MID,
        PlayerRole.ADC,
        PlayerRole.SUPPORT,
    ]
    assert red[0].riot_name == "Red_Top"
    assert red[0].champion_internal == "MonkeyKing"


def test_enrich_computes_per_minute_stats(raw_metadata_30min):
    parsed = enrich(raw_metadata_30min)

    # Game length = 30 min exactly. Blue_Mid: 30_000 damage / 30 min = 1000 DPM.
    mid_blue = next(
        p for p in parsed.players if p.side == TeamSide.BLUE and p.role == PlayerRole.MID
    )
    assert mid_blue.enriched.damage_per_min == pytest.approx(1000.0)
    # gold 12_000 / 30 = 400 GPM
    assert mid_blue.enriched.gold_per_min == pytest.approx(400.0)
    # cs = 240 + 10 = 250 / 30 = 8.33 cs/min
    assert mid_blue.enriched.cs_per_min == pytest.approx(8.33, rel=1e-3)
    # KDA = (5+6) / max(1, 1) = 11.0
    assert mid_blue.enriched.kda == pytest.approx(11.0)


def test_kill_participation_and_shares_sum_to_one_per_team(raw_metadata_30min):
    parsed = enrich(raw_metadata_30min)

    for side in (TeamSide.BLUE, TeamSide.RED):
        side_players = [p for p in parsed.players if p.side == side]
        damage_share = sum(p.enriched.damage_share for p in side_players)
        gold_share = sum(p.enriched.gold_share for p in side_players)
        # Allow rounding tolerance (each value rounded to 4 dp).
        assert damage_share == pytest.approx(1.0, abs=5e-4)
        assert gold_share == pytest.approx(1.0, abs=5e-4)

    # KP for blue: each player's (K+A) summed / team kills.
    # Sum over team of (K+A) = 2*team_kills (every kill counts for killer + each assister)
    # so KP sum > 1 in general — assert each KP is ≤ 1.5 (the Pydantic upper bound).
    for p in parsed.players:
        assert 0.0 <= p.enriched.kill_participation <= 1.5


def test_team_aggregates_and_result(raw_metadata_30min):
    parsed = enrich(raw_metadata_30min)

    blue = next(t for t in parsed.teams if t.side == TeamSide.BLUE)
    red = next(t for t in parsed.teams if t.side == TeamSide.RED)

    assert blue.result == MatchResult.WIN
    assert red.result == MatchResult.LOSS
    # 5 players × 12_000 gold = 60_000
    assert blue.total_gold == 60_000
    # 5+4+5+4+2 = 20 kills
    assert blue.total_kills == 20
    # 1+3+2+3+1 = 10 kills
    assert red.total_kills == 10


def test_prisma_payload_maps_to_player_match_stats_shape(raw_metadata_30min):
    """The `.prisma` sub-object must carry exactly the fields the Next.js
    side will write into the PlayerMatchStats Prisma model (minus FKs)."""
    parsed = enrich(raw_metadata_30min)

    blue_top = next(
        p for p in parsed.players if p.side == TeamSide.BLUE and p.role == PlayerRole.TOP
    )
    payload = blue_top.prisma.model_dump()

    assert set(payload.keys()) == {
        "champion",
        "kills",
        "deaths",
        "assists",
        "cs",
        "gold",
        "damage",
        "visionScore",
        "side",
        "result",
    }
    assert payload["champion"] == "Sett"
    assert payload["kills"] == 5
    assert payload["deaths"] == 2
    assert payload["cs"] == 220  # 200 + 20
    assert payload["side"] == "BLUE"
    assert payload["result"] == "WIN"


def test_enrich_preserves_item_slots(raw_metadata_30min):
    parsed = enrich(raw_metadata_30min)

    blue_top = next(
        p for p in parsed.players if p.side == TeamSide.BLUE and p.role == PlayerRole.TOP
    )

    assert blue_top.items == [1001, 3071, 3047, 3065, 1028, 2055, 3364]


def test_enrich_handles_scientific_notation_strings():
    """Riot sometimes encodes large ints as strings in scientific notation."""
    raw = {
        "rofl_version": "ROFL2",
        "game": {"gameLength": "1.8E6"},  # 1_800_000 ms = 30 min as string
        "players": [
            {
                "NAME": f"P{i}",
                "TEAM": 100 if i < 5 else 200,
                "SKIN": "Sett",
                "WIN": "Win" if i < 5 else "Fail",
                "CHAMPIONS_KILLED": "5",  # int as string
                "NUM_DEATHS": 1,
                "ASSISTS": 5,
                "MINIONS_KILLED": "200",
                "NEUTRAL_MINIONS_KILLED": 0,
                "GOLD_EARNED": "1.2E4",  # 12_000 in scientific notation
                "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": 15_000,
            }
            for i in range(10)
        ],
    }
    parsed = enrich(raw)
    assert parsed.game.duration_seconds == 1800
    # gold 12_000 / 30 min = 400
    assert parsed.players[0].enriched.gold_per_min == pytest.approx(400.0)
    assert parsed.players[0].prisma.kills == 5


def test_enrich_rejects_non_ten_players(raw_metadata_30min):
    bad = dict(raw_metadata_30min)
    bad["players"] = raw_metadata_30min["players"][:9]
    with pytest.raises(ValueError, match="10 players"):
        enrich(bad)
