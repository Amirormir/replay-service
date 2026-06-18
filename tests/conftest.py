"""Shared pytest fixtures: synthetic raw-metadata factories.

These avoid the need for an actual .rofl file when unit-testing the
enrichment layer. A real .rofl is only required for parser integration tests
(see tests/fixtures/, gitignored).
"""

from __future__ import annotations

from typing import Any

import pytest


def _player(
    *,
    name: str,
    team: int,
    skin: str,
    win: bool,
    kills: int,
    deaths: int,
    assists: int,
    minions: int = 0,
    neutrals: int = 0,
    gold: int,
    damage: int,
    physical: int = 0,
    magic: int = 0,
    true_dmg: int = 0,
    damage_taken: int = 0,
    self_mitigated: int = 0,
    vision: int = 0,
    turrets: int = 0,
    dragons: int = 0,
    barons: int = 0,
    inhibs: int = 0,
    items: list[int] | None = None,
) -> dict[str, Any]:
    # If damage type breakdown isn't provided, fan it out so it sums to damage.
    if physical == 0 and magic == 0 and true_dmg == 0:
        physical = damage
    item_slots = items if items is not None else [0, 0, 0, 0, 0, 0, 0]
    data = {
        "NAME": name,
        "TEAM": team,
        "SKIN": skin,
        "WIN": "Win" if win else "Fail",
        "CHAMPIONS_KILLED": kills,
        "NUM_DEATHS": deaths,
        "ASSISTS": assists,
        "MINIONS_KILLED": minions,
        "NEUTRAL_MINIONS_KILLED": neutrals,
        "GOLD_EARNED": gold,
        "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": damage,
        "PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS": physical,
        "MAGIC_DAMAGE_DEALT_TO_CHAMPIONS": magic,
        "TRUE_DAMAGE_DEALT_TO_CHAMPIONS": true_dmg,
        "TOTAL_DAMAGE_TAKEN": damage_taken,
        "DAMAGE_SELF_MITIGATED": self_mitigated,
        "VISION_SCORE": vision,
        "TURRET_KILLS": turrets,
        "DRAGON_KILLS": dragons,
        "BARON_KILLS": barons,
        "BARRACKS_KILLED": inhibs,
    }
    for index, item_id in enumerate(item_slots[:7]):
        data[f"ITEM{index}"] = item_id
    return data


@pytest.fixture
def raw_metadata_30min() -> dict[str, Any]:
    """Synthetic 30-minute game, BLUE wins. Round numbers for easy assertions.

    BLUE team kills total = 20 (5+4+5+4+2), RED = 10 (1+3+2+3+1)
    BLUE gold total = 60_000 (12_000 each), damage total = 90_000 (15k×4 + 30k)
    """
    blue = [
        _player(name="Blue_Top",   team=100, skin="Sett",        win=True, kills=5, deaths=2, assists=5, minions=200, neutrals=20, gold=12_000, damage=15_000, items=[1001, 3071, 3047, 3065, 1028, 2055, 3364]),
        _player(name="Blue_Jgl",   team=100, skin="LeeSin",      win=True, kills=4, deaths=3, assists=8, minions=40,  neutrals=160, gold=12_000, damage=15_000, items=[1104, 1036, 3047, 3053, 3071, 2055, 3364]),
        _player(name="Blue_Mid",   team=100, skin="Ahri",        win=True, kills=5, deaths=1, assists=6, minions=240, neutrals=10, gold=12_000, damage=30_000, magic=30_000, items=[1056, 3020, 6655, 3165, 3089, 4645, 3363]),
        _player(name="Blue_Adc",   team=100, skin="Jinx",        win=True, kills=4, deaths=2, assists=4, minions=280, neutrals=20, gold=12_000, damage=15_000, items=[1055, 3006, 6672, 3085, 3031, 3094, 3363]),
        _player(name="Blue_Sup",   team=100, skin="Thresh",      win=True, kills=2, deaths=4, assists=12,minions=20,  neutrals=0,  gold=12_000, damage=15_000, vision=80, items=[3860, 3117, 3190, 3109, 3222, 2055, 3364]),
    ]
    red = [
        _player(name="Red_Top",    team=200, skin="MonkeyKing",  win=False, kills=1, deaths=4, assists=2, minions=180, neutrals=10, gold=9_000, damage=10_000, items=[1036, 3071, 3047, 3053, 6333, 2055, 3364]),
        _player(name="Red_Jgl",    team=200, skin="Graves",      win=False, kills=3, deaths=4, assists=3, minions=30,  neutrals=140, gold=9_000, damage=10_000, items=[1104, 3006, 6676, 3095, 3036, 3031, 3364]),
        _player(name="Red_Mid",    team=200, skin="Syndra",      win=False, kills=2, deaths=3, assists=4, minions=220, neutrals=10, gold=9_000, damage=20_000, magic=20_000, items=[1056, 3020, 6655, 3157, 3089, 3135, 3363]),
        _player(name="Red_Adc",    team=200, skin="Caitlyn",     win=False, kills=3, deaths=4, assists=2, minions=260, neutrals=20, gold=9_000, damage=10_000, items=[1055, 3006, 6671, 3094, 3031, 3085, 3363]),
        _player(name="Red_Sup",    team=200, skin="Lulu",        win=False, kills=1, deaths=5, assists=8, minions=20,  neutrals=0,  gold=9_000, damage=10_000, vision=60, items=[3853, 3117, 3504, 3222, 2065, 2055, 3364]),
    ]
    return {
        "rofl_version": "ROFL2",
        "game": {
            "gameLength": 30 * 60 * 1000,  # 30 minutes in ms
            "gameVersion": "14.11.589.1234",
            "lastGameChunkId": 0,
            "lastKeyFrameId": 0,
        },
        "players": blue + red,
    }
