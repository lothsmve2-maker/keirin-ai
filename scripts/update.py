# ============================================================
# KEIRIN AI DATA UPDATE v4.0
# 展開・コメント・ライン・飛びつき対応予想エンジン
# ============================================================

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
# 基本設定
# ============================================================

JST = timezone(timedelta(hours=9))

BASE_URL = "https://www.oddspark.com/keirin/"
SP_BASE_URL = "https://sp.oddspark.com/keirin/yosou/"

OUTPUT = Path("data/today.json")

MAX_WORKERS = 8
TIMEOUT = 15
RETRIES = 1


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
    "千葉": "32",
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
# オッズパークの予想ページ用 slug
# ============================================================

VENUE_SLUGS = {
    "函館": "hakodate",
    "青森": "aomori",
    "いわき平": "iwakitaira",
    "弥彦": "yahiko",
    "前橋": "maebashi",
    "取手": "toride",
    "宇都宮": "utsunomiya",
    "大宮": "omiya",
    "西武園": "seibuen",
    "京王閣": "keiokaku",
    "立川": "tachikawa",
    "松戸": "matsudo",
    "千葉": "chiba",
    "川崎": "kawasaki",
    "平塚": "hiratsuka",
    "小田原": "odawara",
    "伊東": "ito",
    "静岡": "shizuoka",
    "名古屋": "nagoya",
    "岐阜": "gifu",
    "大垣": "ogaki",
    "豊橋": "toyohashi",
    "富山": "toyama",
    "松阪": "matsusaka",
    "四日市": "yokkaichi",
    "福井": "fukui",
    "奈良": "nara",
    "向日町": "mukomachi",
    "和歌山": "wakayama",
    "岸和田": "kishiwada",
    "玉野": "tamano",
    "広島": "hiroshima",
    "防府": "hofu",
    "高松": "takamatsu",
    "小松島": "komatsushima",
    "高知": "kochi",
    "松山": "matsuyama",
    "小倉": "kokura",
    "久留米": "kurume",
    "武雄": "takeo",
    "佐世保": "sasebo",
    "別府": "beppu",
    "熊本": "kumamoto",
}


# ============================================================
# 都道府県
# ============================================================

PREFECTURES = [
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
    "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
    "新潟", "富山", "石川", "福井", "山梨", "長野",
    "岐阜", "静岡", "愛知", "三重",
    "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山",
    "鳥取", "島根", "岡山", "広島", "山口",
    "徳島", "香川", "愛媛", "高知",
    "福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄"
]


# ============================================================
# HTML parser
# ============================================================

class SimpleHTMLParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.tables = []
        self.current_table = None
        self.current_row = None
        self.current_cell = None

    def handle_starttag(self, tag, attrs):

        if tag == "table":
            self.current_table = []

        elif tag == "tr" and self.current_table is not None:
            self.current_row = []

        elif tag in ("td", "th") and self.current_row is not None:
            self.current_cell = ""

    def handle_data(self, data):

        if self.current_cell is not None:
            self.current_cell += data

    def handle_endtag(self, tag):

        if tag in ("td", "th") and self.current_cell is not None:

            value = " ".join(
                unescape(self.current_cell).split()
            )

            self.current_row.append(value)
            self.current_cell = None

        elif tag == "tr" and self.current_row is not None:

            if self.current_table is not None:
                self.current_table.append(self.current_row)

            self.current_row = None

        elif tag == "table" and self.current_table is not None:

            self.tables.append(self.current_table)
            self.current_table = None


# ============================================================
# HTTP
# ============================================================

def fetch(url):

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Safari/605.1.15"
        )
    }

    for attempt in range(RETRIES + 1):

        try:

            req = Request(
                url,
                headers=headers
            )

            with urlopen(req, timeout=TIMEOUT) as response:

                return response.read().decode(
                    "utf-8",
                    errors="ignore"
                )

        except (HTTPError, URLError, TimeoutError):

            if attempt < RETRIES:
                time.sleep(0.5)

    return ""


# ============================================================
# 数字
# ============================================================

def to_int(value):

    if value is None:
        return None

    value = str(value)

    value = value.translate(
        str.maketrans(
            "０１２３４５６７８９",
            "0123456789"
        )
    )

    m = re.search(r"\d+", value)

    return int(m.group()) if m else None


