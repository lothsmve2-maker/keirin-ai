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
# Version 3.0
#
# データ取得
# ・開催場
# ・レース
# ・選手名
# ・年齢
# ・期別
# ・府県
# ・脚質
# ・競走得点
# ・着順1-2-3-外
# ・決まり手
# ・今場所
# ・前場所
# ・前々場所
#
# AI
# ・競走得点
# ・1着率
# ・2連対率
# ・3連対率
# ・決まり手
# ・脚質
# ・直近成績
# ・今場所成績
# ・競輪場の決まり手傾向
# ・車番補正
#
# ※ 車番だけで順位が決まらないようにする
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


PREFECTURES = [
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
    "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
    "新潟", "富山", "石川", "福井", "山梨", "長野", "岐阜",
    "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫",
    "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "山口",
    "徳島", "香川", "愛媛", "高知", "福岡", "佐賀", "長崎",
    "熊本", "大分", "宮崎", "鹿児島", "沖縄",
]


# ============================================================
# HTML
# ============================================================

class SimpleHTMLParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.text_parts = []

        self.in_table = False
        self.in_tr = False
        self.in_td = False

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
            self.current_cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("td", "th") and self.in_td:

            value = clean_text(
                " ".join(self.current_cell)
            )

            self.current_row.append(value)

            self.in_td = False
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


def clean_text(value):

    if value is None:
        return ""

    value = unescape(str(value))

    value = value.replace("\xa0", " ")
    value = value.replace("\u3000", " ")

    value = re.sub(r"\s+", " ", value)

    return value.strip()


# ============================================================
# HTTP
# ============================================================

def fetch(url, retries=RETRIES):

    last_error = None

    for attempt in range(retries + 1):

        try:

            req = Request(
                url,
                headers=HEADERS
            )

            with urlopen(
                req,
                timeout=TIMEOUT
            ) as response:

                data = response.read()

            return data.decode(
                "utf-8",
                errors="ignore"
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError
        ) as e:

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
# 数値
# ============================================================

def to_float(value):

    if value is None:
        return None

    m = re.search(
        r"(\d+(?:\.\d+)?)",
        str(value)
    )

    if not m:
        return None

    try:
        return float(m.group(1))
    except Exception:
        return None


def to_int(value):

    if value is None:
        return None

    m = re.search(
        r"(\d+)",
        str(value)
    )

    if not m:
        return None

    try:
        return int(m.group(1))
    except Exception:
        return None


# ============================================================
# 開催場
# ============================================================

def find_today_venues(html):

    text = clean_text(html)

    venues = []

    for venue_name, code in VENUE_CODES.items():

        if venue_name in text:

            venues.append({
                "name": venue_name,
                "code": code,
            })

    unique = {}

    for venue in venues:
        unique[venue["code"]] = venue

    return list(unique.values())


# ============================================================
# URL
# ============================================================

def get_all_race_url(
    venue_code,
    date_str
):

    return (
        f"{BASE_URL}"
        f"AllRaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={date_str}"
    )


def get_race_url(
    venue_code,
    date_str,
    race_no
):

    return (
        f"{BASE_URL}"
        f"RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={date_str}"
        f"&raceNo={race_no}"
    )


# ============================================================
# レース番号
# ============================================================

def find_race_numbers(html):

    race_numbers = set()

    patterns = [
        r"raceNo=(\d+)",
        r"第(\d+)レース",
        r"第(\d+)R",
    ]

    for pattern in patterns:

        for m in re.finditer(
            pattern,
            html
        ):

            try:

                number = int(
                    m.group(1)
                )

                if 1 <= number <= 12:
                    race_numbers.add(number)

            except Exception:
                pass

    if not race_numbers:

        return list(range(1, 13))

    return sorted(
        race_numbers
    )


# ============================================================
# 着順
# ============================================================

