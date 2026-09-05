"""Read back every SQLite row and compare it with the acquired source data."""
import json
import sqlite3
import time

from build_history import OUT, collect, digest, validate


def verify():
    expected, sources, provenance, coverage, compared = collect()
    path = OUT / "draw-history.sqlite3"
    manifest = json.loads((OUT / "manifest.json").read_text())
    assert manifest["sha256"] == digest(path.read_bytes())
    assert manifest["bytes"] == path.stat().st_size
    assert manifest["coverage"] == coverage
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        actual = {}
        for game, number, date, main, bonus, digits in db.execute("SELECT * FROM draws"):
            draw = dict(game=game, drawNumber=number, drawDate=date)
            if digits is not None:
                assert main is None and bonus is None
                draw["digits"] = digits
            else:
                draw.update(mainNumbers=json.loads(main), bonusNumbers=json.loads(bonus))
            validate(draw)
            actual[(game, number)] = draw
        assert actual == expected
        assert db.execute("SELECT * FROM sources ORDER BY id").fetchall() == sources
        expected_refs = sorted((*key, sid) for key, ids in provenance.items() for sid in ids)
        assert db.execute("SELECT * FROM draw_sources ORDER BY game, draw_number, source_id").fetchall() == expected_refs
        # Known first draw: an exact six-number match must be discoverable offline.
        start = time.perf_counter()
        selection = {2, 8, 10, 13, 27, 30}
        hits = []
        for number, main, bonus in db.execute("SELECT draw_number,main_numbers,bonus_numbers FROM draws WHERE game='loto6'"):
            mains = set(json.loads(main))
            count = len(selection & mains)
            if count >= 5:
                rank = 1 if count == 6 else 2 if selection & set(json.loads(bonus)) else 3
                hits.append((number, rank))
        milliseconds = (time.perf_counter() - start) * 1000
        assert (1, 1) in hits
    report = dict(verifiedRows=len(actual), verifiedSourcePages=len(sources),
                  productionFeedMatches=compared, loto6FullScanMilliseconds=round(milliseconds, 3),
                  exampleSelection=sorted(selection), exampleMatches=hits)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    verify()
