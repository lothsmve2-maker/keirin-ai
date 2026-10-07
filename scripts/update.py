import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import unescape
from html.parser import HTMLParser


# ============================================================
# KEIRIN AI DATA UPDATE
# AI VERSION 1.3 FINAL
# ============================================================

JST = timezone(timedelta(hours=9))

BASE_URL = "https://www.oddspark.com/keirin/"

OUTPUT_FILE = Path("data/today.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Connection": "keep-alive",
}

# サイトへの負荷を抑える
REQUEST_SLEEP = 0.15

# 各競輪場の最大レース数
MAX_RACE_NO = 12


# ============================================================
# 競輪場コード
# ============================================================

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


# ============================================================
# HTTP取得
# ============================================================

def fetch(url, retries=2):

    for attempt in range(retries + 1):

        try:

            req = urllib.request.Request(
                url,
                headers=HEADERS
            )

            with urllib.request.urlopen(
                req,
                timeout=20
            ) as response:

                raw = response.read()

            text = raw.decode(
                "utf-8",
                errors="ignore"
            )

            if text:

                time.sleep(REQUEST_SLEEP)

                return text

        except Exception as e:

            if attempt >= retries:

                print(
                    f"    取得失敗: {url}"
                )
                print(
                    f"    {e}"
                )

            else:

                time.sleep(1)

    return ""


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):

    def __init__(self):

        super().__init__()

        self.tables = []

        self.in_table = False
        self.in_tr = False
        self.in_cell = False

        self.current_table = []
        self.current_row = []
        self.current_cell = []

    def handle_starttag(self, tag, attrs):

        tag = tag.lower()

        if tag == "table":

            self.in_table = True
            self.current_table = []

        elif tag == "tr" and self.in_table:

            self.in_tr = True
            self.current_row = []

        elif tag in ("td", "th") and self.in_tr:

            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):

        tag = tag.lower()

        if tag in ("td", "th") and self.in_cell:

            value = clean_text(
                "".join(self.current_cell)
            )

            self.current_row.append(value)

            self.current_cell = []
            self.in_cell = False

        elif tag == "tr" and self.in_tr:

            if self.current_row:

                self.current_table.append(
                    self.current_row
                )

            self.current_row = []
            self.in_tr = False

        elif tag == "table" and self.in_table:

            if self.current_table:

                self.tables.append(
                    self.current_table
                )

            self.current_table = []
            self.in_table = False

    def handle_data(self, data):

        if self.in_cell:

            self.current_cell.append(data)


# ============================================================
# TEXT CLEAN
# ============================================================

def clean_text(text):

    text = unescape(text or "")

    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# 今日の日付
# ============================================================

def today_jst():

    return datetime.now(
        JST
    ).strftime("%Y%m%d")


# ============================================================
# 開催場取得
# ============================================================

def get_today_venues(date_str):

    url = (
        f"{BASE_URL}"
        f"RaceListInfo.do"
        f"?kaisaiBi={date_str}"
    )

    print(
        f"開催場一覧取得: {url}"
    )

    html = fetch(url)

    if not html:

        return []

    venues = []

    for name, code in VENUE_CODES.items():

        if name in html:

            venues.append({
                "name": name,
                "code": code
            })

    return venues


# ============================================================
# 数値
# ============================================================

def to_float(text):

    if not text:
        return None

    m = re.search(
        r"(\d+(?:\.\d+)?)",
        text
    )

    if not m:
        return None

    try:
        return float(
            m.group(1)
        )
    except:
        return None


def to_int(text):

    if not text:
        return None

    m = re.search(
        r"(\d+)",
        text
    )

    if not m:
        return None

    try:
        return int(
            m.group(1)
        )
    except:
        return None


# ============================================================
# 競走得点
# ============================================================

def extract_race_point(text):

    patterns = [
        r"競走得点\s*[:：]\s*(\d+(?:\.\d+)?)",
        r"競走得点\s*(\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text
        )

        if m:

            try:
                return float(
                    m.group(1)
                )
            except:
                pass

    return None


# ============================================================
# 着順
# ============================================================

