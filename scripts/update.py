import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUTPUT_FILE = Path("data/today.json")

SALE_PLACE_URL = (
    "https://sp.oddspark.com/keirin/"
    "SpSalePlaceList.do"
    "?gamenId=S101"
    "&gamenKoumokuId=sideMenuKeirinSale"
)

VENUE_CODES = {
    "函館": "11", "青森": "12", "いわき平": "13",
    "弥彦": "21", "前橋": "22", "取手": "23",
    "宇都宮": "24", "大宮": "25", "西武園": "26",
    "京王閣": "27", "立川": "28", "松戸": "31",
    "川崎": "34", "平塚": "35", "小田原": "36",
    "伊東": "37", "静岡": "38", "名古屋": "42",
    "岐阜": "43", "大垣": "44", "豊橋": "45",
    "富山": "46", "松阪": "47", "四日市": "48",
    "福井": "51", "奈良": "53", "向日町": "54",
    "和歌山": "55", "岸和田": "56", "玉野": "61",
    "広島": "62", "防府": "63", "高松": "71",
    "小松島": "73", "高知": "74", "松山": "75",
    "小倉": "81", "久留米": "83", "武雄": "84",
    "佐世保": "85", "別府": "86", "熊本": "87"
}


def fetch(url, timeout=10):
    print("取得:", url)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "ja-JP,ja;q=0.9"
        }
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def html_to_text(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def find_today_venues(text):
    """
    開催場ページから開催情報を探す。
    ページ全体に存在するナビゲーションだけでは
    全競輪場を拾わないよう、日付付近を優先して調べる。
    """

    found = []

    for name, code in VENUE_CODES.items():
        if name not in text:
            continue

        # 競輪場名の周辺だけ確認
        positions = [m.start() for m in re.finditer(re.escape(name), text)]

        for pos in positions:
            block = text[max(0, pos - 250):pos + 250]

            # 発売・開催・レースなどの文脈がある場合を優先
            if any(word in block for word in [
                "開催", "発売", "レース", "R", "初日", "最終日", "2日目", "3日目"
            ]):
                found.append({
                    "name": name,
                    "code": code
                })
                break

    unique = []
    used = set()

    for venue in found:
        if venue["name"] not in used:
            used.add(venue["name"])
            unique.append(venue)

    return unique


def find_race_numbers(text):
    numbers = set()

    for match in re.finditer(r"([1-9]|1[0-2])R", text):
        number = int(match.group(1))
        if 1 <= number <= 12:
            numbers.add(number)

    return sorted(numbers)


def parse_entries(text):
    entries = []

    # 競輪選手名の基本的な抽出
    pattern = re.compile(
        r"([1-9])\s+"
        r"([一-龯ぁ-んァ-ヶー]{2,8})"
    )

    for match in pattern.finditer(text):
        number = int(match.group(1))
        name = match.group(2)

        if any(e["number"] == number for e in entries):
            continue

        entries.append({
            "number": number,
            "name": name,
            "age": None,
            "period": None,
            "prefecture": "",
            "class": "",
            "score": None,
            "gear": None,
            "style": "",
            "comment": "",
            "recent_results": [],
            "last_race": {},
            "second_last_race": {},
            "line": ""
        })

    entries.sort(key=lambda x: x["number"])

    return entries[:9]


def fetch_race(venue, date_string, race_no):
    code = venue["code"]

    url = (
        "https://sp.oddspark.com/keirin/"
        "SpRaceInfo.do"
        f"?joCd={code}"
        f"&joCode={code}"
        f"&kaisaiBi={date_string}"
        f"&raceNo={race_no}"
    )

    try:
        html = fetch(url, timeout=8)
    except Exception as e:
        print("取得失敗:", venue["name"], race_no, e)
        return None

    text = html_to_text(html)

    entries = parse_entries(text)

    time_match = re.search(r"(\d{1,2}:\d{2})\s*発走", text)

    start_time = ""
    if time_match:
        start_time = time_match.group(1)

    return {
        "number": race_no,
        "start_time": start_time,
        "title": "",
        "entries": entries,
        "entry_count": len(entries),
        "source": url
    }


def fetch_venue(venue, date_string):
    print("")
    print("==============================")
    print("開催場:", venue["name"])
    print("==============================")

    # まずプログラムページを確認
    program_url = (
        "https://www.oddspark.com/keirin/"
        "RaceProgram.do"
        f"?joCode={venue['code']}"
        f"&shonichi={date_string}"
    )

    race_numbers = []

    try:
        html = fetch(program_url, timeout=8)
        text = html_to_text(html)
        race_numbers = find_race_numbers(text)
    except Exception as e:
        print("プログラム取得失敗:", e)

    # プログラムから取得できない場合は1〜12Rを対象
    if not race_numbers:
        race_numbers = list(range(1, 13))

    races = []

    for race_no in race_numbers:
        race = fetch_race(
            venue,
            date_string,
            race_no
        )

        if race:
            races.append(race)

    return {
        "name": venue["name"],
        "code": venue["code"],
        "races": races
    }


def save_json(data):
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("保存:", OUTPUT_FILE)


def main():

    now = datetime.now(JST)

    date_string = now.strftime("%Y%m%d")
    date_display = now.strftime("%Y-%m-%d")

    print("")
    print("================================")
    print(" KEIRIN AI AUTO UPDATE")
    print("================================")
    print("対象日:", date_display)
    print("================================")

    # 今日の開催場を取得
    try:
        html = fetch(
            SALE_PLACE_URL,
            timeout=10
        )
    except Exception as e:
        print("開催場ページ取得失敗:", e)
        return

    text = html_to_text(html)

    venues = find_today_venues(text)

    print("")
    print("今日の開催場:")

    for venue in venues:
        print("-", venue["name"])

    if not venues:
        print("開催場を取得できませんでした。")
        return

    venue_data = []

    for venue in venues:

        try:
            data = fetch_venue(
                venue,
                date_string
            )

            venue_data.append(data)

        except Exception as e:
            print(
                "開催場処理失敗:",
                venue["name"],
                e
            )

    total_races = 0
    total_entries = 0

    for venue in venue_data:

        total_races += len(
            venue["races"]
        )

        for race in venue["races"]:

            total_entries += race[
                "entry_count"
            ]

    result = {
        "updated_at": now.isoformat(),
        "source": "OddsPark",
        "date": date_display,
        "venue_count": len(venue_data),
        "race_count": total_races,
        "entry_count": total_entries,
        "status": "ok",
        "venues": venue_data
    }

    save_json(result)

    print("")
    print("================================")
    print(" 更新完了")
    print("開催場:", len(venue_data))
    print("レース:", total_races)
    print("選手:", total_entries)
    print("================================")


if __name__ == "__main__":
    main()
