"""Enrichment layer: turns the raw metadata dict (from `parser.parse_rofl`)
into a `ParsedReplay` with derived stats (DPM, KP%, shares, etc.) and the
Nexus-League-friendly role assignment by position.
"""

from __future__ import annotations

from typing import Any

from .models import (
    ROLE_BY_POSITION,
    EnrichedPlayerStats,
    GameStats,
    MatchResult,
    ParsedReplay,
    PlayerStats,
    PrismaPlayerStats,
    TeamSide,
    TeamStats,
)
from .parser import _to_int

# Riot encodes team ID as 100 (blue) / 200 (red).
_TEAM_ID_TO_SIDE: dict[int, TeamSide] = {
    100: TeamSide.BLUE,
    200: TeamSide.RED,
}


def enrich(raw: dict[str, Any]) -> ParsedReplay:
    """Build a `ParsedReplay` from the raw dict returned by `parse_rofl`.

    The input shape is exactly what `parser.parse_rofl` returns:
        {"rofl_version": ..., "game": {...}, "players": [<10 dicts>]}
    """
    game_raw = raw["game"]
    players_raw = raw["players"]

    if not isinstance(players_raw, list) or len(players_raw) != 10:
        raise ValueError(
            f"expected exactly 10 players in metadata, got "
            f"{len(players_raw) if isinstance(players_raw, list) else type(players_raw).__name__}"
        )

    duration_ms = _to_int(game_raw.get("gameLength", 0))
    duration_seconds = max(duration_ms // 1000, 1)
    duration_minutes = round(duration_seconds / 60, 2)
    safe_minutes = max(duration_minutes, 1 / 60)  # avoid div-by-zero on broken metadata

    # Split players by team using the TEAM field
    by_side: dict[TeamSide, list[dict[str, Any]]] = {TeamSide.BLUE: [], TeamSide.RED: []}
    for p in players_raw:
        team_id = _to_int(p.get("TEAM", 0))
        side = _TEAM_ID_TO_SIDE.get(team_id)
        if side is None:
            raise ValueError(f"unknown TEAM id {team_id!r} (expected 100 or 200)")
        by_side[side].append(p)

    for side, members in by_side.items():
        if len(members) != 5:
            raise ValueError(f"side {side.value} has {len(members)} players, expected 5")

    # Team-level aggregates (computed once, reused for each player's share)
    team_aggregates: dict[TeamSide, dict[str, int]] = {}
    for side, members in by_side.items():
        team_aggregates[side] = {
            "kills": sum(_to_int(m.get("CHAMPIONS_KILLED", 0)) for m in members),
            "gold": sum(_to_int(m.get("GOLD_EARNED", 0)) for m in members),
            "damage": sum(
                _to_int(m.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0)) for m in members
            ),
            "damage_taken": sum(_to_int(m.get("TOTAL_DAMAGE_TAKEN", 0)) for m in members),
            "turret_kills": sum(_to_int(m.get("TURRET_KILLS", 0)) for m in members),
            "dragon_kills": sum(_to_int(m.get("DRAGON_KILLS", 0)) for m in members),
            "baron_kills": sum(_to_int(m.get("BARON_KILLS", 0)) for m in members),
            "inhibitor_kills": sum(_to_int(m.get("BARRACKS_KILLED", 0)) for m in members),
        }

    # Build player rows in canonical order: BLUE positions 0..4, then RED positions 0..4
    players: list[PlayerStats] = []
    for side in (TeamSide.BLUE, TeamSide.RED):
        for position, member in enumerate(by_side[side]):
            players.append(
                _build_player(
                    member,
                    side=side,
                    position=position,
                    team_totals=team_aggregates[side],
                    safe_minutes=safe_minutes,
                )
            )

    # Team-level results (WIN = "Win" in the metadata)
    teams: list[TeamStats] = []
    for side in (TeamSide.BLUE, TeamSide.RED):
        first = by_side[side][0]
        result = _parse_result(first.get("WIN"))
        agg = team_aggregates[side]
        teams.append(
            TeamStats(
                side=side,
                result=result,
                total_kills=agg["kills"],
                total_gold=agg["gold"],
                total_damage=agg["damage"],
                total_damage_taken=agg["damage_taken"],
                turret_kills=agg["turret_kills"],
                dragon_kills=agg["dragon_kills"],
                baron_kills=agg["baron_kills"],
                inhibitor_kills=agg["inhibitor_kills"],
            )
        )

    game = GameStats(
        duration_seconds=duration_seconds,
        duration_minutes=duration_minutes,
        game_version=game_raw.get("gameVersion"),
        game_mode=game_raw.get("gameMode"),
        rofl_version=raw["rofl_version"],
    )

    result = ParsedReplay(game=game, teams=teams, players=players)
    result.assert_invariants()
    return result