def extract_finish_stats(text):

    m = re.search(
        r"着\s*順\s*[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        text
    )

    if not m:

        return {
            "first": None,
            "second": None,
            "third": None,
            "other": None
        }

    return {
        "first": int(m.group(1)),
        "second": int(m.group(2)),
        "third": int(m.group(3)),
        "other": int(m.group(4))
    }


# ============================================================
# 決まり手
# ============================================================

def extract_decisive(text):

    m = re.search(
        r"決まり手\s*[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        text
    )

    if not m:

        return {
            "escape": None,
            "sweep": None,
            "difference": None,
            "mark": None
        }

    return {
        "escape": int(m.group(1)),
        "sweep": int(m.group(2)),
        "difference": int(m.group(3)),
        "mark": int(m.group(4))
    }


# ============================================================
# 脚質
# ============================================================

def extract_style(text):

    # 出走表では「逃」「追」「両」などが
    # 独立した項目として存在する

    if re.search(
        r"(?:脚質\s*)[:：]?\s*逃",
        text
    ):
        return "逃"

    if re.search(
        r"(?:脚質\s*)[:：]?\s*追",
        text
    ):
        return "追"

    if re.search(
        r"(?:脚質\s*)[:：]?\s*両",
        text
    ):
        return "両"

    # 表記が「3.92 逃」のようになっている場合
    if re.search(
        r"\d+\.\d+\s+逃",
        text
    ):
        return "逃"

    if re.search(
        r"\d+\.\d+\s+追",
        text
    ):
        return "追"

    if re.search(
        r"\d+\.\d+\s+両",
        text
    ):
        return "両"

    return None


# ============================================================
# 選手名
# ============================================================

def extract_rider_name(row):

    for cell in row:

        cell = clean_text(cell)

        if not cell:
            continue

        if re.fullmatch(
            r"\d+",
            cell
        ):
            continue

        if (
            "競走得点" in cell
            or "着順" in cell
            or "決まり手" in cell
        ):
            continue

        # 年齢／期別が入る選手情報
        m = re.match(
            r"(.+?)\s*\d+歳[／/]",
            cell
        )

        if m:

            name = clean_text(
                m.group(1)
            )

            if name:
                return name

    return None


# ============================================================
# 車番
# ============================================================

def extract_car_number(row):

    # 最初の1～3列から車番を探す

    for cell in row[:4]:

        cell = clean_text(cell)

        if re.fullmatch(
            r"[1-9]",
            cell
        ):

            return int(cell)

    return None


# ============================================================
# 選手行判定
# ============================================================

def is_rider_row(row):

    text = " ".join(row)

    if "誘導" in text:

        return False

    # 競走得点がある
    if "競走得点" in text:

        return True

    # 「○歳／○期」がある
    if re.search(
        r"\d+歳[／/]\d+期",
        text
    ):

        return True

    return False


# ============================================================
# 選手解析
# ============================================================

def parse_riders(html):

    parser = TableParser()

    parser.feed(html)

    riders = []

    for table in parser.tables:

        for row in table:

            if not is_rider_row(row):

                continue

            car_number = (
                extract_car_number(row)
            )

            if car_number is None:

                continue

            name = (
                extract_rider_name(row)
            )

            if not name:

                continue

            text = " ".join(row)

            point = (
                extract_race_point(text)
            )

            finish = (
                extract_finish_stats(text)
            )

            decisive = (
                extract_decisive(text)
            )

            style = (
                extract_style(text)
            )

            rider = {
                "car_number": car_number,
                "name": name,

                "race_point": point,

                "finish_stats": finish,

                "decisive": decisive,

                "style": style,

                "raw": row
            }

            # 車番＋名前で重複排除
            exists = False

            for old in riders:

                if (
                    old["car_number"]
                    == rider["car_number"]
                    and
                    old["name"]
                    == rider["name"]
                ):

                    exists = True
                    break

            if not exists:

                riders.append(
                    rider
                )

    riders.sort(
        key=lambda x:
        x["car_number"]
    )

    return riders


# ============================================================
# 直近成績抽出
# ============================================================

