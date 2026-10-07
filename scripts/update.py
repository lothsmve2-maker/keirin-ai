import os
import re
import json
import time
import html
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen


# ============================================================
# KEIRIN AI DATA UPDATE v4.2
# ============================================================

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).strftime("%Y%m%d")

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

OUT_FILE = "data/today.json"

# 取得速度と安定性のバランス
MAX_WORKERS = 10
TIMEOUT = 20


# ============================================================
# TODAY'S VENUES
#
# 2026/10/07 confirmed:
# 前橋 22
# 大宮 25
# 静岡 38
# 大垣 44
# 四日市 48
# 奈良 53
# 高知 74
#
# ただし毎日の開催変更に対応するため、
# RaceListInfo / AIページから自動検出も行う。
# ============================================================

KNOWN_VENUES = {
    "22": "前橋",
    "25": "大宮",
    "38": "静岡",
    "44": "大垣",
    "48": "四日市",
    "53": "奈良",
    "74": "高知",
}


VENUE_SLUGS = {
    "前橋": "maebashi",
    "大宮": "omiya",
    "静岡": "shizuoka",
    "大垣": "ogaki",
    "四日市": "yokkaichi",
    "奈良": "nara",
    "高知": "kochi",
    "松阪": "matsusaka",
    "富山": "toyama",
    "小倉": "kokura",
    "熊本": "kumamoto",
    "防府": "hofu",
    "高松": "takamatsu",
    "玉野": "tamano",
    "福井": "fukui",
    "岐阜": "gifu",
    "久留米": "kurume",
    "佐世保": "sasebo",
    "別府": "beppu",
}


# ============================================================
# HTTP
# ============================================================

def fetch(url, timeout=TIMEOUT):
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Safari/604.1"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "ja-JP,ja;q=0.9",
            "Connection": "close",
        },
    )

    for attempt in range(3):
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()

            # OddsParkはページによって文字コードが違う可能性がある
            for enc in ("utf-8", "cp932", "shift_jis"):
                try:
                    return raw.decode(enc, errors="ignore")
                except Exception:
                    pass

        except Exception:
            if attempt < 2:
                time.sleep(0.8)

    return ""


# ============================================================
# HTML TEXT
# ============================================================

class TextParser:
    """
    HTMLタグを除去して、画面上の文字列を取り出す。
    """

    def __init__(self):
        self.parts = []

    def feed(self, source):
        source = re.sub(
            r"<script\b[^>]*>.*?</script>",
            " ",
            source,
            flags=re.I | re.S,
        )

        source = re.sub(
            r"<style\b[^>]*>.*?</style>",
            " ",
            source,
            flags=re.I | re.S,
        )

        source = re.sub(
            r"<br\s*/?>",
            "\n",
            source,
            flags=re.I,
        )

        source = re.sub(
            r"</(div|p|tr|li|h1|h2|h3|h4|section|article)>",
            "\n",
            source,
            flags=re.I,
        )

        source = re.sub(
            r"<[^>]+>",
            " ",
            source,
        )

        source = html.unescape(source)

        source = source.replace("\r", "\n")

        lines = []

        for line in source.split("\n"):
            line = re.sub(r"\s+", " ", line).strip()

            if line:
                lines.append(line)

        self.parts = lines

    def text(self):
        return "\n".join(self.parts)


def html_text(source):
    parser = TextParser()
    parser.feed(source)
    return parser.text()


# ============================================================
# UTILS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def valid_name(name):
    name = clean_text(name)

    if len(name) < 2:
        return False

    if any(
        x in name
        for x in ["◎", "○", "▲", "△", "×", "注"]
    ):
        return False

    if not re.search(
        r"[一-龥ぁ-んァ-ヶ]",
        name,
    ):
        return False

    return True


def first_number(text):
    m = re.search(r"\d+", text or "")

    if not m:
        return None

    try:
        return int(m.group())
    except Exception:
        return None


def number_float(text):
    m = re.search(
        r"\d+(?:\.\d+)?",
        text or "",
    )

    if not m:
        return 0.0

    try:
        return float(m.group())
    except Exception:
        return 0.0


# ============================================================
# VENUE DETECTION
# ============================================================

def get_venues():
    """
    当日の開催場を取得。

    まずRaceListInfoから取得。
    取得できない場合はKNOWN_VENUESを利用。
    """

    url = (
        f"{BASE_URL}/keirin/RaceListInfo.do"
        f"?kaisaiBi={TODAY}"
    )

    source = fetch(url)

    found = {}

    if source:

        # joCode=25 のようなリンクから取得
        for m in re.finditer(
            r"joCode=(\d{1,3})",
            source,
            flags=re.I,
        ):
            code = m.group(1).zfill(2)

            if code in KNOWN_VENUES:
                found[code] = KNOWN_VENUES[code]

        # 既知コードが直接見えない場合もあるので、
        # 今日の予想ページの一覧から補完
        for code, name in KNOWN_VENUES.items():

            if (
                f"joCode={int(code)}" in source
                or f"joCode={code}" in source
            ):
                found[code] = name

    # 今日の既知開催場を安全策として補完
    for code, name in KNOWN_VENUES.items():
        if code not in found:
            found[code] = name

    venues = [
        {
            "jo_code": code,
            "name": found[code],
        }
        for code in found
    ]

    venues.sort(
        key=lambda x: int(x["jo_code"])
    )

    return venues


# ============================================================
# RACE NUMBERS
# ============================================================