def to_float(value):

    if value is None:
        return None

    value = str(value)

    value = value.translate(
        str.maketrans(
            "０１２３４５６７８９．",
            "0123456789."
        )
    )

    m = re.search(r"\d+(?:\.\d+)?", value)

    return float(m.group()) if m else None


# ============================================================
# 今日の開催場
# ============================================================

def find_today_venues(html):

    result = []

    for venue in VENUE_CODES:

        if venue in html and venue not in result:
            result.append(venue)

    return result


# ============================================================
# レース番号
# ============================================================

def find_race_numbers(html):

    result = set()

    patterns = [
        r"raceNo=(\d+)",
        r"第\s*(\d+)\s*レース",
        r"第\s*(\d+)\s*R",
    ]

    for pattern in patterns:

        for x in re.findall(pattern, html):
            result.add(int(x))

    if not result:
        result = set(range(1, 13))

    return sorted(result)


# ============================================================
# 選手名
# ============================================================

BAD_NAMES = {
    "◎", "○", "▲", "△", "×", "注",
    "本命", "対抗", "単穴", "連下", "穴"
}


def is_valid_rider_name(name):

    if not name:
        return False

    name = name.strip()

    if name in BAD_NAMES:
        return False

    if len(name) < 2:
        return False

    if any(x in name for x in ["◎", "○", "▲", "△", "×", "注"]):
        return False

    return bool(
        re.search(r"[一-龯ぁ-んァ-ヶ]", name)
    )


def extract_name_age_period(row):

    for i, cell in enumerate(row):

        m = re.search(
            r"(.+?)\s*(\d{1,2})歳\s*[／/]\s*(\d{2,3})期",
            cell
        )

        if m:

            name = m.group(1).strip()

            name = re.sub(
                r"[◎○▲△×注]",
                "",
                name
            ).strip()

            if is_valid_rider_name(name):

                return (
                    name,
                    int(m.group(2)),
                    int(m.group(3))
                )

    for i in range(len(row) - 1):

        if re.search(r"\d{1,2}歳", row[i + 1]):

            age = to_int(row[i + 1])

            if age:

                m = re.search(
                    r"(\d{2,3})期",
                    row[i + 1]
                )

                if m:

                    name = re.sub(
                        r"[◎○▲△×注]",
                        "",
                        row[i]
                    ).strip()

                    if is_valid_rider_name(name):

                        return (
                            name,
                            age,
                            int(m.group(1))
                        )

    return None, None, None


# ============================================================
# 着順
# ============================================================

def parse_finish(text):

    result = {
        "1": 0,
        "2": 0,
        "3": 0,
        "out": 0
    }

    m = re.search(
        r"着\s*順\s*[:：]?\s*([0-9]+)\s*[-－]\s*([0-9]+)\s*[-－]\s*([0-9]+)\s*[-－]\s*([0-9]+)",
        text
    )

    if m:

        result["1"] = int(m.group(1))
        result["2"] = int(m.group(2))
        result["3"] = int(m.group(3))
        result["out"] = int(m.group(4))

    return result


# ============================================================
# 決まり手
# ============================================================

def parse_kimari(text):

    result = {
        "nige": 0,
        "maki": 0,
        "sashi": 0,
        "mark": 0
    }

    m = re.search(
        r"決まり手\s*[:：]?\s*([0-9]+)\s*[-－]\s*([0-9]+)\s*[-－]\s*([0-9]+)\s*[-－]\s*([0-9]+)",
        text
    )

    if m:

        result["nige"] = int(m.group(1))
        result["maki"] = int(m.group(2))
        result["sashi"] = int(m.group(3))
        result["mark"] = int(m.group(4))

    return result


# ============================================================
# 直近成績
# ============================================================

def parse_recent_results(text):

    results = []

    text = text.translate(
        str.maketrans(
            "０１２３４５６７８９",
            "0123456789"
        )
    )

    pattern = re.compile(
        r"(\d{1,2})\s*/\s*(\d{1,2}).{0,60}?([1-9])着"
    )

    for m in pattern.finditer(text):

        results.append({
            "month": int(m.group(1)),
            "day": int(m.group(2)),
            "rank": int(m.group(3))
        })

    return results[:12]


# ============================================================
# レース基本情報
# ============================================================

def parse_race_info(html):

    text = " ".join(
        SimpleHTMLParser().feed(html) or []
    )

    m = re.search(
        r"第\s*(\d+)\s*レース.*?\(([^)]+)\)",
        text
    )

    if m:
        return m.group(2)

    return ""