def extract_recent_results(html):

    # 出走表に含まれる
    # 「今場所」「前場所」「前々場所」
    # の着順を簡易的に抽出する。

    text = clean_text(html)

    results = []

    matches = re.findall(
        r"(\d+/\s*\d+)\s+"
        r"[^\d]{0,30}"
        r"([１-９\d]+)着",
        text
    )

    for date_text, result in matches:

        result = (
            result
            .replace("１", "1")
            .replace("２", "2")
            .replace("３", "3")
            .replace("４", "4")
            .replace("５", "5")
            .replace("６", "6")
            .replace("７", "7")
            .replace("８", "8")
            .replace("９", "9")
        )

        try:

            results.append({
                "date": clean_text(
                    date_text
                ),
                "finish": int(
                    result
                )
            })

        except:
            pass

    return results[:12]


# ============================================================
# AIスコア
# ============================================================

def calculate_ai_score(rider):

    point = rider.get(
        "race_point"
    )

    if point is None:

        return None

    score = 0.0

    # ----------------------------------------
    # 競走得点
    # ----------------------------------------

    score += point * 1.00

    # ----------------------------------------
    # 直近成績
    # ----------------------------------------

    finish = (
        rider.get(
            "finish_stats"
        )
        or {}
    )

    first = finish.get(
        "first"
    )

    second = finish.get(
        "second"
    )

    third = finish.get(
        "third"
    )

    other = finish.get(
        "other"
    )

    if first is not None:

        score += first * 0.30

    if second is not None:

        score += second * 0.12

    if third is not None:

        score += third * 0.06

    # ----------------------------------------
    # 決まり手
    # ----------------------------------------

    decisive = (
        rider.get(
            "decisive"
        )
        or {}
    )

    escape = decisive.get(
        "escape"
    )

    sweep = decisive.get(
        "sweep"
    )

    difference = decisive.get(
        "difference"
    )

    mark = decisive.get(
        "mark"
    )

    if escape is not None:

        score += escape * 0.10

    if sweep is not None:

        score += sweep * 0.10

    if difference is not None:

        score += difference * 0.06

    if mark is not None:

        score += mark * 0.03

    # ----------------------------------------
    # 脚質補正
    # ----------------------------------------

    style = rider.get(
        "style"
    )

    if style == "逃":

        score += 0.8

    elif style == "両":

        score += 0.5

    elif style == "追":

        score += 0.2

    return round(
        score,
        3
    )


# ============================================================
# AI評価
# ============================================================

def evaluate_race(riders):

    valid = []

    for rider in riders:

        score = (
            calculate_ai_score(
                rider
            )
        )

        rider["ai_score"] = score

        if score is not None:

            valid.append(
                rider
            )

        rider["ai_rank"] = None
        rider["ai_label"] = (
            "評価データ不足"
        )

    valid.sort(
        key=lambda x:
        x["ai_score"],
        reverse=True
    )

    for rank, rider in enumerate(
        valid,
        start=1
    ):

        rider["ai_rank"] = rank

    if len(valid) >= 1:

        valid[0][
            "ai_label"
        ] = "本命"

    if len(valid) >= 2:

        valid[1][
            "ai_label"
        ] = "対抗"

    if len(valid) >= 3:

        valid[2][
            "ai_label"
        ] = "単穴"

    # 穴は4～6位から選ぶ
    if len(valid) >= 4:

        candidates = valid[3:6]

        if candidates:

            hole = max(
                candidates,
                key=lambda x: (
                    x.get(
                        "decisive",
                        {}
                    ).get(
                        "sweep"
                    ) or 0
                )
            )

            hole[
                "ai_label"
            ] = "穴"

    return len(valid)


# ============================================================
# レースページ
# ============================================================

def get_race(
    venue_name,
    venue_code,
    date_str,
    race_no
):

    url = (
        f"{BASE_URL}"
        f"RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={date_str}"
        f"&raceNo={race_no}"
    )

    print(
        f"    {venue_name} "
        f"{race_no}R"
    )

    html = fetch(url)

    if not html:

        return None

    # 存在しないレースを除外
    if "出走表" not in html:

        return None

    riders = parse_riders(
        html
    )

    # 7選手前後が取れなければ
    # 無効ページとして扱う
    if len(riders) < 3:

        return None

    # 直近成績
    recent_results = (
        extract_recent_results(
            html
        )
    )

    ai_count = evaluate_race(
        riders
    )

    point_count = sum(
        1
        for rider in riders
        if rider.get(
            "race_point"
        ) is not None
    )

    return {
        "race_no": race_no,

        "url": url,

        "riders": riders,

        "recent_results": (
            recent_results
        ),

        "race_point_count": (
            point_count
        ),

        "ai_rider_count": (
            ai_count
        )
    }