def get_race_numbers(venue):
    jo_code = venue["jo_code"]

    url = (
        f"{BASE_URL}/keirin/AllRaceList.do"
        f"?joCode={int(jo_code)}"
        f"&kaisaiBi={TODAY}"
    )

    source = fetch(url)

    numbers = set()

    if source:

        for m in re.finditer(
            r"raceNo=(\d{1,2})",
            source,
            flags=re.I,
        ):
            n = int(m.group(1))

            if 1 <= n <= 12:
                numbers.add(n)

        if not numbers:

            for m in re.finditer(
                r"(\d{1,2})R",
                source,
            ):
                n = int(m.group(1))

                if 1 <= n <= 12:
                    numbers.add(n)

    return sorted(numbers)


# ============================================================
# PREFECTURE
# ============================================================

PREFECTURES = [
    "北海道",
    "青森",
    "岩手",
    "宮城",
    "秋田",
    "山形",
    "福島",
    "茨城",
    "栃木",
    "群馬",
    "埼玉",
    "千葉",
    "東京",
    "神奈川",
    "新潟",
    "富山",
    "石川",
    "福井",
    "山梨",
    "長野",
    "岐阜",
    "静岡",
    "愛知",
    "三重",
    "滋賀",
    "京都",
    "大阪",
    "兵庫",
    "奈良",
    "和歌山",
    "和歌",
    "鳥取",
    "島根",
    "岡山",
    "広島",
    "山口",
    "徳島",
    "香川",
    "愛媛",
    "高知",
    "福岡",
    "佐賀",
    "長崎",
    "熊本",
    "大分",
    "宮崎",
    "鹿児島",
    "沖縄",
]


def normalize_prefecture(value):
    value = clean_text(value)

    if value == "和歌":
        return "和歌山"

    return value


# ============================================================
# RACE TABLE PARSER
# ============================================================

def extract_riders_from_race(source):
    """
    RaceList.doから選手情報を抽出。

    テーブル構造に依存しすぎないよう、
    7車の名前・年齢・期・府県・脚質・得点・成績を
    周辺テキストから拾う。
    """

    text = html_text(source)

    lines = text.splitlines()

    riders = []

    # --------------------------------------------------------
    # まず「車番 名前」を探す
    # --------------------------------------------------------

    for i, line in enumerate(lines):

        m = re.match(
            r"^\s*([1-9])\s+(.+)$",
            line,
        )

        if not m:
            continue

        car_no = int(m.group(1))
        candidate = clean_text(m.group(2))

        if not valid_name(candidate):
            continue

        # 印などを除外
        if candidate in [
            "◎",
            "○",
            "▲",
            "△",
            "×",
            "注",
        ]:
            continue

        # 年齢・期別の確認
        window = " ".join(
            lines[
                i:min(
                    len(lines),
                    i + 8,
                )
            ]
        )

        age_match = re.search(
            r"(\d{2})歳",
            window,
        )

        period_match = re.search(
            r"(\d{2,3})期",
            window,
        )

        if not age_match or not period_match:
            continue

        age = int(age_match.group(1))
        period = int(period_match.group(1))

        # ----------------------------------------------------
        # 府県
        # ----------------------------------------------------

        prefecture = ""

        for p in PREFECTURES:

            if re.search(
                rf"\b{re.escape(p)}\b",
                window,
            ):
                prefecture = normalize_prefecture(p)
                break

        # 日本語HTMLでは \b が効かない場合がある
        if not prefecture:

            for p in PREFECTURES:

                if p in window:
                    prefecture = normalize_prefecture(p)
                    break

        # ----------------------------------------------------
        # 脚質
        # ----------------------------------------------------

        style = ""

        if re.search(r"\b逃\b", window):
            style = "逃"

        elif re.search(r"\b追\b", window):
            style = "追"

        elif re.search(r"\b両\b", window):
            style = "両"

        else:

            if "逃" in window:
                style = "逃"

            elif "追" in window:
                style = "追"

            elif "両" in window:
                style = "両"

        # ----------------------------------------------------
        # 競走得点
        # ----------------------------------------------------

        score = 0.0

        score_match = re.search(
            r"競走得点[:：]?\s*(\d+(?:\.\d+)?)",
            window,
        )

        if score_match:
            score = float(
                score_match.group(1)
            )

        # ----------------------------------------------------
        # 着順1-2-3-外
        # ----------------------------------------------------

        finish = {
            "1": 0,
            "2": 0,
            "3": 0,
            "out": 0,
        }

        finish_match = re.search(
            r"着順\s*[:：]?\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)",
            window,
        )

        if finish_match:

            finish = {
                "1": int(finish_match.group(1)),
                "2": int(finish_match.group(2)),
                "3": int(finish_match.group(3)),
                "out": int(finish_match.group(4)),
            }

        # ----------------------------------------------------
        # 決まり手
        # ----------------------------------------------------

        kimari = {
            "nige": 0,
            "maki": 0,
            "sashi": 0,
            "mark": 0,
        }

        kimari_match = re.search(
            r"決まり手\s*[:：]?\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)\s*[-－]\s*"
            r"(\d+)",
            window,
        )

        if kimari_match:

            kimari = {
                "nige": int(kimari_match.group(1)),
                "maki": int(kimari_match.group(2)),
                "sashi": int(kimari_match.group(3)),
                "mark": int(kimari_match.group(4)),
            }

        # ----------------------------------------------------
        # 直近結果
        # ----------------------------------------------------

        recent_results = []

        for rm in re.finditer(
            r"(\d{1,2})/\s*(\d{1,2}).{0,30}?"
            r"([1-9]|10)着",
            window,
        ):

            recent_results.append(
                {
                    "month": int(rm.group(1)),
                    "day": int(rm.group(2)),
                    "rank": int(rm.group(3)),
                }
            )

        rider = {
            "car_no": car_no,
            "name": candidate,
            "age": age,
            "period": period,
            "prefecture": prefecture,
            "style": style,
            "score": score,
            "finish": finish,
            "kimari": kimari,
            "recent_results": recent_results[:10],
        }

        # 重複防止
        if not any(
            r["car_no"] == car_no
            for r in riders
        ):
            riders.append(rider)

    riders.sort(
        key=lambda x: x["car_no"]
    )

    return riders