# ============================================================
# 選手データ
# ============================================================

def parse_riders_from_race(html):

    parser = SimpleHTMLParser()
    parser.feed(html)

    riders = []

    for table in parser.tables:

        for row in table:

            if not row:
                continue

            joined = " ".join(row)

            if "競走得点" not in joined:
                continue

            car_no = None

            for cell in row:

                n = to_int(cell)

                if n is not None and 1 <= n <= 9:

                    car_no = n
                    break

            if car_no is None:
                continue

            name, age, period = extract_name_age_period(row)

            if not name:
                continue

            prefecture = ""

            for p in PREFECTURES:

                if p in joined:
                    prefecture = p
                    break

            style = ""

            for s in ["逃", "両", "追"]:
                if re.search(rf"\b{s}\b", joined):
                    style = s
                    break

            score_match = re.search(
                r"競走得点\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)",
                joined
            )

            score = (
                float(score_match.group(1))
                if score_match
                else 0
            )

            finish = parse_finish(joined)
            kimari = parse_kimari(joined)

            recent_results = parse_recent_results(joined)

            riders.append({

                "car_no": car_no,
                "name": name,
                "age": age,
                "period": period,
                "prefecture": prefecture,
                "style": style,
                "score": score,
                "finish": finish,
                "kimari": kimari,
                "recent_results": recent_results,

                # 予想エンジン用
                "comment": "",
                "line": [],
                "line_style": "",
                "role": "",
                "jump_probability": 0,
                "front_probability": 0,
                "attack_probability": 0,
            })

    # 車番重複除去
    unique = {}

    for rider in riders:

        if rider["car_no"] not in unique:
            unique[rider["car_no"]] = rider

    return sorted(
        unique.values(),
        key=lambda x: x["car_no"]
    )


# ============================================================
# コメント・ライン取得
# ============================================================

def build_prediction_url(venue, date_str):

    slug = VENUE_SLUGS.get(venue)

    if not slug:
        return ""

    year = date_str[:4]
    month_day = date_str[4:]

    return (
        f"{SP_BASE_URL}"
        f"{slug}/{year}/{month_day}.html"
    )


def normalize_comment(text):

    text = re.sub(
        r"\s+",
        "",
        text or ""
    )

    return text


def detect_comment_type(comment):

    comment = normalize_comment(comment)

    if not comment:
        return "不明"

    if re.search(r"自力自在|自力で自在|自在に自力", comment):
        return "自力自在"

    if re.search(r"自力", comment):
        return "自力"

    if re.search(r"前で|前々|前々に|前で頑張", comment):
        return "前"

    if re.search(r"自在", comment):
        return "自在"

    if re.search(r"番手|○○へ|[0-9]番手", comment):
        return "番手"

    if re.search(r"単騎", comment):
        return "単騎"

    if re.search(r"流れ見て|流れを見て", comment):
        return "流れ"

    return "その他"