def parse_four_numbers(value):

    if not value:
        return [
            0, 0, 0, 0
        ]

    nums = re.findall(
        r"\d+",
        value
    )

    result = []

    for n in nums[:4]:

        try:
            result.append(
                int(n)
            )
        except Exception:
            result.append(0)

    while len(result) < 4:
        result.append(0)

    return result


# ============================================================
# 選手名
# ============================================================

def extract_name_age_period(
    row
):

    """
    最重要部分。

    オッズパークでは、

    選手名
    47歳／86期

    のような情報が同じセル、
    または隣接セルに存在する。

    「◎ ○ ▲ △ × 注」を
    選手名として取得しない。
    """

    name = ""
    age = None
    period = None

    # --------------------------------------------------------
    # ① 同一セル
    # --------------------------------------------------------

    for i, cell in enumerate(row):

        m = re.search(
            r"(.+?)"
            r"\s*(\d{1,2})歳"
            r"\s*[／/]\s*"
            r"(\d{2,3})期",
            cell
        )

        if m:

            candidate = clean_text(
                m.group(1)
            )

            candidate = re.sub(
                r"^[◎○▲△×注★☆＊*\s]+",
                "",
                candidate
            )

            if is_valid_rider_name(
                candidate
            ):

                name = candidate
                age = int(m.group(2))
                period = int(m.group(3))

                return (
                    name,
                    age,
                    period
                )

    # --------------------------------------------------------
    # ② 年齢/期別セルを探して直前セルを見る
    # --------------------------------------------------------

    for i, cell in enumerate(row):

        m = re.search(
            r"(\d{1,2})歳"
            r"\s*[／/]\s*"
            r"(\d{2,3})期",
            cell
        )

        if not m:
            continue

        age = int(
            m.group(1)
        )

        period = int(
            m.group(2)
        )

        # 直前セル
        if i > 0:

            candidate = clean_text(
                row[i - 1]
            )

            candidate = re.sub(
                r"^[◎○▲△×注★☆＊*\s]+",
                "",
                candidate
            )

            if is_valid_rider_name(
                candidate
            ):

                name = candidate

                return (
                    name,
                    age,
                    period
                )

    return (
        "",
        age,
        period
    )


def is_valid_rider_name(
    name
):

    if not name:
        return False

    # 予想印だけは絶対に除外
    invalid = {
        "◎",
        "○",
        "▲",
        "△",
        "×",
        "注",
        "◎ ○ ▲ △ × 注",
        "○ ▲ △ × 注",
    }

    if name in invalid:
        return False

    if "◎" in name and len(name) <= 10:
        return False

    if "○" in name and len(name) <= 10:
        return False

    if "▲" in name and len(name) <= 10:
        return False

    if "△" in name and len(name) <= 10:
        return False

    if "競走得点" in name:
        return False

    if "決まり手" in name:
        return False

    if "着順" in name:
        return False

    if "今場所" in name:
        return False

    if "前場所" in name:
        return False

    # 日本語が含まれている必要がある
    if not re.search(
        r"[一-龯ぁ-んァ-ヶ]",
        name
    ):
        return False

    return len(name) >= 2


# ============================================================
# 成績
# ============================================================