# ============================================================
# VENUE BIAS
# ============================================================

def extract_venue_bias(source):
    text = html_text(source)

    result = {
        "nige": 0.0,
        "maki": 0.0,
        "sashi": 0.0,
    }

    patterns = {
        "nige": r"逃げ\s*[:：]?\s*(\d+(?:\.\d+)?)%",
        "maki": r"捲り\s*[:：]?\s*(\d+(?:\.\d+)?)%",
        "sashi": r"差し\s*[:：]?\s*(\d+(?:\.\d+)?)%",
    }

    for key, pattern in patterns.items():

        m = re.search(
            pattern,
            text,
        )

        if m:
            result[key] = float(
                m.group(1)
            )

    return result


# ============================================================
# RACE FETCH
# ============================================================

def fetch_race(venue, race_no):

    jo_code = venue["jo_code"]

    url = (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={int(jo_code)}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )

    source = fetch(url)

    if not source:
        return None

    riders = extract_riders_from_race(
        source
    )

    if not riders:
        return None

    return {
        "venue": venue["name"],
        "jo_code": jo_code,
        "race_no": race_no,
        "venue_bias": extract_venue_bias(
            source
        ),
        "riders": riders,
    }


# ============================================================
# PREDICTION PAGE
# ============================================================

def prediction_url(venue_name):

    slug = VENUE_SLUGS.get(
        venue_name
    )

    if not slug:
        return ""

    return (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/{TODAY[4:]}.html"
    )


def split_prediction_races(source):
    """
    予想ページを1R〜12Rに分割。

    実際のOddsPark予想ページは
    「1R」「2R」...という見出しで
    1ページ内に複数レースを掲載している。
    """

    text = html_text(source)

    lines = text.splitlines()

    sections = {}

    current = None
    buffer = []

    for line in lines:

        line = clean_text(line)

        m = re.match(
            r"^([1-9]|1[0-2])R$",
            line,
        )

        if m:

            if current is not None:
                sections[current] = "\n".join(
                    buffer
                )

            current = int(
                m.group(1)
            )

            buffer = []

        elif current is not None:

            buffer.append(line)

    if current is not None:
        sections[current] = "\n".join(
            buffer
        )

    return sections


# ============================================================
# COMMENT PARSER
# ============================================================

def parse_comments(section):

    comments = {}

    lines = section.splitlines()

    # コメント表の開始
    comment_start = None

    for i, line in enumerate(lines):

        if (
            "コメント" in line
            and (
                "選手名" in line
                or "車番" in line
                or "枠番" in line
            )
        ):
            comment_start = i
            break

    if comment_start is not None:

        # 表形式の場合
        for line in lines[
            comment_start + 1:
        ]:

            m = re.match(
                r"^\s*(\d+)\s+"
                r"(\d+)?\s*"
                r"(.+?)\s+"
                r"(.+)$",
                line,
            )

            if not m:
                continue

            car = int(m.group(1))

            if not 1 <= car <= 9:
                continue

            name = clean_text(
                m.group(3)
            )

            comment = clean_text(
                m.group(4)
            )

            if valid_name(name):

                comments[car] = {
                    "name": name,
                    "comment": comment,
                }

    # --------------------------------------------------------
    # フォールバック
    # --------------------------------------------------------

    if len(comments) < 2:

        # 「1 名前 コメント」
        pattern = re.compile(
            r"^\s*([1-9])\s+"
            r"(?:◎|○|▲|△|×|注|…)?\s*"
            r"([一-龥ぁ-んァ-ヶー・]{2,20})"
            r"\s+(.+)$"
        )

        for line in lines:

            m = pattern.match(line)

            if not m:
                continue

            car = int(m.group(1))
            name = clean_text(
                m.group(2)
            )
            comment = clean_text(
                m.group(3)
            )

            if (
                valid_name(name)
                and comment
            ):
                comments[car] = {
                    "name": name,
                    "comment": comment,
                }

    return comments


# ============================================================
# LINE PARSER
# ============================================================

STYLE_PATTERNS = [
    "逃捲",
    "追捲",
    "自在",
    "差脚",
    "差捲",
    "先捲",
    "自力",
    "追込",
    "前々",
    "何か",
    "単騎",
    "先行",
    "捲り",
]