def extract_comments_and_lines(html, riders):

    text = re.sub(
        r"<[^>]+>",
        " ",
        html
    )

    text = unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    # --------------------------------------------------------
    # 選手コメント
    # --------------------------------------------------------

    for rider in riders:

        name = rider["name"]

        pos = text.find(name)

        if pos < 0:
            continue

        area = text[pos:pos + 300]

        # 名前直後～次の選手名っぽいところまで
        comment = area

        comment = re.sub(
            r"^.*?" + re.escape(name),
            "",
            comment,
            count=1
        )

        comment = re.split(
            r"\d+\s+[^\s]{2,10}\s+\d{1,3}歳",
            comment
        )[0]

        comment = re.split(
            r"\b(?:逃捲|先捲|捲先|自在|追込|差捲|先行|叩先)\b",
            comment
        )[0]

        comment = comment[:180].strip()

        # ゴミを除去
        comment = re.sub(
            r"^[\s:：|｜]+",
            "",
            comment
        )

        if len(comment) > 3:
            rider["comment"] = comment
            rider["comment_type"] = detect_comment_type(comment)

    # --------------------------------------------------------
    # 並び情報
    # 例:
    # 1 4 6 / 2 7 / 5
    # --------------------------------------------------------

    line_patterns = [

        r"並び予想.{0,500}?((?:\d\s*){2,9})",

        r"予想の並び.{0,500}?((?:\d\s*){2,9})",

    ]

    lines = []

    # HTMLを簡易的に見て車番列を探す
    blocks = re.findall(
        r"(?:並び予想|予想の並び)(.{0,1200})",
        text
    )

    for block in blocks:

        nums = re.findall(
            r"(?<!\d)([1-9])(?!\d)",
            block
        )

        # 7車・9車など、実在車番だけ採用
        valid_nums = []

        existing = {
            r["car_no"]
            for r in riders
        }

        for n in nums:

            n = int(n)

            if n in existing and n not in valid_nums:
                valid_nums.append(n)

        if len(valid_nums) >= 2:

            lines.append(
                valid_nums
            )

            break

    # --------------------------------------------------------
    # 取れなかった場合
    # 同じ都道府県を弱いライン候補にする
    # ※確定ラインではない
    # --------------------------------------------------------

    if not lines:

        pref_groups = {}

        for rider in riders:

            pref = rider["prefecture"]

            if not pref:
                continue

            pref_groups.setdefault(
                pref,
                []
            ).append(
                rider["car_no"]
            )

        for group in pref_groups.values():

            if len(group) >= 2:
                lines.append(group)

    # riderに反映
    for rider in riders:

        rider["line"] = []

        for line in lines:

            if rider["car_no"] in line:

                rider["line"] = line

                if len(line) >= 2:

                    idx = line.index(
                        rider["car_no"]
                    )

                    if idx == 0:
                        rider["role"] = "先頭"
                    elif idx == 1:
                        rider["role"] = "番手"
                    else:
                        rider["role"] = "三番手以降"

                break

    return lines


# ============================================================
# 最近の調子
# ============================================================

def recent_form_score(rider):

    results = rider.get(
        "recent_results",
        []
    )

    if not results:
        return 0

    score = 0
    weight = 1.0

    for item in results[:8]:

        rank = item.get("rank")

        if rank == 1:
            score += 10 * weight
        elif rank == 2:
            score += 7 * weight
        elif rank == 3:
            score += 5 * weight
        elif rank == 4:
            score += 2 * weight
        elif rank:
            score -= 1 * weight

        weight *= 0.88

    return score


# ============================================================
# 飛びつき判定
# ============================================================

def calculate_jump_probability(rider):

    comment = normalize_comment(
        rider.get("comment", "")
    )

    comment_type = rider.get(
        "comment_type",
        "不明"
    )

    style = rider.get(
        "style",
        ""
    )

    kimari = rider.get(
        "kimari",
        {}
    )

    jump = 0.0

    # --------------------------------------------------------
    # コメント
    # --------------------------------------------------------

    if comment_type == "自在":
        jump += 28

    elif comment_type == "自力自在":
        jump += 24

    elif comment_type == "前":
        jump += 25

    elif comment_type == "流れ":
        jump += 15

    elif comment_type == "番手":
        jump += 4

    # --------------------------------------------------------
    # 明確な位置取り
    # --------------------------------------------------------

    if "前々" in comment:
        jump += 15

    if "前で" in comment:
        jump += 15

    if "位置" in comment:
        jump += 5

    if "好位" in comment:
        jump += 8

    if "切り替え" in comment:
        jump += 12

    if "飛びつ" in comment:
        jump += 30

    # --------------------------------------------------------
    # 登録脚質とのズレ
    # --------------------------------------------------------

    if style == "追" and comment_type in (
        "自在",
        "自力自在",
        "前"
    ):
        jump += 18

    if style == "両" and comment_type in (
        "自在",
        "自力自在",
        "前"
    ):
        jump += 10

    # --------------------------------------------------------
    # 過去の自在性
    # --------------------------------------------------------

    sashi = kimari.get("sashi", 0)
    mark = kimari.get("mark", 0)

    if sashi >= 3:
        jump += 5

    if mark >= 2:
        jump += 4

    return min(
        100,
        round(jump, 1)
    )


# ============================================================
# 自力・先行能力
# ============================================================

def calculate_front_probability(rider):

    style = rider.get(
        "style",
        ""
    )

    comment_type = rider.get(
        "comment_type",
        "不明"
    )

    kimari = rider.get(
        "kimari",
        {}
    )

    value = 0

    if style == "逃":
        value += 55

    elif style == "両":
        value += 30

    elif style == "追":
        value += 5

    if comment_type == "自力":
        value += 25

    elif comment_type == "自力自在":
        value += 18

    elif comment_type == "前":
        value += 15

    if kimari.get("nige", 0) > 0:
        value += min(
            15,
            kimari["nige"] * 3
        )

    return min(
        100,
        value
    )


