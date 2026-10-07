import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser


# ============================================================
# KEIRIN AI DATA UPDATE
# Version 2.0
#
# - 安定版の開催場取得を維持
# - RaceList.do から詳細データ取得
# - 並列取得で処理時間を短縮
# - 競走得点 / 着順 / 決まり手 / 脚質 / 今場所 / 前場所
# - 実データベースのAIスコア
# ============================================================


JST = timezone(timedelta(hours=9))

BASE_URL = "https://www.oddspark.com/keirin/"
OUTPUT_FILE = Path("data/today.json")

MAX_WORKERS = 8
TIMEOUT = 15
RETRIES = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}


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
# HTML parser
# ============================================================

class SimpleHTMLParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.text_parts = []

        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.in_th = False

        self.current_row = []
        self.current_cell = []

        self.rows = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "table":
            self.in_table = True

        elif tag == "tr" and self.in_table:
            self.in_tr = True
            self.current_row = []

        elif tag in ("td", "th") and self.in_tr:
            self.in_td = True
            self.in_th = tag == "th"
            self.current_cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("td", "th") and self.in_td:
            value = clean_text(" ".join(self.current_cell))
            self.current_row.append(value)

            self.in_td = False
            self.in_th = False
            self.current_cell = []

        elif tag == "tr" and self.in_tr:
            if self.current_row:
                self.rows.append(self.current_row)

            self.in_tr = False
            self.current_row = []

        elif tag == "table":
            self.in_table = False

    def handle_data(self, data):
        value = clean_text(data)

        if not value:
            return

        self.text_parts.append(value)

        if self.in_td:
            self.current_cell.append(value)

    def get_text(self):
        return "\n".join(self.text_parts)

    def get_rows(self):
        return self.rows


# ============================================================
# 共通
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = unescape(str(value))
    value = value.replace("\xa0", " ")
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def fetch(url, retries=RETRIES):
    last_error = None

    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=HEADERS)

            with urlopen(req, timeout=TIMEOUT) as response:
                data = response.read()

            return data.decode("utf-8", errors="ignore")

        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last_error = e

            if attempt < retries:
                time.sleep(0.5)

    print(f"  [取得失敗] {url}")
    print(f"  [理由] {last_error}")

    return ""


def parse_html(html):
    parser = SimpleHTMLParser()
    parser.feed(html)

    return parser


# ============================================================
# 開催場取得
# ============================================================

def find_today_venues(html):
    venues = []

    text = clean_text(html)

    for venue_name, code in VENUE_CODES.items():
        if venue_name in text:
            venues.append({
                "name": venue_name,
                "code": code,
            })

    # 重複削除
    unique = {}

    for venue in venues:
        unique[venue["code"]] = venue

    return list(unique.values())


# ============================================================
# レースURL取得
# ============================================================

def get_all_race_url(venue_code, date_str):
    return (
        f"{BASE_URL}AllRaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={date_str}"
    )


def get_race_url(venue_code, date_str, race_no):
    return (
        f"{BASE_URL}RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={date_str}"
        f"&raceNo={race_no}"
    )


# ============================================================
# AllRaceListから存在するRを取得
# ============================================================

def find_race_numbers(html):
    race_numbers = set()

    # RaceList.do のリンクを探す
    patterns = [
        r"raceNo=(\d+)",
        r"第(\d+)R",
        r"(\d+)R",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, html):
            try:
                number = int(match.group(1))

                if 1 <= number <= 12:
                    race_numbers.add(number)

            except Exception:
                pass

    # ページ構造から取れなかった場合
    # 1～12Rを候補にする
    if not race_numbers:
        return list(range(1, 13))

    return sorted(race_numbers)


# ============================================================
# 数値
# ============================================================

def to_float(value):
    if value is None:
        return None

    m = re.search(r"(\d+(?:\.\d+)?)", str(value))

    if not m:
        return None

    try:
        return float(m.group(1))
    except Exception:
        return None


def to_int(value):
    if value is None:
        return None

    m = re.search(r"(\d+)", str(value))

    if not m:
        return None

    try:
        return int(m.group(1))
    except Exception:
        return None


# ============================================================
# 着順・決まり手
# ============================================================