def parse_lines(section):

    lines = section.splitlines()

    result = []

    # --------------------------------------------------------
    # まず数字行を探す
    # --------------------------------------------------------

    candidate_groups = []

    for i, line in enumerate(lines):

        nums = [
            int(x)
            for x in re.findall(
                r"\b([1-9])\b",
                line,
            )
        ]

        if len(nums) < 2:
            continue

        if len(nums) > 7:
            continue

        # 車番だけで構成された行
        cleaned = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if not re.fullmatch(
            r"[1-9](?:\s+[1-9])+",
            cleaned,
        ):
            continue

        # 直後の行に脚質があるか確認
        styles = []

        if i + 1 < len(lines):

            next_line = lines[i + 1]

            for style in STYLE_PATTERNS:

                if style in next_line:
                    styles.append(
                        style
                    )

        if styles:
            candidate_groups.append(
                nums
            )

    # --------------------------------------------------------
    # 典型的なOddsPark構造では、
    # 1 4 / 5 2 7 / 3 6
    # のように複数グループ。
    # --------------------------------------------------------

    used = set()

    for group in candidate_groups:

        if len(group) < 2:
            continue

        if len(set(group)) != len(group):
            continue

        # 既に全部使っている場合は除外
        if all(
            n in used
            for n in group
        ):
            continue

        result.append(group)

        for n in group:
            used.add(n)

    # --------------------------------------------------------
    # フォールバック:
    # 「並び予想」付近の車番を拾う
    # --------------------------------------------------------

    if not result:

        start = None

        for i, line in enumerate(lines):

            if (
                "並び予想" in line
                or "予想の並び" in line
            ):
                start = i
                break

        if start is not None:

            nums = []

            for line in lines[
                start:start + 10
            ]:

                for n in re.findall(
                    r"\b([1-9])\b",
                    line,
                ):

                    n = int(n)

                    if n not in nums:
                        nums.append(n)

            # 7車なら全車番が揃っている
            if len(nums) == 7:

                # 予想ページの一般的な構造から
                # 3-2-2 / 2-3-2などを候補化
                # ただし無理な推測はしない。
                result = [nums]

    return result


# ============================================================
# RACE COMMENT
# ============================================================

def parse_race_comment(section):

    lines = section.splitlines()

    candidates = []

    keywords = [
        "本命",
        "中心",
        "軸",
        "上位",
        "期待",
        "仕掛け",
        "展開",
        "捲り",
        "逃げ",
        "一撃",
        "警戒",
        "有力",
        "勝負",
        "勝利",
        "狙う",
    ]

    for line in lines:

        line = clean_text(line)

        if len(line) < 20:
            continue

        if len(line) > 180:
            continue

        if "予想の並び" in line:
            continue

        if "掲載されている情報" in line:
            continue

        if any(
            k in line
            for k in keywords
        ):
            candidates.append(line)

    if candidates:
        return candidates[-1]

    return ""


# ============================================================
# GET ALL PREDICTION DATA FOR VENUE
# ============================================================

def fetch_prediction_venue(venue):

    url = prediction_url(
        venue["name"]
    )

    if not url:
        return {
            "venue": venue["name"],
            "races": {},
        }

    source = fetch(url)

    if not source:
        return {
            "venue": venue["name"],
            "races": {},
        }

    sections = split_prediction_races(
        source
    )

    result = {}

    for race_no, section in sections.items():

        comments = parse_comments(
            section
        )

        lines = parse_lines(
            section
        )

        race_comment = parse_race_comment(
            section
        )

        result[race_no] = {
            "comments": comments,
            "lines": lines,
            "race_comment": race_comment,
        }

    return {
        "venue": venue["name"],
        "races": result,
    }


# ============================================================
# COMMENT ANALYSIS
# ============================================================

def analyze_comment(comment):

    comment = clean_text(comment)

    data = {
        "comment_type": "不明",
        "jump_probability": 0.0,
        "front_probability": 0.0,
        "attack_probability": 0.0,
    }

    if not comment:
        return data

    # --------------------------------------------------------
    # 自力
    # --------------------------------------------------------

    if "自力自在" in comment:

        data["comment_type"] = "自力自在"
        data["front_probability"] = 75
        data["attack_probability"] = 90

    elif "自力基本" in comment:

        data["comment_type"] = "自力基本"
        data["front_probability"] = 85
        data["attack_probability"] = 90

    elif "自力" in comment:

        data["comment_type"] = "自力"
        data["front_probability"] = 85
        data["attack_probability"] = 90

    elif "先行" in comment:

        data["comment_type"] = "先行"
        data["front_probability"] = 95
        data["attack_probability"] = 85

    elif (
        "捲り" in comment
        or "まくり" in comment
    ):

        data["comment_type"] = "捲り"
        data["front_probability"] = 45
        data["attack_probability"] = 90

    elif "自在" in comment:

        data["comment_type"] = "自在"
        data["front_probability"] = 55
        data["attack_probability"] = 70

    elif "何でも" in comment:

        data["comment_type"] = "何でも"
        data["front_probability"] = 60
        data["attack_probability"] = 75

    elif "何か" in comment:

        data["comment_type"] = "何か"
        data["front_probability"] = 50
        data["attack_probability"] = 65

    elif "単騎" in comment:

        data["comment_type"] = "単騎"
        data["front_probability"] = 35
        data["attack_probability"] = 60

    elif (
        "番手" in comment
        or "マーク" in comment
        or "君へ" in comment
        or "さんへ" in comment
    ):

        data["comment_type"] = "番手"

    # --------------------------------------------------------
    # 飛びつき
    # --------------------------------------------------------

    jump_words = [
        "飛びつ",
        "飛付",
        "番手勝負",
        "番手を狙",
        "番手取り",
        "位置取り",
        "位置を取",
        "好位",
        "前々",
        "切り込",
        "競り",
        "競る",
        "何でもやる",
        "何かする",
    ]

    for word in jump_words:

        if word in comment:

            data[
                "jump_probability"
            ] += 25

    if (
        "自在" in comment
        or "何でも" in comment
        or "何か" in comment
    ):
        data[
            "jump_probability"
        ] += 15

    if "前々" in comment:
        data[
            "jump_probability"
        ] += 20

    data[
        "jump_probability"
    ] = min(
        100,
        data["jump_probability"],
    )

    return data