# ============================================================
# 攻撃力
# ============================================================

def calculate_attack_probability(rider):

    style = rider.get(
        "style",
        ""
    )

    comment_type = rider.get(
        "comment_type",
        "不明"
    )

    kimari = rider.get(
        "kimari",
        {}
    )

    value = 0

    if style == "逃":
        value += 35

    elif style == "両":
        value += 30

    elif style == "追":
        value += 15

    if comment_type == "自力":
        value += 30

    elif comment_type == "自力自在":
        value += 27

    elif comment_type == "自在":
        value += 22

    if kimari.get("maki", 0):
        value += min(
            20,
            kimari["maki"] * 4
        )

    if kimari.get("nige", 0):
        value += min(
            15,
            kimari["nige"] * 2
        )

    return min(
        100,
        value
    )


# ============================================================
# 選手基本評価
# ============================================================

def base_rider_score(rider, riders):

    scores = [
        r.get("score", 0)
        for r in riders
        if r.get("score", 0) > 0
    ]

    if not scores:
        relative = 0
    else:

        mn = min(scores)
        mx = max(scores)

        if mx == mn:
            relative = 25
        else:
            relative = (
                (rider["score"] - mn)
                / (mx - mn)
            ) * 35

    finish = rider.get(
        "finish",
        {}
    )

    total = (
        finish.get("1", 0)
        + finish.get("2", 0)
        + finish.get("3", 0)
        + finish.get("out", 0)
    )

    if total:

        win = finish.get("1", 0) / total
        top2 = (
            finish.get("1", 0)
            + finish.get("2", 0)
        ) / total
        top3 = (
            finish.get("1", 0)
            + finish.get("2", 0)
            + finish.get("3", 0)
        ) / total

        form_score = (
            win * 30
            + top2 * 15
            + top3 * 10
        )

    else:
        form_score = 0

    recent = recent_form_score(
        rider
    )

    kimari = rider.get(
        "kimari",
        {}
    )

    tactical = (
        kimari.get("nige", 0) * 1.2
        + kimari.get("maki", 0) * 1.5
        + kimari.get("sashi", 0) * 1.3
        + kimari.get("mark", 0) * 0.8
    )

    return (
        relative
        + form_score
        + min(15, recent)
        + min(12, tactical)
    )


# ============================================================
# 展開タイプ
# ============================================================

def determine_race_pattern(riders):

    front = sum(
        1
        for r in riders
        if r.get("front_probability", 0) >= 55
    )

    jumpers = sum(
        1
        for r in riders
        if r.get("jump_probability", 0) >= 40
    )

    attackers = sum(
        1
        for r in riders
        if r.get("attack_probability", 0) >= 55
    )

    if front <= 1:

        pattern = "先行有利"

    elif front == 2:

        if jumpers >= 1:
            pattern = "先行争い＋飛びつき警戒"
        else:
            pattern = "先行争い→番手差し・捲り"

    else:

        if jumpers >= 2:
            pattern = "激しい位置取り・ライン崩れ警戒"
        else:
            pattern = "先行争い激化→捲り有利"

    if attackers >= 3:
        pattern += "・捲り合戦"

    return pattern


# ============================================================
# 展開補正
# ============================================================

def apply_race_dynamics(riders):

    pattern = determine_race_pattern(
        riders
    )

    for rider in riders:

        score = rider["_base_ai"]

        front = rider[
            "front_probability"
        ]

        attack = rider[
            "attack_probability"
        ]

        jump = rider[
            "jump_probability"
        ]

        role = rider.get(
            "role",
            ""
        )

        style = rider.get(
            "style",
            ""
        )

        # ----------------------------------------------------
        # 先行少ない
        # ----------------------------------------------------

        if "先行有利" in pattern:

            if front >= 60:
                score += 7

            if role == "番手":
                score += 6

        # ----------------------------------------------------
        # 先行争い
        # ----------------------------------------------------

        if "先行争い" in pattern:

            if attack >= 60:
                score += 5

            if role == "番手":
                score += 8

            if jump >= 50:
                score += 4

        # ----------------------------------------------------
        # 飛びつき
        # ----------------------------------------------------

        if "飛びつき" in pattern:

            if jump >= 45:
                score += 9

            if role == "番手":
                score -= 3

        # ----------------------------------------------------
        # ライン崩れ
        # ----------------------------------------------------

        if "ライン崩れ" in pattern:

            if jump >= 55:
                score += 8

            if attack >= 60:
                score += 7

            if role == "番手" and jump < 30:
                score -= 4

        # ----------------------------------------------------
        # 捲り
        # ----------------------------------------------------

        if "捲り" in pattern:

            if attack >= 65:
                score += 6

            if style in ("両", "逃"):
                score += 3

        rider["_dynamic_ai"] = score

    return pattern


