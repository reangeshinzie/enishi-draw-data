"""Build an offline SQLite archive from the official public result pages.

No changes to the live feed. Cached page snapshots permit offline rebuilds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
from update_draws import GAME_CONFIG, parse_month, validate_draw

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "history-cache"
OUT = ROOT / "database"
HOST = "https://www.mizuhobank.co.jp"
STARTS = {"miniloto": "1999-04-13", "loto6": "2000-10-05",
          "loto7": "2013-04-05", "numbers3": "1994-10-07",
          "numbers4": "1994-10-07", "bingo5": "2017-04-05"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def validate(draw):
    game = draw["game"]
    if type(draw["drawNumber"]) is not int or draw["drawNumber"] < 1:
        raise ValueError("Invalid draw number")
    d = date.fromisoformat(draw["drawDate"])
    if not date.fromisoformat(STARTS[game]) <= d <= date.today():
        raise ValueError(f"Invalid date: {draw}")
    if game in GAME_CONFIG:
        validate_draw(draw)
        if draw["mainNumbers"] != sorted(draw["mainNumbers"]):
            raise ValueError(f"Unsorted main numbers: {draw}")
    elif game.startswith("numbers"):
        if not re.fullmatch(r"[0-9]{" + game[-1] + "}", draw["digits"]):
            raise ValueError(f"Invalid digits: {draw}")
    elif game == "bingo5":
        nums = draw["mainNumbers"]
        if len(nums) != 8 or any(type(n) is not int or not i*5+1 <= n <= i*5+5
                                for i, n in enumerate(nums)):
            raise ValueError(f"Invalid bingo positions: {draw}")
    else:
        raise ValueError(game)
    return draw


def parse_snapshot(snapshot):
    game = snapshot["game"]
    text = snapshot["text"]
    draws = []
    if snapshot["kind"] == "archive":
        for row in snapshot["rows"]:
            match = re.fullmatch(r"\s*第\s*(\d+)\s*回\s+(\d{4})年\s*(\d+)月\s*(\d+)日\s+([\d\s]+)", row)
            if not match:
                continue
            number, y, m, d, values = match.groups()
            base = {"drawNumber": int(number), "drawDate": f"{int(y):04}-{int(m):02}-{int(d):02}"}
            values = values.split()
            if game == "numbers":
                if len(values) != 2:
                    raise ValueError(row)
                draws.extend(dict(base, game=g, digits=v) for g, v in zip(["numbers3", "numbers4"], values))
            else:
                main_count = GAME_CONFIG[game]["main"] if game in GAME_CONFIG else 8
                bonus_count = GAME_CONFIG[game]["bonus"] if game in GAME_CONFIG else 0
                if len(values) != main_count + bonus_count:
                    raise ValueError(row)
                draws.append(dict(base, game=game, mainNumbers=list(map(int, values[:main_count])),
                                  bonusNumbers=list(map(int, values[main_count:]))))
    elif game in GAME_CONFIG:
        draws = parse_month(text, game)
    else:
        for block in text.split("回別")[1:]:
            n = re.search(r"第\s*(\d+)\s*回", block)
            d = re.search(r"(\d{4})年\s*(\d+)月\s*(\d+)日", block)
            if not n or not d or "抽せん数字" not in block:
                continue
            base = {"game": game, "drawNumber": int(n[1]),
                    "drawDate": f"{int(d[1]):04}-{int(d[2]):02}-{int(d[3]):02}"}
            segment = block.split("抽せん数字", 1)[1]
            if game.startswith("numbers"):
                digits = re.match(r"\s*([0-9]+)", segment)
                if not digits:
                    raise ValueError(block)
                draws.append(dict(base, digits=digits[1]))
            else:
                values = re.findall(r"\d+", re.split(r"[1１]等|当せん条件", segment)[0])
                draws.append(dict(base, mainNumbers=list(map(int, values)), bonusNumbers=[]))
    unique = {}
    for draw in draws:
        validate(draw)
        key = (draw["game"], draw["drawNumber"])
        if key in unique and unique[key] != draw:
            raise ValueError(f"Conflicting duplicate: {key}")
        unique[key] = draw
    return list(unique.values())


def discover(page):
    targets = {}
    for group in ["loto", "numbers", "bingo"]:
        url = f"{HOST}/takarakuji/check/{group}/backnumber/index.html"
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("Array.from(document.querySelectorAll('article a')).some(e=>/第1回/.test(e.textContent))")
        links = page.locator("article a").evaluate_all("es=>es.map(e=>({url:e.href,label:e.textContent.trim(),column:e.closest('td')?.cellIndex}))")
        for link in links:
            url, label = link["url"], link["label"]
            parsed = urlparse(url)
            if parsed.hostname != "www.mizuhobank.co.jp":
                continue
            q = parse_qs(parsed.query)
            if re.match(r"第\d+回", label):
                if group == "loto":
                    game = q.get("type", [None])[0]
                    if game is None:
                        game = "loto6" if "/loto6" in parsed.path else "miniloto"
                else:
                    game = "numbers" if group == "numbers" else "bingo5"
                bounds = list(map(int, re.findall(r"第(\d+)回", label)))
                targets[url] = dict(url=url, game=game, kind="archive", first=bounds[0], last=bounds[-1])
            elif "year" in q and "month" in q:
                game = parsed.path.split("/")[-2]
                if game in STARTS:
                    targets[url] = dict(url=url, game=game, kind="month")
    today = date.today()
    for game in STARTS:
        group = "loto" if game in GAME_CONFIG else "numbers" if game.startswith("numbers") else "bingo"
        url = f"{HOST}/takarakuji/check/{group}/{game}/index.html?year={today.year}&month={today.month}"
        targets[url] = dict(url=url, game=game, kind="month")
    return list(targets.values())


def fetch():
    CACHE.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(locale="ja-JP")
        try:
            targets = discover(page)
            save_json(CACHE / "targets.json", targets)
            print(f"Discovered {len(targets)} result pages", flush=True)
            for i, target in enumerate(targets):
                path = CACHE / (digest(target["url"].encode()) + ".json")
                if path.exists():
                    continue
                response = page.goto(target["url"], wait_until="domcontentloaded", timeout=60000)
                if not response or response.status != 200:
                    raise ValueError(f"HTTP failure: {target['url']}")
                end = time.monotonic() + 35
                previous = None
                while time.monotonic() < end:
                    page.wait_for_timeout(500)
                    snapshot = dict(target, text=page.locator("article").inner_text(),
                                    rows=page.locator("article tr").all_inner_texts())
                    draws = parse_snapshot(snapshot)
                    if target["kind"] == "archive":
                        numbers = {d["drawNumber"] for d in draws}
                        ready = numbers == set(range(target["first"], target["last"]+1))
                    else:
                        # The month heading declares the expected range.
                        heading = re.search(r"月分\s*[（(]第(\d+)回[〜～~－\-]第(\d+)回", snapshot["text"])
                        ready = bool(heading) and {d["drawNumber"] for d in draws} == set(range(int(heading[1]), int(heading[2])+1))
                        if not heading:
                            single = re.search(r"月分\s*[（(]第(\d+)回[）)]", snapshot["text"])
                            ready = bool(single) and {d["drawNumber"] for d in draws} == {int(single[1])}
                    if ready and draws == previous:
                        snapshot["retrievedAt"] = datetime.now(timezone.utc).isoformat()
                        save_json(path, snapshot)
                        break
                    previous = draws
                else:
                    raise ValueError(f"Incomplete page: {target}, got {len(draws)}")
                if i % 10 == 0 or i+1 == len(targets):
                    print(f"Fetched {i+1}/{len(targets)} ({target['game']})", flush=True)
        finally:
            browser.close()


def collect():
    targets = json.loads((CACHE / "targets.json").read_text())
    records, sources, provenance = {}, [], {}
    for target in targets:
        path = CACHE / (digest(target["url"].encode()) + ".json")
        snapshot = json.loads(path.read_text())
        sid = len(sources) + 1
        sources.append((sid, target["url"], snapshot["retrievedAt"], digest(path.read_bytes())))
        for draw in parse_snapshot(snapshot):
            key = (draw["game"], draw["drawNumber"])
            if key in records and records[key] != draw:
                raise ValueError(f"Sources disagree: {key}")
            records[key] = draw
            provenance.setdefault(key, []).append(sid)
    coverage = check_coverage(records.values())
    # Existing production results are independent DOM-parser evidence.
    compared = 0
    for old in json.loads((ROOT / "public/draws.json").read_text())["draws"]:
        key = (old["game"], old["drawNumber"])
        if key not in records or any(records[key].get(k) != old[k] for k in ["drawDate", "mainNumbers", "bonusNumbers"]):
            raise ValueError(f"Production feed mismatch: {key}")
        compared += 1
    return records, sources, provenance, coverage, compared


def check_coverage(records, starts=None):
    starts = STARTS if starts is None else starts
    records = list(records)
    coverage = {}
    for game, start in starts.items():
        rows = sorted((d for d in records if d["game"] == game), key=lambda d: d["drawNumber"])
        if not rows or rows[0]["drawDate"] != start:
            raise ValueError(f"Missing first draw: {game}")
        missing = sorted(set(range(1, rows[-1]["drawNumber"]+1)) - {d["drawNumber"] for d in rows})
        if missing:
            raise ValueError(f"Missing {game} draws: {missing}")
        if any(a["drawDate"] >= b["drawDate"] for a, b in zip(rows, rows[1:])):
            raise ValueError(f"Dates not increasing: {game}")
        coverage[game] = dict(count=len(rows), firstDate=start, lastDate=rows[-1]["drawDate"], lastDraw=rows[-1]["drawNumber"], missing=0)
    return coverage


def build():
    records, sources, provenance, coverage, compared = collect()
    OUT.mkdir(exist_ok=True)
    final = OUT / "draw-history.sqlite3"
    temp = OUT / "draw-history.sqlite3.tmp"
    if temp.exists():
        raise ValueError(f"Unfinished build exists: {temp}")
    with sqlite3.connect(temp) as db:
        db.executescript('''
          PRAGMA user_version=1;
          PRAGMA foreign_keys=ON;
          CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE draws(game TEXT NOT NULL, draw_number INTEGER NOT NULL CHECK(draw_number>0),
            draw_date TEXT NOT NULL, main_numbers TEXT, bonus_numbers TEXT, digits TEXT,
            PRIMARY KEY(game,draw_number), UNIQUE(game,draw_date));
          CREATE INDEX draws_date ON draws(draw_date);
          CREATE TABLE sources(id INTEGER PRIMARY KEY, url TEXT NOT NULL, retrieved_at TEXT NOT NULL, sha256 TEXT NOT NULL);
          CREATE TABLE draw_sources(game TEXT, draw_number INTEGER, source_id INTEGER REFERENCES sources(id),
            PRIMARY KEY(game,draw_number,source_id), FOREIGN KEY(game,draw_number) REFERENCES draws(game,draw_number));
        ''')
        db.executemany("INSERT INTO sources VALUES(?,?,?,?)", sources)
        for key, d in sorted(records.items()):
            db.execute("INSERT INTO draws VALUES(?,?,?,?,?,?)", (*key, d["drawDate"],
                       json.dumps(d["mainNumbers"], separators=(",", ":")) if "mainNumbers" in d else None,
                       json.dumps(d["bonusNumbers"], separators=(",", ":")) if "bonusNumbers" in d else None, d.get("digits")))
            db.executemany("INSERT INTO draw_sources VALUES(?,?,?)", [(*key, sid) for sid in provenance[key]])
        db.execute("INSERT INTO metadata VALUES(?,?)", ("coverage", json.dumps(coverage, sort_keys=True)))
        db.execute("INSERT INTO metadata VALUES(?,?)", ("purpose", "Historical comparison; no prediction or proof of purchase"))
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database integrity check failed")
    temp.replace(final)
    report = dict(schemaVersion=1, coverage=coverage, totalDraws=len(records), sourcePages=len(sources),
                  productionFeedMatches=compared, bytes=final.stat().st_size, sha256=digest(final.read_bytes()))
    save_json(OUT / "manifest.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch()
    build()
