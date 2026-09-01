from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "public" / "draws.json"
BASE_URL = "https://www.mizuhobank.co.jp/takarakuji/check/loto/{path}/index.html?year={year}&month={month}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

GAME_CONFIG = {
    "miniloto": {"path": "miniloto", "main": 5, "bonus": 1, "max": 31},
    "loto6": {"path": "loto6", "main": 6, "bonus": 1, "max": 43},
    "loto7": {"path": "loto7", "main": 7, "bonus": 2, "max": 37},
}

DRAW_NUMBER_PATTERN = re.compile(r"第\s*(\d+)\s*回")
DATE_PATTERN = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
NUMBER_PATTERN = re.compile(r"\d{1,2}")


class ValidationError(ValueError):
    pass


def parse_month(text: str, game: str) -> list[dict]:
    config = GAME_CONFIG[game]
    parsed: list[dict] = []
    for block in text.split("回別")[1:]:
        if "本数字" not in block or "ボーナス数字" not in block:
            continue
        draw_match = DRAW_NUMBER_PATTERN.search(block)
        date_match = DATE_PATTERN.search(block)
        if not draw_match or not date_match:
            continue

        if game == "miniloto":
            # ミニロトは「本数字（ボーナス）」の一続きで表示される。
            number_segment = block.split("ボーナス数字", 1)[1]
            number_segment = re.split(r"当せん条件|[1１]等", number_segment, maxsplit=1)[0]
            values = [int(value) for value in NUMBER_PATTERN.findall(number_segment)]
            main = values[: config["main"]]
            bonus = values[config["main"] : config["main"] + config["bonus"]]
        else:
            main_segment = block.split("本数字", 1)[1].split("ボーナス数字", 1)[0]
            bonus_segment = block.split("ボーナス数字", 1)[1]
            bonus_segment = re.split(r"当せん条件|[1１]等", bonus_segment, maxsplit=1)[0]
            main = [int(value) for value in NUMBER_PATTERN.findall(main_segment)][: config["main"]]
            bonus = [int(value) for value in NUMBER_PATTERN.findall(bonus_segment)][: config["bonus"]]
        parsed.append(
            {
                "game": game,
                "drawNumber": int(draw_match.group(1)),
                "drawDate": (
                    f"{int(date_match.group(1)):04d}-"
                    f"{int(date_match.group(2)):02d}-"
                    f"{int(date_match.group(3)):02d}"
                ),
                "mainNumbers": sorted(main),
                "bonusNumbers": sorted(bonus),
            }
        )
    return parsed


def validate_draw(draw: dict) -> dict:
    game = draw.get("game")
    if game not in GAME_CONFIG:
        raise ValidationError(f"未対応のゲーム: {game}")
    config = GAME_CONFIG[game]
    draw_number = draw.get("drawNumber")
    draw_date = draw.get("drawDate")
    main = draw.get("mainNumbers")
    bonus = draw.get("bonusNumbers")

    if not isinstance(draw_number, int) or draw_number <= 0:
        raise ValidationError(f"{game}: 回号が不正です")
    try:
        datetime.strptime(draw_date, "%Y-%m-%d")
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{game} 第{draw_number}回: 日付が不正です") from error
    if not isinstance(main, list) or len(main) != config["main"] or len(set(main)) != len(main):
        raise ValidationError(f"{game} 第{draw_number}回: 本数字が不正です {main}")
    if not isinstance(bonus, list) or len(bonus) != config["bonus"] or len(set(bonus)) != len(bonus):
        raise ValidationError(f"{game} 第{draw_number}回: ボーナス数字が不正です {bonus}")
    if any(not isinstance(number, int) or not 1 <= number <= config["max"] for number in main + bonus):
        raise ValidationError(f"{game} 第{draw_number}回: 数字が範囲外です")
    if set(main) & set(bonus):
        raise ValidationError(f"{game} 第{draw_number}回: 本数字とボーナス数字が重複しています")

    normalized = dict(draw)
    normalized["mainNumbers"] = sorted(main)
    normalized["bonusNumbers"] = sorted(bonus)
    return normalized