# ============================================================
# 最終AI評価
# ============================================================

def calculate_final_predictions(riders):

    if not riders:
        return "", []

    # 基本値
    for rider in riders:

        rider["jump_probability"] = (
            calculate_jump_probability(
                rider
            )
        )

        rider["front_probability"] = (
            calculate_front_probability(
                rider
            )
        )

        rider["attack_probability"] = (
            calculate_attack_probability(
                rider
            )
        )

        rider["_base_ai"] = (
            base_rider_score(
                rider,
                riders
            )
        )

    # 展開
    pattern = apply_race_dynamics(
        riders
    )

    # 車番による補正はかなり弱くする
    for rider in riders:

        score = rider["_dynamic_ai"]

        car = rider["car_no"]

        if car == 1:
            score += 1.5

        elif car == 2:
            score += 0.8

        elif car >= 7:
            score -= 0.5

        # 飛びつき自体をプラス評価
        # ただし「飛びつけば必ず好走」ではない
        if rider["jump_probability"] >= 60:
            score += 3

        rider["ai_score"] = round(
            score,
            2
        )

    # 順位
    riders.sort(
        key=lambda x: x["ai_score"],
        reverse=True
    )

    labels = [
        "本命",
        "対抗",
        "単穴",
        "連下"
    ]

    for i, rider in enumerate(riders):

        if i < len(labels):
            rider["ai_rank"] = i + 1
            rider["ai_label"] = labels[i]
        else:
            rider["ai_rank"] = i + 1
            rider["ai_label"] = "穴"

    # 信頼度
    if len(riders) >= 2:

        diff = (
            riders[0]["ai_score"]
            - riders[1]["ai_score"]
        )

    else:
        diff = 0

    if diff >= 12:
        confidence = 5
    elif diff >= 8:
        confidence = 4
    elif diff >= 5:
        confidence = 3
    elif diff >= 2:
        confidence = 2
    else:
        confidence = 1

    # --------------------------------------------------------
    # 穴候補
    # --------------------------------------------------------

    value_candidates = []

    for rider in riders:

        if rider["ai_rank"] >= 4:

            value = (
                rider["jump_probability"] * 0.30
                + rider["attack_probability"] * 0.25
                + rider["front_probability"] * 0.15
                + rider["ai_score"] * 0.30
            )

            rider["_value_score"] = round(
                value,
                2
            )

            value_candidates.append(
                rider
            )

    value_candidates.sort(
        key=lambda x: x["_value_score"],
        reverse=True
    )

    hole = (
        value_candidates[0]["car_no"]
        if value_candidates
        else None
    )

    # --------------------------------------------------------
    # 買い目候補
    # --------------------------------------------------------

    top = riders[:4]

    exacta_candidates = []

    if len(top) >= 2:

        # 本命→対抗
        exacta_candidates.append([
            top[0]["car_no"],
            top[1]["car_no"]
        ])

        # 本命→単穴
        exacta_candidates.append([
            top[0]["car_no"],
            top[2]["car_no"]
        ])

        # 穴絡み
        if hole and hole != top[0]["car_no"]:

            exacta_candidates.append([
                top[0]["car_no"],
                hole
            ])

    trifecta_candidates = []

    if len(top) >= 3:

        trifecta_candidates.append([
            top[0]["car_no"],
            top[1]["car_no"],
            top[2]["car_no"]
        ])

        if hole:

            trifecta_candidates.append([
                top[0]["car_no"],
                top[1]["car_no"],
                hole
            ])

    # --------------------------------------------------------
    # 表示用展開コメント
    # --------------------------------------------------------

    if "飛びつき" in pattern:

        development = (
            f"{pattern}。"
            "コメントから位置取りを変える選手に注意。"
            "番手固定とは決めつけず、飛びつき後のライン崩れまで評価。"
        )

    elif "捲り" in pattern:

        development = (
            f"{pattern}。"
            "先行争いが激しくなれば、後方からの捲り・差しを重視。"
        )

    else:

        development = (
            f"{pattern}。"
            "先行勢と番手の連係を基本線として評価。"
        )

    # --------------------------------------------------------
    # 不要な内部データ削除
    # --------------------------------------------------------

    for rider in riders:

        rider.pop("_base_ai", None)
        rider.pop("_dynamic_ai", None)
        rider.pop("_value_score", None)

    race_prediction = {

        "pattern": pattern,

        "development": development,

        "confidence": confidence,

        "confidence_text": (
            "かなり強い"
            if confidence == 5
            else "強め"
            if confidence == 4
            else "標準"
            if confidence == 3
            else "混戦"
            if confidence == 2
            else "超混戦"
        ),

        "main": top[0]["car_no"]
            if len(top) >= 1 else None,

        "opponent": top[1]["car_no"]
            if len(top) >= 2 else None,

        "third": top[2]["car_no"]
            if len(top) >= 3 else None,

        "hole": hole,

        "exacta": exacta_candidates,

        "trifecta": trifecta_candidates,
    }

    return race_prediction