# ============================================================
# HISTORICAL METRICS
# ============================================================

def total_results(rider):

    f = rider.get(
        "finish",
        {},
    )

    return (
        f.get("1", 0)
        + f.get("2", 0)
        + f.get("3", 0)
        + f.get("out", 0)
    )


def win_rate(rider):

    total = total_results(rider)

    if total == 0:
        return 0

    return (
        rider["finish"].get("1", 0)
        / total
    )


def top2_rate(rider):

    total = total_results(rider)

    if total == 0:
        return 0

    return (
        rider["finish"].get("1", 0)
        + rider["finish"].get("2", 0)
    ) / total


def top3_rate(rider):

    total = total_results(rider)

    if total == 0:
        return 0

    return (
        rider["finish"].get("1", 0)
        + rider["finish"].get("2", 0)
        + rider["finish"].get("3", 0)
    ) / total


def recent_form(rider):

    results = rider.get(
        "recent_results",
        [],
    )

    if not results:
        return 0.0

    score = 0
    weight = 0

    for i, result in enumerate(
        results[:7]
    ):

        rank = result.get(
            "rank",
            9,
        )

        if rank == 1:
            value = 1.0
        elif rank == 2:
            value = 0.75
        elif rank == 3:
            value = 0.55
        elif rank == 4:
            value = 0.35
        else:
            value = 0.1

        w = 1 / (i + 1)

        score += value * w
        weight += w

    if weight == 0:
        return 0

    return score / weight


# ============================================================
# LINE / ROLE
# ============================================================

def assign_lines(race):

    lines = race.get(
        "lines",
        [],
    )

    for rider in race["riders"]:

        rider["line"] = []
        rider["line_role"] = ""

        car = rider["car_no"]

        for line in lines:

            if car not in line:
                continue

            rider["line"] = line

            pos = line.index(car)

            if pos == 0:
                rider["line_role"] = "先頭"

            elif pos == 1:
                rider["line_role"] = "番手"

            else:
                rider["line_role"] = "三番手以降"

            break


def estimate_line_style(rider):

    comment = rider.get(
        "comment",
        "",
    )

    registered = rider.get(
        "style",
        "",
    )

    comment_type = rider.get(
        "comment_type",
        "不明",
    )

    if (
        comment_type in
        ("自力", "自力基本")
    ):
        return "自力"

    if comment_type == "自力自在":
        return "自在"

    if comment_type == "先行":
        return "先行"

    if comment_type == "捲り":
        return "捲り"

    if comment_type in (
        "自在",
        "何でも",
        "何か",
    ):
        return "自在"

    if comment_type == "番手":
        return "追込"

    if registered == "逃":
        return "逃捲"

    if registered == "追":
        return "追込"

    if registered == "両":
        return "自在"

    return registered


# ============================================================
# RACE DEVELOPMENT
# ============================================================

def analyze_development(race):

    riders = race["riders"]

    attacking = []
    front_candidates = []
    jumpers = []

    for rider in riders:

        if (
            rider.get(
                "attack_probability",
                0,
            ) >= 70
        ):
            attacking.append(
                rider["car_no"]
            )

        if (
            rider.get(
                "front_probability",
                0,
            ) >= 70
        ):
            front_candidates.append(
                rider["car_no"]
            )

        if (
            rider.get(
                "jump_probability",
                0,
            ) >= 40
        ):
            jumpers.append(
                rider["car_no"]
            )

    # ライン単位
    lines = race.get(
        "lines",
        [],
    )

    active_lines = []

    for line in lines:

        active = False

        for car in line:

            rider = next(
                (
                    r
                    for r in riders
                    if r["car_no"] == car
                ),
                None,
            )

            if not rider:
                continue

            if (
                rider.get(
                    "attack_probability",
                    0,
                ) >= 70
                or rider.get(
                    "front_probability",
                    0,
                ) >= 70
            ):
                active = True

        if active:
            active_lines.append(
                line
            )

    if len(active_lines) >= 3:

        pattern = "先行争い激化"

        development = (
            "自力・前々型が複数存在。"
            "主導権争いから捲り・番手差しまで警戒。"
        )

    elif len(active_lines) == 2:

        pattern = "二分戦"

        development = (
            "主導権候補が2ライン。"
            "先行ラインの番手と後方捲りを比較。"
        )

    elif len(active_lines) == 1:

        pattern = "一本主導権"

        development = (
            "主導権候補が比較的明確。"
            "番手差しと後方からの捲りを重視。"
        )

    else:

        pattern = "混戦"

        development = (
            "明確な主導権候補が少ない。"
            "位置取り・仕掛けのタイミングを重視。"
        )

    if jumpers:

        development += (
            " 飛びつき警戒: "
            + ",".join(
                map(str, jumpers)
            )
            + "車。"
        )

    return {
        "pattern": pattern,
        "development": development,
        "active_lines": active_lines,
        "attackers": attacking,
        "front_candidates": front_candidates,
        "jumpers": jumpers,
    }


# ============================================================
# AI SCORE
# ============================================================