def parse_four_numbers(value):
    if not value:
        return [0, 0, 0, 0]

    nums = re.findall(r"\d+", value)

    result = []

    for n in nums[:4]:
        try:
            result.append(int(n))
        except Exception:
            result.append(0)

    while len(result) < 4:
        result.append(0)

    return result


def parse_recent_results(text):
    """
    ページ内に存在する直近成績を簡易解析。
    例:
    10/6 ガ予１ ２着
    9/28 ガ予１ １着
    """

    results = []

    pattern = re.compile(
        r"(\d{1,2}/\s*\d{1,2}).{0,30}?"
        r"([１-９0-9]+)\s*着"
    )

    for m in pattern.finditer(text):
        place = m.group(2)

        place = (
            place.replace("１", "1")
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
            rank = int(place)
        except Exception:
            continue

        results.append({
            "date": clean_text(m.group(1)),
            "rank": rank,
        })

    return results[:10]


# ============================================================
# 選手情報抽出
# ============================================================

def parse_riders_from_race(html, venue_name, venue_code, race_no):
    parser = parse_html(html)

    text = parser.get_text()
    rows = parser.get_rows()

    riders = []

    # --------------------------------------------------------
    # 方法1：table rowから取得
    # --------------------------------------------------------

    for row in rows:

        row_text = " ".join(row)

        if "競走得点" not in row_text:
            continue

        # 車番
        car_no = None

        for cell in row:
            value = to_int(cell)

            if value is not None and 1 <= value <= 9:
                car_no = value
                break

        if car_no is None:
            continue

        # 競走得点
        score_match = re.search(
            r"競走得点\s*[:：]\s*(\d+(?:\.\d+)?)",
            row_text
        )

        score = None

        if score_match:
            score = float(score_match.group(1))

        # 着順
        finish = [0, 0, 0, 0]

        finish_match = re.search(
            r"着\s*順\s*[:：]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)",
            row_text
        )

        if finish_match:
            finish = [
                int(finish_match.group(1)),
                int(finish_match.group(2)),
                int(finish_match.group(3)),
                int(finish_match.group(4)),
            ]

        # 決まり手
        kimari = [0, 0, 0, 0]

        kimari_match = re.search(
            r"決まり手\s*[:：]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)",
            row_text
        )

        if kimari_match:
            kimari = [
                int(kimari_match.group(1)),
                int(kimari_match.group(2)),
                int(kimari_match.group(3)),
                int(kimari_match.group(4)),
            ]

        # 脚質
        style = ""

        style_match = re.search(
            r"\b(逃|両|追)\b",
            row_text
        )

        if style_match:
            style = style_match.group(1)

        # 選手名
        name = ""

        for cell in row:
            if (
                len(cell) >= 2
                and "競走得点" not in cell
                and "着順" not in cell
                and "決まり手" not in cell
                and "宮崎" not in cell
                and "茨城" not in cell
                and "東京" not in cell
                and "静岡" not in cell
                and "愛知" not in cell
                and "千葉" not in cell
            ):
                # 名前らしい文字列
                if re.search(r"[一-龯ぁ-んァ-ヶ]", cell):
                    if not re.search(
                        r"(基本情報|直近成績|条件別成績|前場所|今場所|前々場所)",
                        cell
                    ):
                        name = cell
                        break

        # 年齢・期別
        period = None
        age = None

        period_match = re.search(r"(\d{2,3})期", row_text)

        if period_match:
            period = int(period_match.group(1))

        age_match = re.search(r"(\d{2})歳", row_text)

        if age_match:
            age = int(age_match.group(1))

        # 府県
        prefecture = ""

        prefectures = [
            "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
            "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
            "新潟", "富山", "石川", "福井", "山梨", "長野", "岐阜",
            "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫",
            "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "山口",
            "徳島", "香川", "愛媛", "高知", "福岡", "佐賀", "長崎",
            "熊本", "大分", "宮崎", "鹿児島", "沖縄"
        ]

        for pref in prefectures:
            if pref in row_text:
                prefecture = pref
                break

        riders.append({
            "car_no": car_no,
            "name": name,
            "age": age,
            "period": period,
            "prefecture": prefecture,
            "style": style,
            "score": score,
            "finish": {
                "1": finish[0],
                "2": finish[1],
                "3": finish[2],
                "out": finish[3],
            },
            "kimari": {
                "nige": kimari[0],
                "maki": kimari[1],
                "sashi": kimari[2],
                "mark": kimari[3],
            },
        })

    # --------------------------------------------------------
    # 重複除去
    # --------------------------------------------------------

    unique = {}

    for rider in riders:
        car = rider["car_no"]

        if car not in unique:
            unique[car] = rider

    riders = [
        unique[k]
        for k in sorted(unique.keys())
    ]

    # --------------------------------------------------------
    # 方法2：行解析で不足分を補う
    # --------------------------------------------------------

    if len(riders) < 3:

        lines = [
            clean_text(x)
            for x in text.splitlines()
            if clean_text(x)
        ]

        current_car = None
        current_name = None
        current_score = None
        current_style = None
        current_finish = [0, 0, 0, 0]
        current_kimari = [0, 0, 0, 0]

        for line in lines:

            m_car = re.match(r"^([1-9])$", line)

            if m_car:
                current_car = int(m_car.group(1))
                current_name = None
                current_score = None
                current_style = None
                current_finish = [0, 0, 0, 0]
                current_kimari = [0, 0, 0, 0]
                continue

            if current_car is None:
                continue

            if "競走得点" in line:
                current_score = to_float(line)

            if "着順" in line:
                current_finish = parse_four_numbers(line)

            if "決まり手" in line:
                current_kimari = parse_four_numbers(line)

            if line in ("逃", "両", "追"):
                current_style = line

            if (
                current_name is None
                and re.search(r"[一-龯ぁ-んァ-ヶ]", line)
                and not any(
                    x in line
                    for x in [
                        "競走得点",
                        "着順",
                        "決まり手",
                        "今場所",
                        "前場所",
                        "前々場所",
                    ]
                )
            ):
                if len(line) >= 2:
                    current_name = line

            if current_name and current_score is not None:

                existing = next(
                    (
                        x for x in riders
                        if x["car_no"] == current_car
                    ),
                    None
                )

                if existing is None:
                    riders.append({
                        "car_no": current_car,
                        "name": current_name,
                        "age": None,
                        "period": None,
                        "prefecture": "",
                        "style": current_style or "",
                        "score": current_score,
                        "finish": {
                            "1": current_finish[0],
                            "2": current_finish[1],
                            "3": current_finish[2],
                            "out": current_finish[3],
                        },
                        "kimari": {
                            "nige": current_kimari[0],
                            "maki": current_kimari[1],
                            "sashi": current_kimari[2],
                            "mark": current_kimari[3],
                        },
                    })

    riders.sort(key=lambda x: x["car_no"])

    # --------------------------------------------------------
    # レース全体の決まり手割合
    # --------------------------------------------------------

    venue_kimari = {
        "nige": None,
        "maki": None,
        "sashi": None,
    }

    m = re.search(
        r"1着決まり手割合.*?"
        r"逃げ\s*[|｜]\s*捲り\s*[|｜]\s*差し"
        r".*?"
        r"(\d+(?:\.\d+)?)%\s*[|｜]\s*"
        r"(\d+(?:\.\d+)?)%\s*[|｜]\s*"
        r"(\d+(?:\.\d+)?)%",
        text,
        re.S
    )

    if m:
        venue_kimari = {
            "nige": float(m.group(1)),
            "maki": float(m.group(2)),
            "sashi": float(m.group(3)),
        }

    return riders, venue_kimari


# ============================================================
# レースタイトル
# ============================================================

def parse_race_title(html, race_no):
    parser = parse_html(html)
    text = parser.get_text()

    title = ""

    patterns = [
        rf"{race_no}R出走表",
        rf"第{race_no}レース",
    ]

    for pattern in patterns:
        m = re.search(pattern + r".{0,80}", text)

        if m:
            title = clean_text(m.group(0))
            break

    # 例：Ｌ級ガ予２
    class_match = re.search(
        r"(Ｌ級[^\s]{1,10}|Ａ級[^\s]{1,10}|Ｓ級[^\s]{1,10})",
        text
    )

    race_class = ""

    if class_match:
        race_class = class_match.group(1)

    return {
        "title": title,
        "class": race_class,
    }


# ============================================================
# AI
# ============================================================

def calculate_ai_score(rider, venue_kimari):
    score = 0.0

    race_point = rider.get("score")

    # --------------------------------------------------------
    # 競走得点
    # --------------------------------------------------------

    if race_point is not None:
        score += race_point * 1.15

    # --------------------------------------------------------
    # 過去成績
    # --------------------------------------------------------

    finish = rider.get("finish", {})

    first = finish.get("1", 0)
    second = finish.get("2", 0)
    third = finish.get("3", 0)
    out = finish.get("out", 0)

    total = first + second + third + out

    if total > 0:
        win_rate = first / total
        top2_rate = (first + second) / total
        top3_rate = (first + second + third) / total

        score += win_rate * 35
        score += top2_rate * 18
        score += top3_rate * 12

    # --------------------------------------------------------
    # 決まり手
    # --------------------------------------------------------

    kimari = rider.get("kimari", {})

    nige = kimari.get("nige", 0)
    maki = kimari.get("maki", 0)
    sashi = kimari.get("sashi", 0)
    mark = kimari.get("mark", 0)

    score += nige * 0.9
    score += maki * 1.0
    score += sashi * 0.85
    score += mark * 0.4

    # --------------------------------------------------------
    # 脚質
    # --------------------------------------------------------

    style = rider.get("style", "")

    if style == "逃":
        score += 2.5

        if venue_kimari.get("nige") is not None:
            score += venue_kimari["nige"] * 0.10

    elif style == "両":
        score += 3.0

        if venue_kimari.get("maki") is not None:
            score += venue_kimari["maki"] * 0.05

    elif style == "追":
        score += 2.0

        if venue_kimari.get("sashi") is not None:
            score += venue_kimari["sashi"] * 0.08

    # --------------------------------------------------------
    # 車番補正
    # 車番だけで順位を決めないよう弱くする
    # --------------------------------------------------------

    car = rider.get("car_no", 7)

    if car == 1:
        score += 1.5
    elif car == 2:
        score += 1.0
    elif car == 3:
        score += 0.5

    return round(score, 3)


def add_ai(riders, venue_kimari):
    for rider in riders:
        rider["ai_score"] = calculate_ai_score(
            rider,
            venue_kimari
        )

    riders.sort(
        key=lambda x: x.get("ai_score", 0),
        reverse=True
    )

    labels = [
        "本命",
        "対抗",
        "単穴",
        "穴",
    ]

    for index, rider in enumerate(riders):

        if index < len(labels):
            rider["ai_rank"] = index + 1
            rider["ai_label"] = labels[index]
        else:
            rider["ai_rank"] = index + 1
            rider["ai_label"] = ""

    return riders


# ============================================================
# 1レース取得
# ============================================================

def fetch_race(args):
    venue_name, venue_code, date_str, race_no = args

    url = get_race_url(
        venue_code,
        date_str,
        race_no
    )

    html = fetch(url)

    if not html:
        return {
            "venue": venue_name,
            "venue_code": venue_code,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "status": "error",
        }

    riders, venue_kimari = parse_riders_from_race(
        html,
        venue_name,
        venue_code,
        race_no
    )

    race_info = parse_race_title(
        html,
        race_no
    )

    riders = add_ai(
        riders,
        venue_kimari
    )

    return {
        "venue": venue_name,
        "venue_code": venue_code,
        "race_no": race_no,
        "url": url,
        "title": race_info["title"],
        "class": race_info["class"],
        "venue_kimari": venue_kimari,
        "riders": riders,
        "status": "ok" if riders else "partial",
    }


# ============================================================
# 開催場処理
# ============================================================

def get_venue_data(venue, date_str):

    venue_name = venue["name"]
    venue_code = venue["code"]

    print(f"\n開催場: {venue_name} ({venue_code})")

    all_url = get_all_race_url(
        venue_code,
        date_str
    )

    html = fetch(all_url)

    if not html:
        print("  全Rページ取得失敗")
        return []

    race_numbers = find_race_numbers(html)

    print(
        f"  全Rページ取得OK / "
        f"対象R: {len(race_numbers)}"
    )

    return [
        (
            venue_name,
            venue_code,
            date_str,
            race_no
        )
        for race_no in race_numbers
    ]


# ============================================================
# 保存
# ============================================================

def save(data):
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with OUTPUT_FILE.open(
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
# メイン
# ============================================================

def main():

    start_time = time.time()

    now = datetime.now(JST)
    date_str = now.strftime("%Y%m%d")
    display_date = now.strftime("%Y-%m-%d")

    print()
    print("==============================")
    print(" KEIRIN AI DATA UPDATE")
    print("==============================")
    print(f"対象日: {display_date}")
    print("==============================")

    # --------------------------------------------------------
    # 1. 開催場一覧
    # --------------------------------------------------------

    race_list_url = (
        f"{BASE_URL}"
        f"RaceListInfo.do"
        f"?kaisaiBi={date_str}"
    )

    print(
        f"GET: {race_list_url}"
    )

    race_list_html = fetch(
        race_list_url
    )

    if not race_list_html:
        print("開催一覧の取得に失敗しました")
        return

    venues = find_today_venues(
        race_list_html
    )

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
            x["name"]
            for x in venues
        )
    )

    # --------------------------------------------------------
    # 2. 全Rページから対象レースを作る
    # --------------------------------------------------------

    jobs = []

    for venue in venues:

        venue_jobs = get_venue_data(
            venue,
            date_str
        )

        jobs.extend(
            venue_jobs
        )

    print()
    print(
        f"詳細取得対象レース: {len(jobs)}"
    )

    if not jobs:
        print("レースがありません")
        return

    # --------------------------------------------------------
    # 3. 並列取得
    # --------------------------------------------------------

    print()
    print(
        f"詳細出走表を最大{MAX_WORKERS}並列で取得..."
    )

    races = []

    completed = 0
    failed = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_race,
                job
            ): job
            for job in jobs
        }

        for future in as_completed(futures):

            job = futures[future]

            try:
                result = future.result()

                races.append(result)

                completed += 1

                if result["status"] == "error":
                    failed += 1

                print(
                    f"  [{completed}/{len(jobs)}] "
                    f"{result['venue']} "
                    f"{result['race_no']}R "
                    f"選手{len(result['riders'])}人"
                )

            except Exception as e:

                failed += 1

                print(
                    f"  [ERROR] "
                    f"{job[0]} {job[3]}R "
                    f"{e}"
                )

    # --------------------------------------------------------
    # 4. 並び順
    # --------------------------------------------------------

    races.sort(
        key=lambda x: (
            x["venue_code"],
            x["race_no"]
        )
    )

    # --------------------------------------------------------
    # 5. AI集計
    # --------------------------------------------------------

    total_riders = sum(
        len(r["riders"])
        for r in races
    )

    ai_races = sum(
        1
        for r in races
        if r["riders"]
    )

    ai_riders = sum(
        1
        for r in races
        for rider in r["riders"]
        if rider.get("ai_score") is not None
    )

    # --------------------------------------------------------
    # 6. 出力
    # --------------------------------------------------------

    output = {
        "version": "2.0",
        "updated_at": now.isoformat(),
        "date": date_str,
        "date_display": display_date,
        "venue_count": len(venues),
        "race_count": len(races),
        "rider_count": total_riders,
        "ai_race_count": ai_races,
        "ai_rider_count": ai_riders,
        "failed_race_count": failed,
        "venues": venues,
        "races": races,
    }

    save(output)

    elapsed = time.time() - start_time

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    print()
    print("==============================")
    print(" UPDATE COMPLETE")
    print("==============================")
    print(
        f"開催場: {len(venues)}"
    )
    print(
        f"レース: {len(races)}"
    )
    print(
        f"選手: {total_riders}"
    )
    print(
        f"AI評価レース: {ai_races}"
    )
    print(
        f"AI評価選手: {ai_riders}"
    )
    print(
        f"取得失敗レース: {failed}"
    )
    print(
        f"処理時間: {elapsed:.1f}秒"
    )
    print(
        f"保存先: {OUTPUT_FILE}"
    )
    print("==============================")


if __name__ == "__main__":
    main()
