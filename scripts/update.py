import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path


# ============================================
# KEIRIN AI
# 無料・公開ページから実開催データを取得
# ============================================

JST = timezone(timedelta(hours=9))

OUTPUT_FILE = Path("data/today.json")

SALE_PLACE_URL = (
    "https://sp.oddspark.com/keirin/"
    "SpSalePlaceList.do"
    "?gamenId=S101"
    "&gamenKoumokuId=sideMenuKeirinSale"
)


# ============================================
# 競輪場コード
# ============================================

VENUE_CODES = {
    "函館": "11",
    "青森": "12",
    "いわき平": "13",
    "弥彦": "21",
    "前橋": "22",
    "取手": "23",
    "宇都宮": "24",
    "大宮": "25",
    "西武園": "26",
    "京王閣": "27",
    "立川": "28",
    "松戸": "31",
    "川崎": "34",
    "平塚": "35",
    "小田原": "36",
    "伊東": "37",
    "静岡": "38",
    "名古屋": "42",
    "岐阜": "43",
    "大垣": "44",
    "豊橋": "45",
    "富山": "46",
    "松阪": "47",
    "四日市": "48",
    "福井": "51",
    "奈良": "53",
    "向日町": "54",
    "和歌山": "55",
    "岸和田": "56",
    "玉野": "61",
    "広島": "62",
    "防府": "63",
    "高松": "71",
    "小松島": "73",
    "高知": "74",
    "松山": "75",
    "小倉": "81",
    "久留米": "83",
    "武雄": "84",
    "佐世保": "85",
    "別府": "86",
    "熊本": "87",
}


# ============================================
# HTMLテキスト抽出
# ============================================

class TextParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data).strip()

        if text:
            self.parts.append(text)

    def get_text(self):
        return "\n".join(self.parts)


def html_to_text(html):
    parser = TextParser()
    parser.feed(html)
    return parser.get_text()


# ============================================
# Web取得
# ============================================

def fetch(url):

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

    with urllib.request.urlopen(request, timeout=30) as response:

        charset = response.headers.get_content_charset()

        if not charset:
            charset = "utf-8"

        return response.read().decode(
            charset,
            errors="ignore"
        )


# ============================================
# 今日の開催場を取得
# ============================================

def find_today_venues(text):

    venues = []

    # OddsParkの
    # 「本日(10/6)の発売場（競輪）」
    # より後ろを中心に判定する

    marker = "本日"

    if marker in text:

        text = text[text.find(marker):]

    for name, code in VENUE_CODES.items():

        if name in text:

            if name not in venues:

                venues.append({
                    "name": name,
                    "code": code
                })

    return venues


# ============================================
# レース番号・発走時刻を取得
# ============================================

def find_races(text):

    races = []

    # 例
    # 1R
    # 20:50
    #
    # のような情報を拾う

    pattern = re.compile(
        r"(\d{1,2})R.*?"
        r"(\d{1,2}:\d{2})",
        re.S
    )

    for match in pattern.finditer(text):

        race_no = int(match.group(1))
        start_time = match.group(2)

        if 1 <= race_no <= 12:

            item = {
                "number": race_no,
                "start_time": start_time
            }

            if item not in races:
                races.append(item)

    races.sort(
        key=lambda x: x["number"]
    )

    return races


# ============================================
# 選手情報を抽出
# ============================================

def parse_entries(text):

    entries = []

    # OddsPark出走表では
    #
    # 車番
    # 選手名
    # 年齢/期別/級班
    #
    # がまとまって表示される。

    pattern = re.compile(
        r"(?P<number>[1-9])\s+"
        r"(?P<name>[^\n]+?)"
        r"\((?P<pref>[^)]+)\)\s*"
        r"\n?"
        r"(?P<age>\d+)歳/"
        r"(?P<period>\d+)期/"
        r"(?P<class>[A-ZＬＳＡ]\d+級?\d*班)"
    )

    for match in pattern.finditer(text):

        number = int(
            match.group("number")
        )

        name = match.group("name").strip()

        pref = match.group("pref").strip()

        age = match.group("age")

        period = match.group("period")

        class_name = match.group("class")

        # 周辺テキストから追加情報を探す

        start = match.end()

        block = text[
            start:start + 700
        ]

        score = None
        gear = None
        style = None

        score_match = re.search(
            r"(\d{2}\.\d{2,3})",
            block
        )

        if score_match:
            score = score_match.group(1)

        gear_match = re.search(
            r"(\d\.\d{2})",
            block
        )

        if gear_match:
            gear = gear_match.group(1)

        style_match = re.search(
            r"(逃|捲|差|両)",
            block
        )

        if style_match:
            style = style_match.group(1)

        entry = {
            "number": number,
            "name": name,
            "age": int(age),
            "period": int(period),
            "prefecture": pref,
            "class": class_name,
            "score": score,
            "gear": gear,
            "style": style,
            "comment": "",
            "recent_results": [],
            "last_race": {},
            "second_last_race": {},
            "line": ""
        }

        # 重複防止

        if not any(
            e["number"] == number
            for e in entries
        ):
            entries.append(entry)

    entries.sort(
        key=lambda x: x["number"]
    )

    return entries


# ============================================
# コメント抽出
# ============================================