def calculate_score(
    rider,
    race,
):

    riders = race["riders"]

    all_scores = [
        r.get(
            "score",
            0,
        )
        for r in riders
        if r.get(
            "score",
            0,
        ) > 0
    ]

    if all_scores:

        max_score = max(
            all_scores
        )

        min_score = min(
            all_scores
        )

    else:

        max_score = 100
        min_score = 80

    raw = rider.get(
        "score",
        0,
    )

    if max_score > min_score:

        relative = (
            raw - min_score
        ) / (
            max_score - min_score
        )

    else:
        relative = 0.5

    # --------------------------------------------------------
    # 基礎能力
    # --------------------------------------------------------

    ai = relative * 28

    ai += win_rate(
        rider
    ) * 18

    ai += top2_rate(
        rider
    ) * 15

    ai += top3_rate(
        rider
    ) * 10

    ai += recent_form(
        rider
    ) * 12

    # --------------------------------------------------------
    # 決まり手
    # --------------------------------------------------------

    kimari = rider.get(
        "kimari",
        {},
    )

    ai += min(
        5,
        kimari.get(
            "maki",
            0,
        ) * 0.7,
    )

    ai += min(
        5,
        kimari.get(
            "sashi",
            0,
        ) * 0.45,
    )

    # --------------------------------------------------------
    # 展開
    # --------------------------------------------------------

    ai += (
        rider.get(
            "attack_probability",
            0,
        ) * 0.055
    )

    ai += (
        rider.get(
            "front_probability",
            0,
        ) * 0.035
    )

    # --------------------------------------------------------
    # 番手
    # --------------------------------------------------------

    if rider.get(
        "line_role"
    ) == "番手":

        ai += 5

    elif rider.get(
        "line_role"
    ) == "三番手以降":

        ai += 2

    # --------------------------------------------------------
    # 飛びつき
    # --------------------------------------------------------

    jump = rider.get(
        "jump_probability",
        0,
    )

    # 飛びつきは単純加点ではなく
    # 「展開変化要素」として控えめに評価
    ai += jump * 0.025

    # --------------------------------------------------------
    # 競輪場傾向
    # --------------------------------------------------------

    bias = race.get(
        "venue_bias",
        {},
    )

    style = rider.get(
        "line_style",
        "",
    )

    if style in (
        "先行",
        "逃捲",
        "自力",
    ):

        ai += (
            bias.get(
                "nige",
                0,
            ) * 0.035
        )

    if style in (
        "捲り",
        "逃捲",
        "自力",
    ):

        ai += (
            bias.get(
                "maki",
                0,
            ) * 0.035
        )

    if rider.get(
        "line_role"
    ) in (
        "番手",
        "三番手以降",
    ):

        ai += (
            bias.get(
                "sashi",
                0,
            ) * 0.035
        )

    # --------------------------------------------------------
    # 年齢
    # --------------------------------------------------------

    age = rider.get(
        "age",
        0,
    )

    if 0 < age <= 34:
        ai += 1.0

    elif 35 <= age <= 42:
        ai += 0.5

    elif age >= 50:
        ai -= 0.5

    return round(
        ai,
        2,
    )


# ============================================================
# FINAL PREDICTION
# ============================================================