# ============================================================
# レース取得
# ============================================================

def fetch_race(
    venue,
    venue_code,
    date_str,
    race_no
):

    url = (
        f"{BASE_URL}"
        f"RaceList.do?"
        f"joCode={venue_code}"
        f"&kaisaiBi={date_str}"
        f"&raceNo={race_no}"
    )

    html = fetch(url)

    if not html:

        return {
            "venue": venue,
            "venue_code": venue_code,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "status": "failed"
        }

    riders = parse_riders_from_race(
        html
    )

    return {
        "venue": venue,
        "venue_code": venue_code,
        "race_no": race_no,
        "url": url,
        "riders": riders,
        "status": (
            "ok"
            if riders
            else "no_riders"
        )
    }


# ============================================================
# 開催場のレース一覧
# ============================================================

def get_venue_jobs(
    venue,
    venue_code,
    date_str
):

    url = (
        f"{BASE_URL}"
        f"AllRaceList.do?"
        f"joCode={venue_code}"
        f"&kaisaiBi={date_str}"
    )

    html = fetch(url)

    if not html:
        return []

    race_numbers = find_race_numbers(
        html
    )

    return [
        (
            venue,
            venue_code,
            date_str,
            race_no
        )
        for race_no in race_numbers
    ]


# ============================================================
# コメント取得
# ============================================================

def fetch_prediction_comments(
    venue,
    date_str
):

    url = build_prediction_url(
        venue,
        date_str
    )

    if not url:
        return {}

    html = fetch(url)

    if not html:
        return {}

    result = {}

    # Rごとのブロックを抽出
    blocks = re.split(
        r"(?=\b(?:1|2|3|4|5|6|7|8|9|10|11|12)R\b)",
        re.sub(r"\s+", " ", html)
    )

    for block in blocks:

        m = re.match(
            r"\s*(\d{1,2})R\b",
            block
        )

        if not m:
            continue

        race_no = int(
            m.group(1)
        )

        comments = {}

        # 車番＋選手名＋コメント
        for match in re.finditer(
            r"(?<!\d)([1-9])\s+([^|]{2,20})\s+([^|]{2,100})",
            block
        ):

            car_no = int(
                match.group(1)
            )

            name = match.group(2).strip()
            comment = match.group(3).strip()

            if len(comment) > 3:

                comments[car_no] = {
                    "name": name,
                    "comment": comment
                }

        # 車番だけでも拾う
        if comments:
            result[race_no] = comments

    return result


# ============================================================
# コメント反映
# ============================================================

def merge_comments(
    race,
    comment_data
):

    race_no = race["race_no"]

    comments = comment_data.get(
        race_no,
        {}
    )

    for rider in race["riders"]:

        car = rider["car_no"]

        item = comments.get(
            car
        )

        if not item:
            continue

        comment = item.get(
            "comment",
            ""
        )

        if comment:

            rider["comment"] = (
                comment
            )

            rider["comment_type"] = (
                detect_comment_type(
                    comment
                )
            )

    return race


# ============================================================
# AI処理
# ============================================================

