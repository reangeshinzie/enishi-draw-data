import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_draws.py"
SPEC = importlib.util.spec_from_file_location("update_draws", SCRIPT)
update_draws = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(update_draws)


def test_parse_loto6_and_loto7_blocks():
    loto6 = """
    回別 第2133回
    抽せん日 2026年8月31日
    本数字 01 11 14 20 29 38
    ボーナス数字 27
    当せん条件
    """
    loto7 = """
    回別 第692回
    抽せん日 2026年8月28日
    本数字 07 09 15 18 20 28 31
    ボーナス数字 21 34
    1等
    """

    assert update_draws.parse_month(loto6, "loto6") == [{
        "game": "loto6",
        "drawNumber": 2133,
        "drawDate": "2026-08-31",
        "mainNumbers": [1, 11, 14, 20, 29, 38],
        "bonusNumbers": [27],
    }]
    assert update_draws.parse_month(loto7, "loto7") == [{
        "game": "loto7",
        "drawNumber": 692,
        "drawDate": "2026-08-28",
        "mainNumbers": [7, 9, 15, 18, 20, 28, 31],
        "bonusNumbers": [21, 34],
    }]


def test_parse_miniloto_combined_number_block():
    text = """
    回別 第1401回
    抽せん日 2026年8月25日
    本数字( )はボーナス数字
    15 17 23 26 31 (22)
    1等
    """
    assert update_draws.parse_month(text, "miniloto") == [{
        "game": "miniloto",
        "drawNumber": 1401,
        "drawDate": "2026-08-25",
        "mainNumbers": [15, 17, 23, 26, 31],
        "bonusNumbers": [22],
    }]


def test_seed_feed_is_valid():
    path = Path(__file__).resolve().parents[1] / "public" / "draws.json"
    feed = update_draws.validate_feed(json.loads(path.read_text(encoding="utf-8")))
    published = {(draw["game"], draw["drawNumber"]): draw for draw in feed["draws"]}
    assert published[("loto6", 2133)]["mainNumbers"] == [1, 11, 14, 20, 29, 38]
    assert published[("loto7", 692)]["mainNumbers"] == [7, 9, 15, 18, 20, 28, 31]


def test_invalid_numbers_are_rejected():
    draw = {
        "game": "loto6",
        "drawNumber": 2133,
        "drawDate": "2026-08-31",
        "mainNumbers": [1, 1, 14, 20, 29, 38],
        "bonusNumbers": [27],
    }
    with pytest.raises(update_draws.ValidationError):
        update_draws.validate_draw(draw)