def parse_comments(text, entries):

    # コメント欄の周辺を探す

    marker = "コメント"

    if marker not in text:
        return entries

    comment_text = text[
        text.find(marker):
    ]

    for entry in entries:

        name = entry["name"]

        # 苗字・名前の一部だけでも探す
        short_name = name.split("　")[0]

        pos = comment_text.find(
            short_name
        )

        if pos < 0:
            continue

        block = comment_text[
            pos:pos + 150
        ]

        # 「自力。」「前々。」などを拾う
        parts = block.split("\n")

        if len(parts) >= 2:

            comment = parts[-1].strip()

            if comment and len(comment) < 80:

                entry["comment"] = comment

    return entries


# ============================================
# 1レース取得
# ============================================

def fetch_race(
    venue_name,
    venue_code,
    date_string,
    race_no
):

    url = (
        "https://sp.oddspark.com/keirin/"
        "SpRaceInfo.do"
        f"?joCd={venue_code}"
        f"&joCode={venue_code}"
        f"&kaisaiBi={date_string}"
        f"&raceNo={race_no}"
    )

    try:

        html = fetch(url)

    except Exception as e:

        print(
            "レース取得失敗:",
            venue_name,
            race_no,
            e
        )

        return None

    text = html_to_text(html)

    entries = parse_entries(text)

    entries = parse_comments(
        text,
        entries
    )

    # 7車・9車など、実際に取得できた人数を保存

    race = {
        "number": race_no,
        "entries": entries,
        "entry_count": len(entries),
        "source": url
    }

    # 発走時刻

    time_match = re.search(
        r"(\d{1,2}:\d{2})\s+発走",
        text
    )

    if time_match:

        race["start_time"] = (
            time_match.group(1)
        )
    else:

        race["start_time"] = ""

    # レース名

    race_title = ""

    title_patterns = [
        r"\d{4}年\d{1,2}月\d{1,2}日.*?\n(.*?)\n",
        r"\d{1,2}R\s+(.*?)\n"
    ]

    for pattern in title_patterns:

        m = re.search(
            pattern,
            text
        )

        if m:

            race_title = m.group(1).strip()

            if len(race_title) < 100:
                break

    race["title"] = race_title

    return race


# ============================================
# 開催場の全レース取得
# ============================================

def fetch_venue(
    venue,
    date_string
):

    name = venue["name"]
    code = venue["code"]

    print("")
    print(
        "=============================="
    )
    print(
        "開催場:",
        name
    )
    print(
        "=============================="
    )

    # まずレースプログラムを取得

    program_url = (
        "https://www.oddspark.com/keirin/"
        "RaceProgram.do"
        f"?joCode={code}"
        f"&shonichi={date_string}"
    )

    races = []

    try:

        html = fetch(program_url)

        text = html_to_text(html)

        races = find_races(text)

    except Exception as e:

        print(
            "プログラム取得失敗:",
            e
        )

    # プログラムから取得できなかった場合
    # 1～12Rを順番に試す

    if not races:

        races = [
            {
                "number": i,
                "start_time": ""
            }
            for i in range(1, 13)
        ]

    result_races = []

    for race_info in races:

        race_no = race_info["number"]

        race = fetch_race(
            name,
            code,
            date_string,
            race_no
        )

        if race:

            if not race.get(
                "start_time"
            ):

                race["start_time"] = (
                    race_info.get(
                        "start_time",
                        ""
                    )
                )

            result_races.append(race)

    return {
        "name": name,
        "code": code,
        "races": result_races
    }


# ============================================
# JSON保存
# ============================================

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

    print("")
    print(
        "保存完了:",
        OUTPUT_FILE
    )


# ============================================
# メイン
# ============================================

def main():

    now = datetime.now(JST)

    date_string = now.strftime(
        "%Y%m%d"
    )

    date_display = now.strftime(
        "%Y-%m-%d"
    )

    print("")
    print(
        "================================"
    )
    print(
        " KEIRIN AI REAL DATA UPDATE"
    )
    print(
        "================================"
    )
    print(
        "日時:",
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    # ----------------------------------------
    # 今日の開催場取得
    # ----------------------------------------

    try:

        html = fetch(
            SALE_PLACE_URL
        )

    except Exception as e:

        print(
            "開催場取得失敗:",
            e
        )

        return

    text = html_to_text(html)

    venues = find_today_venues(
        text
    )

    print("")
    print(
        "今日の開催場:"
    )

    for venue in venues:

        print(
            "-",
            venue["name"]
        )

    # ----------------------------------------
    # 開催場が取れなかった場合
    # ----------------------------------------

    if not venues:

        print(
            "今日の開催場を取得できませんでした。"
        )

        return

    # ----------------------------------------
    # 各開催場を取得
    # ----------------------------------------

    venue_data = []

    for venue in venues:

        try:

            data = fetch_venue(
                venue,
                date_string
            )

            venue_data.append(
                data
            )

        except Exception as e:

            print(
                "開催場処理失敗:",
                venue["name"],
                e
            )

    # ----------------------------------------
    # 集計
    # ----------------------------------------

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

    # ----------------------------------------
    # JSON
    # ----------------------------------------

    data = {

        "updated_at":
            now.isoformat(),

        "source":
            "OddsPark",

        "date":
            date_display,

        "venue_count":
            len(venue_data),

        "race_count":
            total_races,

        "entry_count":
            total_entries,

        "status":
            "ok",

        "venues":
            venue_data

    }

    save_json(data)

    print("")
    print(
        "================================"
    )
    print(
        " 更新完了"
    )
    print(
        "開催場:",
        len(venue_data)
    )
    print(
        "レース:",
        total_races
    )
    print(
        "選手:",
        total_entries
    )
    print(
        "================================"
    )


if __name__ == "__main__":
    main()