# ============================================================
# 開催場
# ============================================================

def get_venue(
    venue,
    date_str
):

    venue_name = venue["name"]
    venue_code = venue["code"]

    print()
    print(
        f"=============================="
    )
    print(
        f"[{venue_name}] "
        f"コード={venue_code}"
    )
    print(
        f"=============================="
    )

    races = []

    for race_no in range(
        1,
        MAX_RACE_NO + 1
    ):

        race = get_race(
            venue_name,
            venue_code,
            date_str,
            race_no
        )

        if race is not None:

            races.append(
                race
            )

    rider_count = sum(
        len(r["riders"])
        for r in races
    )

    point_count = sum(
        r["race_point_count"]
        for r in races
    )

    ai_count = sum(
        r["ai_rider_count"]
        for r in races
    )

    print(
        f"  → {len(races)}レース"
    )

    print(
        f"  → {rider_count}選手"
    )

    print(
        f"  → 競走得点取得 "
        f"{point_count}人"
    )

    print(
        f"  → AI評価 "
        f"{ai_count}人"
    )

    return {
        "name": venue_name,
        "code": venue_code,
        "races": races
    }


# ============================================================
# 保存
# ============================================================

def save(data):

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


# ============================================================
# MAIN
# ============================================================

def main():

    date_str = today_jst()

    print()
    print(
        "=============================="
    )
    print(
        " KEIRIN AI DATA UPDATE"
    )
    print(
        "=============================="
    )

    print(
        f"対象日: {date_str}"
    )

    print(
        "=============================="
    )

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    venues = get_today_venues(
        date_str
    )

    print()

    print(
        f"開催場数: {len(venues)}"
    )

    if not venues:

        print(
            "本日の開催場を取得できませんでした"
        )

        return

    print(
        "開催場: "
        + ", ".join(
            v["name"]
            for v in venues
        )
    )

    # --------------------------------------------------------
    # データ取得
    # --------------------------------------------------------

    venue_data = []

    total_races = 0
    total_riders = 0
    total_points = 0
    total_ai = 0

    for venue in venues:

        data = get_venue(
            venue,
            date_str
        )

        venue_data.append(
            data
        )

        total_races += len(
            data["races"]
        )

        for race in data["races"]:

            total_riders += len(
                race["riders"]
            )

            total_points += (
                race[
                    "race_point_count"
                ]
            )

            total_ai += (
                race[
                    "ai_rider_count"
                ]
            )

    # --------------------------------------------------------
    # 保存データ
    # --------------------------------------------------------

    output = {

        "updated_at":
            datetime.now(
                JST
            ).isoformat(),

        "date":
            date_str,

        "ai_version":
            "1.3",

        "source":
            "OddsPark",

        "venues":
            venue_data,

        "summary": {

            "venue_count":
                len(venue_data),

            "race_count":
                total_races,

            "rider_count":
                total_riders,

            "race_point_count":
                total_points,

            "ai_rider_count":
                total_ai
        }
    }

    save(
        output
    )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    print()
    print(
        "=============================="
    )
    print(
        " UPDATE COMPLETE"
    )
    print(
        "=============================="
    )

    print(
        f"開催場: "
        f"{len(venue_data)}"
    )

    print(
        f"レース: "
        f"{total_races}"
    )

    print(
        f"選手: "
        f"{total_riders}"
    )

    print(
        f"競走得点取得: "
        f"{total_points}"
    )

    print(
        f"AI評価選手: "
        f"{total_ai}"
    )

    print(
        "AIバージョン: 1.3"
    )

    print(
        f"保存先: "
        f"{OUTPUT_FILE}"
    )

    print(
        "=============================="
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