def _build_player(
    raw: dict[str, Any],
    *,
    side: TeamSide,
    position: int,
    team_totals: dict[str, int],
    safe_minutes: float,
) -> PlayerStats:
    kills = _to_int(raw.get("CHAMPIONS_KILLED", 0))
    deaths = _to_int(raw.get("NUM_DEATHS", 0))
    assists = _to_int(raw.get("ASSISTS", 0))
    cs = _to_int(raw.get("MINIONS_KILLED", 0)) + _to_int(raw.get("NEUTRAL_MINIONS_KILLED", 0))
    gold = _to_int(raw.get("GOLD_EARNED", 0))
    damage = _to_int(raw.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0))
    damage_taken = _to_int(raw.get("TOTAL_DAMAGE_TAKEN", 0))
    self_mitigated = _to_int(raw.get("DAMAGE_SELF_MITIGATED", 0))
    vision = _to_int(raw.get("VISION_SCORE", 0))
    physical = _to_int(raw.get("PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS", 0))
    magic = _to_int(raw.get("MAGIC_DAMAGE_DEALT_TO_CHAMPIONS", 0))
    true_dmg = _to_int(raw.get("TRUE_DAMAGE_DEALT_TO_CHAMPIONS", 0))
    items = [_to_int(raw.get(f"ITEM{slot}", 0)) for slot in range(7)]

    team_kills = team_totals["kills"] or 1
    team_damage = team_totals["damage"] or 1
    team_gold = team_totals["gold"] or 1
    damage_type_total = physical + magic + true_dmg or 1

    enriched = EnrichedPlayerStats(
        kda=round((kills + assists) / max(deaths, 1), 2),
        cs_per_min=round(cs / safe_minutes, 2),
        gold_per_min=round(gold / safe_minutes, 2),
        damage_per_min=round(damage / safe_minutes, 2),
        damage_taken_per_min=round(damage_taken / safe_minutes, 2),
        kill_participation=round((kills + assists) / team_kills, 4),
        damage_share=round(damage / team_damage, 4),
        gold_share=round(gold / team_gold, 4),
        physical_pct=round(physical / damage_type_total, 4),
        magic_pct=round(magic / damage_type_total, 4),
        true_pct=round(true_dmg / damage_type_total, 4),
    )

    prisma = PrismaPlayerStats(
        champion=str(raw.get("SKIN") or ""),
        kills=kills,
        deaths=deaths,
        assists=assists,
        cs=cs,
        gold=gold,
        damage=damage,
        visionScore=vision,
        side=side,
        result=_parse_result(raw.get("WIN")),
    )

    return PlayerStats(
        position_in_team=position,  # type: ignore[arg-type]
        role=ROLE_BY_POSITION[position],
        side=side,
        riot_name=(str(raw["NAME"]) if raw.get("NAME") else None),
        champion_internal=prisma.champion,
        champion_display=None,
        prisma=prisma,
        enriched=enriched,
        items=items,
        raw_damage_taken=damage_taken,
        raw_self_mitigated=self_mitigated,
    )


def _parse_result(raw_win: Any) -> MatchResult:
    if isinstance(raw_win, str) and raw_win.strip().lower() == "win":
        return MatchResult.WIN
    return MatchResult.LOSS