def parse_recent_results(
    text
):

    results = []

    # 例
    # 10/ 6 初特選 ３着
    # 9/21 初特選 ４着
    pattern = re.compile(
        r"(\d{1,2})/\s*(\d{1,2})"
        r".{0,50}?"
        r"([１２３４５６７８９0-9]+)着"
    )

    for m in pattern.finditer(text):

        rank_text = (
            m.group(3)
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

            rank = int(
                rank_text
            )

        except Exception:
            continue

        if not 1 <= rank <= 9:
            continue

        results.append({
            "month": int(m.group(1)),
            "day": int(m.group(2)),
            "rank": rank,
        })

    return results[:12]


def recent_form_score(
    results
):

    if not results:
        return 0.0

    weights = [
        1.00,
        0.90,
        0.80,
        0.70,
        0.60,
        0.50,
    ]

    score = 0.0
    weight_sum = 0.0

    for i, item in enumerate(
        results[:6]
    ):

        rank = item["rank"]

        weight = (
            weights[i]
            if i < len(weights)
            else 0.40
        )

        # 1着=10
        # 2着=8
        # 3着=6
        # 4着=4
        # 5着以下=2以下
        form = max(
            0.0,
            12.0 - rank * 1.8
        )

        score += (
            form * weight
        )

        weight_sum += weight

    if weight_sum == 0:
        return 0.0

    return (
        score / weight_sum
    )


# ============================================================
# 選手データ
# ============================================================

def parse_riders_from_race(
    html,
    venue_name,
    venue_code,
    race_no
):

    parser = parse_html(
        html
    )

    text = parser.get_text()
    rows = parser.get_rows()

    riders = []

    # --------------------------------------------------------
    # テーブル行解析
    # --------------------------------------------------------

    for row in rows:

        row_text = " ".join(row)

        if "競走得点" not in row_text:
            continue

        # 車番
        car_no = None

        for cell in row:

            # 単独の1～9を優先
            if re.fullmatch(
                r"[1-9]",
                cell
            ):

                car_no = int(cell)
                break

        if car_no is None:
            continue

        # ----------------------------------------------------
        # 選手名・年齢・期別
        # ----------------------------------------------------

        (
            name,
            age,
            period
        ) = extract_name_age_period(
            row
        )

        # 名前が取れない選手は登録しない
        if not is_valid_rider_name(
            name
        ):
            continue

        # ----------------------------------------------------
        # 府県
        # ----------------------------------------------------

        prefecture = ""

        for pref in PREFECTURES:

            if pref in row_text:

                prefecture = pref
                break

        # ----------------------------------------------------
        # 脚質
        # ----------------------------------------------------

        style = ""

        style_match = re.search(
            r"(?:^|\s)(逃|両|追)(?:\s|$)",
            row_text
        )

        if style_match:
            style = style_match.group(1)

        # ----------------------------------------------------
        # 競走得点
        # ----------------------------------------------------

        score = None

        score_match = re.search(
            r"競走得点\s*[:：]\s*"
            r"(\d+(?:\.\d+)?)",
            row_text
        )

        if score_match:

            score = float(
                score_match.group(1)
            )

        # ----------------------------------------------------
        # 着順
        # ----------------------------------------------------

        finish = [
            0, 0, 0, 0
        ]

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
                int(
                    finish_match.group(1)
                ),
                int(
                    finish_match.group(2)
                ),
                int(
                    finish_match.group(3)
                ),
                int(
                    finish_match.group(4)
                ),
            ]

        # ----------------------------------------------------
        # 決まり手
        # ----------------------------------------------------

        kimari = [
            0, 0, 0, 0
        ]

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
                int(
                    kimari_match.group(1)
                ),
                int(
                    kimari_match.group(2)
                ),
                int(
                    kimari_match.group(3)
                ),
                int(
                    kimari_match.group(4)
                ),
            ]

        # ----------------------------------------------------
        # 直近成績
        # ----------------------------------------------------

        recent_results = (
            parse_recent_results(
                row_text
            )
        )

        # ----------------------------------------------------
        # 今場所成績
        # ----------------------------------------------------

        current_results = []

        # 今日以外の直近成績も
        # row_textから取得
        if recent_results:
            current_results = (
                recent_results[:3]
            )

        # ----------------------------------------------------
        # データ
        # ----------------------------------------------------

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

            "recent_results": recent_results,

            "current_results": current_results,

        })

    # --------------------------------------------------------
    # 重複排除
    # --------------------------------------------------------

    unique = {}

    for rider in riders:

        car = rider["car_no"]

        if car not in unique:
            unique[car] = rider

    riders = [
        unique[k]
        for k in sorted(
            unique.keys()
        )
    ]

    # --------------------------------------------------------
    # 競輪場の1着決まり手
    # --------------------------------------------------------

    venue_kimari = {
        "nige": None,
        "maki": None,
        "sashi": None,
    }

    m = re.search(
        r"逃げ\s*\|\s*捲り\s*\|\s*差し"
        r".{0,200}?"
        r"(\d+(?:\.\d+)?)%\s*\|\s*"
        r"(\d+(?:\.\d+)?)%\s*\|\s*"
        r"(\d+(?:\.\d+)?)%",
        text,
        re.S
    )

    if m:

        venue_kimari = {

            "nige": float(
                m.group(1)
            ),

            "maki": float(
                m.group(2)
            ),

            "sashi": float(
                m.group(3)
            ),
        }

    return (
        riders,
        venue_kimari
    )


