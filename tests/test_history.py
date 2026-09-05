import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_history as history


def archive(game, rows):
    return history.parse_snapshot(dict(game=game, kind="archive", text="", rows=rows))


def test_numbers_leading_zero_and_repeated_digits():
    rows = archive("numbers", ["第3回\t1994年10月21日\t194\t0097"])
    assert rows[1]["digits"] == "0097"
    assert rows[0]["digits"] == "194"


def test_real_first_loto6():
    rows = archive("loto6", ["第1回\t2000年10月5日\t02\t08\t10\t13\t27\t30\t39"])
    assert rows[0]["mainNumbers"] == [2, 8, 10, 13, 27, 30]
    assert rows[0]["bonusNumbers"] == [39]


def test_monthly_numbers_extracts_digits_not_prize_amount():
    rows = history.parse_snapshot(dict(game="numbers4", kind="month", text="""
    回別 第3回
    抽せん日 1994年10月21日
    抽せん数字 0097
    ストレート 12口 900,000円
    """))
    assert rows == [dict(game="numbers4", drawNumber=3, drawDate="1994-10-21", digits="0097")]


def test_monthly_bingo_keeps_eight_cells():
    rows = history.parse_snapshot(dict(game="bingo5", kind="month", text="""
    回別 第1回
    抽せん日 2017年4月5日
    抽せん数字 01 10 13 19 23 28 35 37
    1等 2口 8,578,800円
    """))
    assert rows[0]["mainNumbers"] == [1, 10, 13, 19, 23, 28, 35, 37]


def test_conflicting_source_rows_rejected():
    with pytest.raises(ValueError, match="Conflicting"):
        archive("numbers", ["第3回 1994年10月21日 194 0097", "第3回 1994年10月21日 195 0097"])


def test_bingo_positions_rejected_if_swapped():
    with pytest.raises(ValueError, match="positions"):
        archive("bingo5", ["第1回 2017年4月5日 10 01 13 19 23 28 35 37"])


def test_loto_bonus_cannot_overlap_main():
    with pytest.raises(ValueError):
        archive("loto6", ["第1回 2000年10月5日 02 08 10 13 27 30 30"])


def test_missing_draw_rejected():
    rows = archive("numbers", ["第1回 1994年10月7日 191 1149", "第3回 1994年10月21日 194 0097"])
    with pytest.raises(ValueError, match="Missing numbers3 draws"):
        history.check_coverage(rows, {"numbers3": "1994-10-07"})


def test_chronological_reversal_rejected():
    rows = archive("numbers", ["第1回 1994年10月7日 191 1149", "第2回 1994年10月21日 194 0097", "第3回 1994年10月14日 988 7921"])
    with pytest.raises(ValueError, match="Dates not increasing"):
        history.check_coverage(rows, {"numbers3": "1994-10-07"})


def test_shipped_database_integrity_and_first_draws():
    path = ROOT / "database/draw-history.sqlite3"
    if not path.exists():
        pytest.skip("History database has not been built yet")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        for game, first in history.STARTS.items():
            low, high, count = db.execute("SELECT MIN(draw_number),MAX(draw_number),COUNT(*) FROM draws WHERE game=?", (game,)).fetchone()
            assert low == 1 and high == count
            assert db.execute("SELECT draw_date FROM draws WHERE game=? AND draw_number=1", (game,)).fetchone()[0] == first
        assert db.execute("SELECT digits FROM draws WHERE game='numbers4' AND draw_number=3").fetchone()[0] == "0097"
        assert json.loads(db.execute("SELECT main_numbers FROM draws WHERE game='bingo5' AND draw_number=1").fetchone()[0]) == [1, 10, 13, 19, 23, 28, 35, 37]