def validate_feed(feed: dict) -> dict:
    if feed.get("schemaVersion") != 1 or not isinstance(feed.get("draws"), list):
        raise ValidationError("配信JSONの形式が不正です")
    by_key: dict[tuple[str, int], dict] = {}
    for raw in feed["draws"]:
        draw = validate_draw(raw)
        key = (draw["game"], draw["drawNumber"])
        if key in by_key and by_key[key] != draw:
            raise ValidationError(f"同一回号の内容が矛盾しています: {key}")
        by_key[key] = draw
    normalized = dict(feed)
    normalized["draws"] = sorted(
        by_key.values(), key=lambda item: (item["drawDate"], item["game"], item["drawNumber"])
    )
    return normalized


def month_targets(count: int, now: datetime | None = None) -> list[tuple[int, int]]:
    if count < 1:
        raise ValueError("months-backは1以上が必要です")
    current = now or datetime.now(JST)
    year, month = current.year, current.month
    targets = []
    for _ in range(count):
        targets.append((year, month))
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return targets


def fetch_month(page, game: str, year: int, month: int) -> list[dict]:
    url = BASE_URL.format(path=GAME_CONFIG[game]["path"], year=year, month=month)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("article", timeout=30_000)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass

    previous_count = -1
    deadline = time.time() + 25
    while time.time() < deadline:
        draws = parse_month(page.inner_text("article"), game)
        if draws and len(draws) == previous_count:
            return draws
        previous_count = len(draws)
        page.wait_for_timeout(800)
    return parse_month(page.inner_text("article"), game)


def fetch_games(games: list[str], months_back: int) -> list[dict]:
    collected: list[dict] = []
    targets = month_targets(months_back)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP", viewport={"width": 1280, "height": 1800})
            page = context.new_page()
            for game in games:
                game_draws: list[dict] = []
                for index, (year, month) in enumerate(targets):
                    draws = fetch_month(page, game, year, month)
                    print(f"[取得] {game} {year}-{month:02d}: {len(draws)}件")
                    game_draws.extend(draws)
                    if index + 1 < len(targets):
                        time.sleep(1)
                if not game_draws:
                    raise ValidationError(f"{game}: 抽せん結果を1件も取得できませんでした")
                collected.extend(validate_draw(draw) for draw in game_draws)
        finally:
            browser.close()
    return collected


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    by_key = {(draw["game"], draw["drawNumber"]): validate_draw(draw) for draw in existing}
    for draw in fresh:
        key = (draw["game"], draw["drawNumber"])
        if key in by_key and by_key[key] != draw:
            raise ValidationError(f"既存結果と再取得結果が一致しません: {key}")
        by_key[key] = draw
    return sorted(by_key.values(), key=lambda item: (item["drawDate"], item["game"], item["drawNumber"]))


def write_feed_atomically(feed: dict) -> None:
    temporary = FEED_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, FEED_PATH)


def set_action_output(changed: bool) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")


def parse_games(value: str) -> list[str]:
    if value == "all":
        return list(GAME_CONFIG)
    games = [part.strip() for part in value.split(",") if part.strip()]
    unknown = set(games) - set(GAME_CONFIG)
    if unknown:
        raise ValueError(f"未対応のゲーム: {', '.join(sorted(unknown))}")
    return games


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", default=os.environ.get("TARGET_GAMES", "all"))
    parser.add_argument("--months-back", type=int, default=int(os.environ.get("MONTHS_BACK", "2")))
    args = parser.parse_args()

    existing = validate_feed(json.loads(FEED_PATH.read_text(encoding="utf-8")))
    fresh = fetch_games(parse_games(args.games), args.months_back)
    merged = merge(existing["draws"], fresh)
    if merged == existing["draws"]:
        print("[変更なし] 公開JSONを維持します")
        set_action_output(False)
        return 0

    updated = {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "draws": merged,
    }
    validate_feed(updated)
    write_feed_atomically(updated)
    print(f"[更新] {len(merged)}回分を {FEED_PATH} へ保存しました")
    set_action_output(True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