# ============================================================
# レース情報
# ============================================================

def parse_race_info(
    html,
    race_no
):

    parser = parse_html(
        html
    )

    text = parser.get_text()

    race_class = ""

    class_patterns = [
        r"\(([ＳＡＬ][級][^)]*)\)",
        r"(Ｓ級[^\s]{1,10})",
        r"(Ａ級[^\s]{1,10})",
        r"(Ｌ級[^\s]{1,10})",
    ]

    for pattern in class_patterns:

        m = re.search(
            pattern,
            text
        )

        if m:

            race_class = clean_text(
                m.group(1)
            )

            break

    return {
        "class": race_class
    }


# ============================================================
# AI
# ============================================================

def calculate_ai_score(
    rider,
    venue_kimari,
    race_riders
):

    score = 0.0

    race_point = rider.get(
        "score"
    )

    # --------------------------------------------------------
    # ① 競走得点
    # レース内で相対評価
    # --------------------------------------------------------

    points = [
        r.get("score")
        for r in race_riders
        if r.get("score") is not None
    ]

    if race_point is not None and points:

        maximum = max(points)
        minimum = min(points)

        if maximum > minimum:

            normalized = (
                (race_point - minimum)
                /
                (maximum - minimum)
            )

        else:

            normalized = 0.5

        # 最大35点
        score += (
            normalized * 35.0
        )

    # --------------------------------------------------------
    # ② 1着率 / 2連対率 / 3連対率
    # --------------------------------------------------------

    finish = rider.get(
        "finish",
        {}
    )

    first = finish.get(
        "1", 0
    )

    second = finish.get(
        "2", 0
    )

    third = finish.get(
        "3", 0
    )

    out = finish.get(
        "out", 0
    )

    total = (
        first
        + second
        + third
        + out
    )

    if total > 0:

        win_rate = (
            first / total
        )

        top2_rate = (
            first + second
        ) / total

        top3_rate = (
            first
            + second
            + third
        ) / total

        # 最大30点
        score += (
            win_rate * 30.0
        )

        score += (
            top2_rate * 12.0
        )

        score += (
            top3_rate * 8.0
        )

    # --------------------------------------------------------
    # ③ 決まり手
    # --------------------------------------------------------

    kimari = rider.get(
        "kimari",
        {}
    )

    nige = kimari.get(
        "nige", 0
    )

    maki = kimari.get(
        "maki", 0
    )

    sashi = kimari.get(
        "sashi", 0
    )

    mark = kimari.get(
        "mark", 0
    )

    # 自力能力を評価
    score += nige * 0.65
    score += maki * 0.75
    score += sashi * 0.45
    score += mark * 0.20

    # --------------------------------------------------------
    # ④ 脚質 × 競輪場傾向
    # --------------------------------------------------------

    style = rider.get(
        "style",
        ""
    )

    if style == "逃":

        score += 2.0

        if venue_kimari.get(
            "nige"
        ) is not None:

            score += (
                venue_kimari["nige"]
                * 0.10
            )

    elif style == "両":

        score += 2.5

        if venue_kimari.get(
            "maki"
        ) is not None:

            score += (
                venue_kimari["maki"]
                * 0.07
            )

    elif style == "追":

        score += 1.5

        if venue_kimari.get(
            "sashi"
        ) is not None:

            score += (
                venue_kimari["sashi"]
                * 0.10
            )

    # --------------------------------------------------------
    # ⑤ 直近成績
    # --------------------------------------------------------

    recent = rider.get(
        "recent_results",
        []
    )

    form = recent_form_score(
        recent
    )

    # 最大10点程度
    score += (
        form * 0.8
    )

    # --------------------------------------------------------
    # ⑥ 車番
    #
    # ここは弱くする。
    # 車番だけで本命にはしない。
    # --------------------------------------------------------

    car = rider.get(
        "car_no",
        7
    )

    if car == 1:
        score += 0.8

    elif car == 2:
        score += 0.5

    elif car == 3:
        score += 0.3

    # --------------------------------------------------------
    # ⑦ 年齢補正
    #
    # 極端な補正はしない
    # --------------------------------------------------------

    age = rider.get(
        "age"
    )

    if age is not None:

        if 22 <= age <= 32:
            score += 0.8

        elif 33 <= age <= 39:
            score += 0.5

        elif age >= 50:
            score -= 0.3

    return round(
        score,
        3
    )


