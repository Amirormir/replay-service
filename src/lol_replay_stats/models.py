"""Pydantic models for the parsed replay output.

The output shape is designed to feed the Nexus League admin flow:

1. The microservice returns 10 player entries ordered by team (BLUE/RED)
   and by position-in-team (0..4).
2. By Nexus League convention, position 0 = TOP, 1 = JUNGLE, 2 = MID,
   3 = ADC, 4 = SUPPORT — for BOTH teams. This pre-fills the role selector
   in the admin UI so each champion lines up with the expected player slot.
3. The admin still picks the actual `playerId` from the team roster for each
   champion (the replay has no Riot ID for custom games). The selector is
   filtered by `role` to narrow the options.
4. The `prisma` sub-object on each player carries exactly the fields needed
   to insert a `PlayerMatchStats` row once `playerId/matchGameId/teamId` are
   provided by the Next.js layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PlayerRole(str, Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MID"
    ADC = "ADC"
    SUPPORT = "SUPPORT"


class MatchResult(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"


class TeamSide(str, Enum):
    BLUE = "BLUE"
    RED = "RED"


ROLE_BY_POSITION: tuple[PlayerRole, ...] = (
    PlayerRole.TOP,
    PlayerRole.JUNGLE,
    PlayerRole.MID,
    PlayerRole.ADC,
    PlayerRole.SUPPORT,
)


class PrismaPlayerStats(BaseModel):
    """Fields that map 1:1 to Prisma `PlayerMatchStats` (minus FKs)."""

    champion: str
    kills: int
    deaths: int
    assists: int
    cs: int
    gold: int
    damage: int
    visionScore: int
    side: TeamSide
    result: MatchResult


class EnrichedPlayerStats(BaseModel):
    """Derived stats used for display only — not persisted in Prisma."""

    kda: float
    cs_per_min: float
    gold_per_min: float
    damage_per_min: float
    damage_taken_per_min: float
    kill_participation: float = Field(ge=0.0, le=1.5)
    damage_share: float = Field(ge=0.0, le=1.0)
    gold_share: float = Field(ge=0.0, le=1.0)
    physical_pct: float = Field(ge=0.0, le=1.0)
    magic_pct: float = Field(ge=0.0, le=1.0)
    true_pct: float = Field(ge=0.0, le=1.0)


class PlayerStats(BaseModel):
    """One player line in the scoreboard."""

    position_in_team: Literal[0, 1, 2, 3, 4]
    role: PlayerRole
    side: TeamSide
    riot_name: str | None = Field(
        default=None,
        description="Riot ID if present in the replay metadata (often empty for customs).",
    )
    champion_internal: str = Field(description="Riot internal champion name (the `SKIN` field).")
    champion_display: str | None = Field(
        default=None,
        description="Human-readable champion name (Data Dragon mapping, post-MVP).",
    )
    prisma: PrismaPlayerStats
    enriched: EnrichedPlayerStats
    items: list[int] = Field(
        min_length=7,
        max_length=7,
        description="Inventory slots ITEM0..ITEM6, with 0 for empty slots.",
    )
    raw_damage_taken: int
    raw_self_mitigated: int


class TeamStats(BaseModel):
    side: TeamSide
    result: MatchResult
    total_kills: int
    total_gold: int
    total_damage: int
    total_damage_taken: int
    turret_kills: int
    dragon_kills: int
    baron_kills: int
    inhibitor_kills: int


class GameStats(BaseModel):
    duration_seconds: int
    duration_minutes: float
    game_version: str | None = None
    game_mode: str | None = None
    rofl_version: Literal["ROFL", "ROFL2"]


class ParsedReplay(BaseModel):
    """Top-level response returned by the API and CLI."""

    game: GameStats
    teams: list[TeamStats]
    players: list[PlayerStats]

    def assert_invariants(self) -> None:
        """Cheap sanity checks. Raise `ValueError` on failure."""
        if len(self.players) != 10:
            raise ValueError(f"Expected 10 players, got {len(self.players)}")
        if len(self.teams) != 2:
            raise ValueError(f"Expected 2 teams, got {len(self.teams)}")

        for side in (TeamSide.BLUE, TeamSide.RED):
            side_players = [p for p in self.players if p.side == side]
            if len(side_players) != 5:
                raise ValueError(f"Side {side} has {len(side_players)} players, expected 5")
            positions = sorted(p.position_in_team for p in side_players)
            if positions != [0, 1, 2, 3, 4]:
                raise ValueError(f"Side {side} positions are {positions}, expected 0..4")
            for p in side_players:
                expected_role = ROLE_BY_POSITION[p.position_in_team]
                if p.role != expected_role:
                    raise ValueError(
                        f"Side {side} pos {p.position_in_team} has role {p.role}, "
                        f"expected {expected_role}"
                    )