def make_prediction(race):

    riders = race["riders"]

    if not riders:
        return {}

    # --------------------------------------------------------
    # 基礎順位
    # --------------------------------------------------------

    ranked = sorted(
        riders,
        key=lambda r: r.get(
            "ai_score",
            0,
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # 展開補正
    # --------------------------------------------------------

    for rider in ranked:

        value = rider.get(
            "ai_score",
            0,
        )

        # 番手
        if rider.get(
            "line_role"
        ) == "番手":

            value += 3.0

        # 自力の仕掛け
        if rider.get(
            "attack_probability",
            0,
        ) >= 80:

            value += 2.0

        # 飛びつき
        if rider.get(
            "jump_probability",
            0,
        ) >= 60:

            value += 1.5

        rider["_final"] = value

    ranked.sort(
        key=lambda r: r[
            "_final"
        ],
        reverse=True,
    )

    main = (
        ranked[0]
        if len(ranked) > 0
        else None
    )

    opponent = (
        ranked[1]
        if len(ranked) > 1
        else None
    )

    third = (
        ranked[2]
        if len(ranked) > 2
        else None
    )

    # --------------------------------------------------------
    # 穴
    #
    # 4位固定ではなく、
    # 飛びつき・攻撃力・直近状態を考慮
    # --------------------------------------------------------

    candidates = ranked[3:]

    if candidates:

        hole = max(
            candidates,
            key=lambda r: (
                r.get(
                    "jump_probability",
                    0,
                ) * 0.8
                + r.get(
                    "attack_probability",
                    0,
                ) * 0.5
                + recent_form(r) * 30
                + r.get(
                    "score",
                    0,
                ) * 0.05
            ),
        )

    else:

        hole = third

    # --------------------------------------------------------
    # 信頼度
    # --------------------------------------------------------

    if opponent:

        gap = (
            main["_final"]
            - opponent["_final"]
        )

    else:

        gap = 0

    if gap >= 12:
        confidence = 90
        confidence_text = "かなり高い"

    elif gap >= 8:
        confidence = 82
        confidence_text = "高い"

    elif gap >= 4:
        confidence = 68
        confidence_text = "普通"

    else:
        confidence = 52
        confidence_text = "混戦"

    # --------------------------------------------------------
    # 車番
    # --------------------------------------------------------

    def car(rider):
        if rider:
            return rider["car_no"]

        return None

    m = car(main)
    o = car(opponent)
    t = car(third)
    h = car(hole)

    # --------------------------------------------------------
    # 2車単
    # --------------------------------------------------------

    exacta = []

    if m and o:

        exacta.append(
            [m, o]
        )

        # 混戦なら裏も残す
        if confidence < 70:

            exacta.append(
                [o, m]
            )

    # --------------------------------------------------------
    # 3連単
    # --------------------------------------------------------

    trifecta = []

    if m and o and t:

        trifecta.append(
            [m, o, t]
        )

        trifecta.append(
            [m, t, o]
        )

    # 穴
    if (
        m
        and h
        and t
        and h not in [m, t]
    ):

        trifecta.append(
            [m, h, t]
        )

    if (
        o
        and m
        and h
        and h not in [o, m]
    ):

        trifecta.append(
            [o, m, h]
        )

    return {
        "pattern": race.get(
            "development",
            {},
        ).get(
            "pattern",
            "通常戦",
        ),
        "development": race.get(
            "development",
            {},
        ).get(
            "development",
            "",
        ),
        "confidence": confidence,
        "confidence_text": confidence_text,
        "main": m,
        "opponent": o,
        "third": t,
        "hole": h,
        "exacta": exacta,
        "trifecta": trifecta,
    }


# ============================================================
# APPLY
# ============================================================

def apply_prediction(
    race,
    prediction_data,
):

    comments = prediction_data.get(
        "comments",
        {},
    )

    lines = prediction_data.get(
        "lines",
        [],
    )

    race_comment = prediction_data.get(
        "race_comment",
        "",
    )

    race["lines"] = lines

    race["race_comment"] = (
        race_comment
    )

    # --------------------------------------------------------
    # rider
    # --------------------------------------------------------

    for rider in race["riders"]:

        car = rider["car_no"]

        comment_data = comments.get(
            car,
            {},
        )

        comment = comment_data.get(
            "comment",
            "",
        )

        rider["comment"] = comment

        analysis = analyze_comment(
            comment
        )

        rider.update(
            analysis
        )

    # --------------------------------------------------------
    # line
    # --------------------------------------------------------

    assign_lines(
        race
    )

    # --------------------------------------------------------
    # 展開脚質
    # --------------------------------------------------------

    for rider in race["riders"]:

        rider[
            "line_style"
        ] = estimate_line_style(
            rider
        )

    # --------------------------------------------------------
    # development
    # --------------------------------------------------------

    race[
        "development"
    ] = analyze_development(
        race
    )

    # --------------------------------------------------------
    # score
    # --------------------------------------------------------

    for rider in race["riders"]:

        rider[
            "ai_score"
        ] = calculate_score(
            rider,
            race,
        )

    # --------------------------------------------------------
    # rank
    # --------------------------------------------------------

    ranked = sorted(
        race["riders"],
        key=lambda r: r[
            "ai_score"
        ],
        reverse=True,
    )

    for rank, rider in enumerate(
        ranked,
        start=1,
    ):

        rider[
            "ai_rank"
        ] = rank

        if rank == 1:
            rider[
                "ai_label"
            ] = "本命"

        elif rank == 2:
            rider[
                "ai_label"
            ] = "対抗"

        elif rank == 3:
            rider[
                "ai_label"
            ] = "単穴"

        else:
            rider[
                "ai_label"
            ] = "穴候補"

    # --------------------------------------------------------
    # prediction
    # --------------------------------------------------------

    race[
        "prediction"
    ] = make_prediction(
        race
    )

    # internal key
    for rider in race["riders"]:

        rider.pop(
            "_final",
            None,
        )


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    print("")
    print("==============================")
    print(" KEIRIN AI DATA UPDATE v4.2")
    print("==============================")
    print(
        f"対象日: "
        f"{TODAY[:4]}-"
        f"{TODAY[4:6]}-"
        f"{TODAY[6:]}"
    )
    print("==============================")

    # --------------------------------------------------------
    # VENUES
    # --------------------------------------------------------

    venues = get_venues()

    print(
        f"開催場数: {len(venues)}"
    )

    for venue in venues:

        print(
            f"  {venue['jo_code']} "
            f"{venue['name']}"
        )

    # --------------------------------------------------------
    # RACES
    # --------------------------------------------------------

    targets = []

    for venue in venues:

        race_numbers = get_race_numbers(
            venue
        )

        print(
            f"{venue['name']}: "
            f"{len(race_numbers)}レース"
        )

        for race_no in race_numbers:

            targets.append(
                (
                    venue,
                    race_no,
                )
            )

    print("")
    print(
        f"詳細取得対象レース: "
        f"{len(targets)}"
    )

    # --------------------------------------------------------
    # RACE DATA
    # --------------------------------------------------------

    races = []

    failed = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_race,
                venue,
                race_no,
            ): (
                venue,
                race_no,
            )
            for venue, race_no in targets
        }

        for future in as_completed(
            futures
        ):

            venue, race_no = futures[
                future
            ]

            try:

                race = future.result()

                if race:

                    races.append(
                        race
                    )

                else:

                    failed += 1

            except Exception as e:

                failed += 1

                print(
                    "race error:",
                    venue["name"],
                    race_no,
                    repr(e),
                )

    races.sort(
        key=lambda r: (
            r["jo_code"],
            r["race_no"],
        )
    )

    # --------------------------------------------------------
    # PREDICTION PAGES
    # --------------------------------------------------------

    print("")
    print(
        "コメント・並び・展開情報取得中..."
    )

    prediction_pages = {}

    with ThreadPoolExecutor(
        max_workers=7
    ) as executor:

        futures = {
            executor.submit(
                fetch_prediction_venue,
                venue,
            ): venue
            for venue in venues
        }

        for future in as_completed(
            futures
        ):

            venue = futures[
                future
            ]

            try:

                data = future.result()

                prediction_pages[
                    venue["name"]
                ] = data

            except Exception as e:

                print(
                    "prediction error:",
                    venue["name"],
                    repr(e),
                )

                prediction_pages[
                    venue["name"]
                ] = {
                    "races": {}
                }

    # --------------------------------------------------------
    # APPLY
    # --------------------------------------------------------

    comment_races = 0
    line_races = 0
    race_comment_races = 0

    comment_riders = 0
    jump_watch = []

    processed = 0
    rider_total = 0
    valid_names = 0

    # 会場別集計
    venue_stats = {}

    for venue in venues:

        venue_stats[
            venue["name"]
        ] = {
            "races": 0,
            "comments": 0,
            "lines": 0,
            "race_comments": 0,
            "riders": 0,
        }

    for race in races:

        venue_name = race[
            "venue"
        ]

        race_no = race[
            "race_no"
        ]

        venue_stats[
            venue_name
        ]["races"] += 1

        prediction_data = (
            prediction_pages
            .get(
                venue_name,
                {},
            )
            .get(
                "races",
                {}
            )
            .get(
                race_no,
                {
                    "comments": {},
                    "lines": [],
                    "race_comment": "",
                },
            )
        )

        if prediction_data.get(
            "comments"
        ):

            comment_races += 1

            venue_stats[
                venue_name
            ]["comments"] += 1

        if prediction_data.get(
            "lines"
        ):

            line_races += 1

            venue_stats[
                venue_name
            ]["lines"] += 1

        if prediction_data.get(
            "race_comment"
        ):

            race_comment_races += 1

            venue_stats[
                venue_name
            ]["race_comments"] += 1

        apply_prediction(
            race,
            prediction_data,
        )

        for rider in race[
            "riders"
        ]:

            rider_total += 1

            venue_stats[
                venue_name
            ]["riders"] += 1

            if valid_name(
                rider["name"]
            ):
                valid_names += 1

            if rider.get(
                "comment"
            ):
                comment_riders += 1

            if rider.get(
                "jump_probability",
                0,
            ) >= 40:

                jump_watch.append(
                    {
                        "venue": venue_name,
                        "race": race_no,
                        "car_no": rider[
                            "car_no"
                        ],
                        "name": rider[
                            "name"
                        ],
                        "jump_probability":
                            rider[
                                "jump_probability"
                            ],
                        "comment":
                            rider.get(
                                "comment",
                                "",
                            ),
                    }
                )

        processed += 1

    # --------------------------------------------------------
    # SORT JUMP WATCH
    # --------------------------------------------------------

    jump_watch.sort(
        key=lambda x: x[
            "jump_probability"
        ],
        reverse=True,
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    output = {
        "version": "4.2",

        "updated_at": datetime.now(
            JST
        ).isoformat(),

        "date": (
            f"{TODAY[:4]}-"
            f"{TODAY[4:6]}-"
            f"{TODAY[6:]}"
        ),

        "summary": {
            "venues": len(venues),
            "races": len(races),
            "riders": rider_total,
            "valid_names": valid_names,

            "comment_races":
                comment_races,

            "line_races":
                line_races,

            "race_comment_races":
                race_comment_races,

            "comment_riders":
                comment_riders,

            "jump_watch":
                len(jump_watch),

            "failed_races":
                failed,
        },

        "venue_stats":
            venue_stats,

        "venues":
            venues,

        "races":
            races,

        "jump_watch":
            jump_watch[:100],
    }

    os.makedirs(
        os.path.dirname(
            OUT_FILE
        ),
        exist_ok=True,
    )

    with open(
        OUT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    elapsed = (
        time.time()
        - started
    )

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    print("")
    print("==============================")
    print(" 更新完了 v4.2")
    print("==============================")

    print(
        f"開催場: {len(venues)}"
    )

    print(
        f"レース: {len(races)}"
    )

    print(
        f"選手: {rider_total}"
    )

    print(
        f"正しい選手名: "
        f"{valid_names}"
    )

    print("")
    print("【会場別取得状況】")

    for venue in venues:

        name = venue["name"]

        s = venue_stats.get(
            name,
            {},
        )

        print(
            f"{name}: "
            f"レース{s.get('races', 0)} "
            f"コメント{s.get('comments', 0)} "
            f"並び{s.get('lines', 0)} "
            f"展開{s.get('race_comments', 0)}"
        )

    print("")
    print(
        f"コメント取得レース: "
        f"{comment_races}/{len(races)}"
    )

    print(
        f"並び取得レース: "
        f"{line_races}/{len(races)}"
    )

    print(
        f"展開コメント取得レース: "
        f"{race_comment_races}/{len(races)}"
    )

    print(
        f"選手コメント: "
        f"{comment_riders}"
    )

    print(
        f"飛びつき警戒選手: "
        f"{len(jump_watch)}"
    )

    print(
        f"AI評価レース: "
        f"{processed}"
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
        f"{OUT_FILE}"
    )

    print("==============================")


if __name__ == "__main__":
    main()