def run_ai(race):

    riders = race.get(
        "riders",
        []
    )

    if not riders:
        return race

    prediction = (
        calculate_final_predictions(
            riders
        )
    )

    race["prediction"] = prediction

    race["riders"] = riders

    return race


# ============================================================
# メイン
# ============================================================

def main():

    start = time.time()

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
    print(" KEIRIN AI DATA UPDATE v4.0")
    print("==============================")
    print(
        f"対象日: {display_date}"
    )
    print("==============================")

    # --------------------------------------------------------
    # 開催場取得
    # --------------------------------------------------------

    race_list_url = (
        f"{BASE_URL}"
        f"RaceListInfo.do?"
        f"kaisaiBi={date_str}"
    )

    html = fetch(
        race_list_url
    )

    venues = find_today_venues(
        html
    )

    print(
        f"開催場数: {len(venues)}"
    )

    # --------------------------------------------------------
    # レース一覧
    # --------------------------------------------------------

    jobs = []

    for venue in venues:

        code = VENUE_CODES[venue]

        venue_jobs = get_venue_jobs(
            venue,
            code,
            date_str
        )

        jobs.extend(
            venue_jobs
        )

    print(
        f"詳細取得対象レース: {len(jobs)}"
    )

    # --------------------------------------------------------
    # レース並列取得
    # --------------------------------------------------------

    races = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                fetch_race,
                *job
            )
            for job in jobs
        ]

        for future in as_completed(
            futures
        ):

            try:

                race = future.result()

                races.append(
                    race
                )

            except Exception as e:

                print(
                    f"レース取得エラー: {e}"
                )

    # --------------------------------------------------------
    # 並び替え
    # --------------------------------------------------------

    venue_order = {
        venue: i
        for i, venue
        in enumerate(venues)
    }

    races.sort(
        key=lambda x: (
            venue_order.get(
                x["venue"],
                999
            ),
            x["race_no"]
        )
    )

    # --------------------------------------------------------
    # コメント取得
    # --------------------------------------------------------

    print(
        "コメント・予想情報取得中..."
    )

    comment_map = {}

    with ThreadPoolExecutor(
        max_workers=6
    ) as executor:

        futures = {
            executor.submit(
                fetch_prediction_comments,
                venue,
                date_str
            ): venue
            for venue in venues
        }

        for future in as_completed(
            futures
        ):

            venue = futures[future]

            try:

                comment_map[venue] = (
                    future.result()
                )

            except Exception:

                comment_map[venue] = {}

    # --------------------------------------------------------
    # AI評価
    # --------------------------------------------------------

    ai_races = 0
    ai_riders = 0
    failed = 0

    for race in races:

        venue = race["venue"]

        # コメント
        race = merge_comments(
            race,
            comment_map.get(
                venue,
                {}
            )
        )

        # ライン推定
        lines = extract_comments_and_lines(
            "",
            race["riders"]
        )

        race["lines"] = lines

        # 飛びつき等を再計算
        for rider in race["riders"]:

            rider["comment_type"] = (
                detect_comment_type(
                    rider.get(
                        "comment",
                        ""
                    )
                )
            )

        # AI
        race = run_ai(
            race
        )

        if race.get("prediction"):
            ai_races += 1
            ai_riders += len(
                race["riders"]
            )

        if race["status"] != "ok":
            failed += 1

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    rider_count = sum(
        len(r["riders"])
        for r in races
    )

    valid_name_count = sum(
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
    # 保存
    # --------------------------------------------------------

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    data = {

        "version": "4.0",

        "updated_at": datetime.now(
            JST
        ).isoformat(),

        "date": display_date,

        "venue_count": len(
            venues
        ),

        "race_count": len(
            races
        ),

        "rider_count": rider_count,

        "valid_name_count":
            valid_name_count,

        "ai_race_count":
            ai_races,

        "ai_rider_count":
            ai_riders,

        "failed_race_count":
            failed,

        "venues": venues,

        "races": races
    }

    OUTPUT.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    elapsed = (
        time.time() - start
    )

    print()
    print("==============================")
    print(" 更新完了")
    print("==============================")
    print(
        f"開催場: {len(venues)}"
    )
    print(
        f"レース: {len(races)}"
    )
    print(
        f"選手: {rider_count}"
    )
    print(
        f"正しい選手名: {valid_name_count}"
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
        f"保存先: {OUTPUT}"
    )
    print("==============================")


if __name__ == "__main__":
    main()