def add_ai(
    riders,
    venue_kimari
):

    if not riders:
        return riders

    # --------------------------------------------------------
    # AIスコア
    # --------------------------------------------------------

    for rider in riders:

        rider["ai_score"] = (
            calculate_ai_score(
                rider,
                venue_kimari,
                riders
            )
        )

    # --------------------------------------------------------
    # AI順位
    # --------------------------------------------------------

    riders.sort(
        key=lambda x: (
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "score",
                0
            )
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # 評価
    # --------------------------------------------------------

    labels = [
        "本命",
        "対抗",
        "単穴",
        "穴",
    ]

    for index, rider in enumerate(
        riders
    ):

        rider["ai_rank"] = (
            index + 1
        )

        if index < len(labels):

            rider["ai_label"] = (
                labels[index]
            )

        else:

            rider["ai_label"] = ""

    return riders


# ============================================================
# レース取得
# ============================================================

def fetch_race(args):

    (
        venue_name,
        venue_code,
        date_str,
        race_no
    ) = args

    url = get_race_url(
        venue_code,
        date_str,
        race_no
    )

    html = fetch(
        url
    )

    if not html:

        return {
            "venue": venue_name,
            "venue_code": venue_code,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "status": "error",
        }

    (
        riders,
        venue_kimari
    ) = parse_riders_from_race(
        html,
        venue_name,
        venue_code,
        race_no
    )

    race_info = parse_race_info(
        html,
        race_no
    )

    if riders:

        riders = add_ai(
            riders,
            venue_kimari
        )

    return {

        "venue": venue_name,

        "venue_code": venue_code,

        "race_no": race_no,

        "url": url,

        "class": race_info[
            "class"
        ],

        "venue_kimari": (
            venue_kimari
        ),

        "riders": riders,

        "status": (
            "ok"
            if riders
            else "partial"
        ),
    }


# ============================================================
# 開催場
# ============================================================

def get_venue_jobs(
    venue,
    date_str
):

    venue_name = venue[
        "name"
    ]

    venue_code = venue[
        "code"
    ]

    print(
        f"\n開催場: "
        f"{venue_name} "
        f"({venue_code})"
    )

    url = get_all_race_url(
        venue_code,
        date_str
    )

    html = fetch(
        url
    )

    if not html:

        print(
            "  全Rページ取得失敗"
        )

        return []

    race_numbers = (
        find_race_numbers(
            html
        )
    )

    print(
        "  全Rページ取得OK / "
        f"対象R: {len(race_numbers)}"
    )

    jobs = []

    for race_no in race_numbers:

        jobs.append(
            (
                venue_name,
                venue_code,
                date_str,
                race_no
            )
        )

    return jobs


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
# MAIN
# ============================================================

def main():

    start_time = time.time()

    now = datetime.now(
        JST
    )

    date_str = now.strftime(
        "%Y%m%d"
    )

    display_date = now.strftime(
        "%Y-%m-%d"
    )

    print()
    print("==============================")
    print(" KEIRIN AI DATA UPDATE")
    print(" VERSION 3.0")
    print("==============================")
    print(
        f"対象日: {display_date}"
    )
    print("==============================")

    # --------------------------------------------------------
    # 1. 開催場
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

        print(
            "開催一覧の取得に失敗しました"
        )

        return

    venues = (
        find_today_venues(
            race_list_html
        )
    )

    print(
        f"開催場数: {len(venues)}"
    )

    if not venues:

        print(
            "本日の開催場を"
            "取得できませんでした"
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
    # 2. レース一覧
    # --------------------------------------------------------

    jobs = []

    for venue in venues:

        jobs.extend(
            get_venue_jobs(
                venue,
                date_str
            )
        )

    print()
    print(
        f"詳細取得対象レース: "
        f"{len(jobs)}"
    )

    if not jobs:

        print(
            "レースがありません"
        )

        return

    # --------------------------------------------------------
    # 3. 並列取得
    # --------------------------------------------------------

    print()
    print(
        f"詳細出走表を最大"
        f"{MAX_WORKERS}並列で取得..."
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

        for future in as_completed(
            futures
        ):

            job = futures[
                future
            ]

            try:

                result = (
                    future.result()
                )

                races.append(
                    result
                )

                completed += 1

                if result[
                    "status"
                ] == "error":

                    failed += 1

                print(
                    f"  [{completed}/"
                    f"{len(jobs)}] "
                    f"{result['venue']} "
                    f"{result['race_no']}R "
                    f"選手"
                    f"{len(result['riders'])}人"
                )

            except Exception as e:

                failed += 1

                print(
                    f"  [ERROR] "
                    f"{job[0]} "
                    f"{job[3]}R "
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
    # 5. 統計
    # --------------------------------------------------------

    total_riders = sum(
        len(
            r["riders"]
        )
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
        if rider.get(
            "ai_score"
        ) is not None
    )

    valid_names = sum(
        1
        for r in races
        for rider in r["riders"]
        if is_valid_rider_name(
            rider.get(
                "name",
                ""
            )
        )
    )

    # --------------------------------------------------------
    # 6. 出力
    # --------------------------------------------------------

    output = {

        "version": "3.0",

        "updated_at":
            now.isoformat(),

        "date":
            date_str,

        "date_display":
            display_date,

        "venue_count":
            len(venues),

        "race_count":
            len(races),

        "rider_count":
            total_riders,

        "valid_name_count":
            valid_names,

        "ai_race_count":
            ai_races,

        "ai_rider_count":
            ai_riders,

        "failed_race_count":
            failed,

        "venues":
            venues,

        "races":
            races,
    }

    save(
        output
    )

    elapsed = (
        time.time()
        - start_time
    )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    print()
    print("==============================")
    print(" UPDATE COMPLETE")
    print("==============================")

    print(
        f"開催場: "
        f"{len(venues)}"
    )

    print(
        f"レース: "
        f"{len(races)}"
    )

    print(
        f"選手: "
        f"{total_riders}"
    )

    print(
        f"正しい選手名: "
        f"{valid_names}"
    )

    print(
        f"AI評価レース: "
        f"{ai_races}"
    )

    print(
        f"AI評価選手: "
        f"{ai_riders}"
    )

    print(
        f"取得失敗レース: "
        f"{failed}"
    )

    print(
        f"処理時間: "
        f"{elapsed:.1f}秒"
    )

    print(
        f"保存先: "
        f"{OUTPUT_FILE}"
    )

    print("==============================")


if __name__ == "__main__":
    main()
