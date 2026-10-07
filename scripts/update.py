import json
import os
import re
import time
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning


# ============================================================
# KEIRIN AI DATA UPDATE v4.7
# OddsPark data collector
# ============================================================

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

REQUEST_TIMEOUT = 20
MAX_WORKERS = 8
RETRY_COUNT = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

VENUE_CODE_TO_NAME = {
    "22": "前橋",
    "25": "大宮",
    "38": "静岡",
    "44": "大垣",
    "48": "四日市",
    "53": "奈良",
    "74": "高知",
}

VENUE_NAME_TO_SLUG = {
    "前橋": "maebashi",
    "大宮": "omiya",
    "静岡": "shizuoka",
    "大垣": "ogaki",
    "四日市": "yokkaichi",
    "奈良": "nara",
    "高知": "kochi",
}

STYLE_WORDS = {
    "逃捲",
    "逃げ",
    "先捲",
    "捲り",
    "捲",
    "追込",
    "追捲",
    "差脚",
    "自在",
    "前々",
    "前 々",
    "単騎",
    "両",
}

PREDICTION_MARK_SCORE = {
    "◎": 18,
    "○": 12,
    "▲": 8,
    "△": 5,
    "×": 1,
    "…": -4,
    "": 0,
}


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def get_html(url):
    """
    HTML取得。
    失敗時は短時間リトライ。
    """
    for attempt in range(RETRY_COUNT + 1):
        try:
            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if response.status_code == 200:
                response.encoding = response.apparent_encoding or response.encoding
                return response.text

            if response.status_code in (404, 410):
                return None

        except requests.RequestException:
            pass

        if attempt < RETRY_COUNT:
            time.sleep(0.5 * (attempt + 1))

    return None


def soup_from_html(html):
    if not html:
        return None

    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return None


def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


# ============================================================
# BASIC PARSERS
# ============================================================

def to_int(value):
    try:
        return int(str(value).strip())
    except Exception:
        return None


def to_float(value):
    try:
        value = str(value).replace(",", "").strip()
        return float(value)
    except Exception:
        return None


def normalize_name(value):
    value = clean_text(value)
    value = value.replace("　", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_age_period(text):
    text = clean_text(text)

    match = re.search(r"(\d{2})\s*/\s*(\d{2,3})", text)

    if not match:
        return None, None

    return (
        to_int(match.group(1)),
        to_int(match.group(2)),
    )


def extract_numbers(text):
    return [
        int(x)
        for x in re.findall(r"\d+", clean_text(text))
    ]


# ============================================================
# VENUE / RACE LIST
# ============================================================

def get_venues():
    """
    当日の開催場を取得。
    """
    url = (
        f"{BASE_URL}/keirin/RaceListInfo.do"
        f"?kaisaiBi={TODAY}"
    )

    html = get_html(url)

    if not html:
        return []

    soup = soup_from_html(html)

    if not soup:
        return []

    found = {}

    text = soup.get_text(" ", strip=True)

    for code, name in VENUE_CODE_TO_NAME.items():
        if name in text:
            found[code] = name

    # URL中のjoCodeからも補強
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        match = re.search(r"joCode=(\d+)", href)

        if match:
            code = match.group(1)

            if code in VENUE_CODE_TO_NAME:
                found[code] = VENUE_CODE_TO_NAME[code]

    return [
        {
            "venue_code": code,
            "venue_name": found[code],
        }
        for code in VENUE_CODE_TO_NAME
        if code in found
    ]


def get_race_numbers(venue_code):
    """
    当日の開催レース番号を取得。
    """
    urls = [
        (
            f"{BASE_URL}/keirin/AllRaceList.do"
            f"?joCode={venue_code}&kaisaiBi={TODAY}"
        ),
        (
            f"{BASE_URL}/keirin/RaceListInfo.do"
            f"?joCode={venue_code}&kaisaiBi={TODAY}"
        ),
    ]

    for url in urls:
        html = get_html(url)

        if not html:
            continue

        numbers = set()

        # raceNo=1 のようなURL
        for match in re.findall(r"raceNo=(\d+)", html):
            race_no = to_int(match)
            if race_no and 1 <= race_no <= 12:
                numbers.add(race_no)

        # 1R / 2R ... の表記
        for match in re.findall(r"(\d{1,2})R", html):
            race_no = to_int(match)
            if race_no and 1 <= race_no <= 12:
                numbers.add(race_no)

        if numbers:
            return sorted(numbers)

    return []


# ============================================================
# RACE DETAIL
# ============================================================

def parse_finish(text):
    nums = extract_numbers(text)

    if len(nums) >= 4:
        return {
            "finish_1": nums[0],
            "finish_2": nums[1],
            "finish_3": nums[2],
            "finish_out": nums[3],
        }

    return {
        "finish_1": None,
        "finish_2": None,
        "finish_3": None,
        "finish_out": None,
    }


def parse_kimari(text):
    nums = extract_numbers(text)

    if len(nums) >= 4:
        return {
            "kimari_nige": nums[0],
            "kimari_makuri": nums[1],
            "kimari_sashi": nums[2],
            "kimari_mark": nums[3],
        }

    return {
        "kimari_nige": None,
        "kimari_makuri": None,
        "kimari_sashi": None,
        "kimari_mark": None,
    }


def parse_rider_row(cells):
    """
    オッズパークの出走表をなるべく構造依存せず解析。
    """

    values = [clean_text(c) for c in cells]

    if not values:
        return None

    car_no = None

    for value in values[:3]:
        if re.fullmatch(r"[1-7]", value):
            car_no = int(value)
            break

    if car_no is None:
        return None

    # 名前候補
    name = ""

    for value in values:
        if not value:
            continue

        if value == str(car_no):
            continue

        # 年齢/期別
        if re.search(r"\d{2}\s*/\s*\d{2,3}", value):
            continue

        # 点数
        if re.fullmatch(r"\d{2,3}(?:\.\d+)?", value):
            continue

        # 数字だけ
        if re.fullmatch(r"\d+", value):
            continue

        # 成績っぽい数字列
        if len(extract_numbers(value)) >= 3 and len(value) <= 30:
            continue

        if len(value) >= 2:
            name = normalize_name(value)
            break

    if not name:
        return None

    age = None
    period = None

    for value in values:
        a, p = extract_age_period(value)

        if a is not None:
            age = a
            period = p
            break

    prefecture = ""
    grade = ""
    style = ""
    score = None

    prefectures = {
        "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
        "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
        "新潟", "富山", "石川", "福井", "山梨", "長野", "岐阜",
        "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫",
        "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "山口",
        "徳島", "香川", "愛媛", "高知", "福岡", "佐賀", "長崎",
        "熊本", "大分", "宮崎", "鹿児島", "沖縄",
    }

    grades = {
        "S1", "S2",
        "A1", "A2", "A3",
        "L1", "L2",
    }

    styles = {
        "逃", "捲", "追", "両", "自在",
    }

    for value in values:
        if value in prefectures:
            prefecture = value

        if value in grades:
            grade = value

        if value in styles:
            style = value

        if re.fullmatch(r"\d{2,3}(?:\.\d+)?", value):
            number = to_float(value)

            if number is not None and 60 <= number <= 130:
                score = number

    finish = {}
    kimari = {}

    # 数字が4つ以上並ぶセルを成績として扱う
    for value in values:
        nums = extract_numbers(value)

        if len(nums) >= 4:
            if not finish:
                finish = parse_finish(value)

            elif not kimari:
                kimari = parse_kimari(value)

    if not finish:
        finish = parse_finish("")

    if not kimari:
        kimari = parse_kimari("")

    return {
        "car_no": car_no,
        "name": name,
        "age": age,
        "period": period,
        "prefecture": prefecture,
        "grade": grade,
        "style": style,
        "score": score,
        **finish,
        **kimari,
        "comment": "",
        "prediction_mark": "",
        "prediction_score": 0,
        "ai_score": 0,
    }


def extract_riders(soup):
    """
    出走表のテーブルを探して7車を取得。
    """

    best = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        if not rows:
            continue

        riders = []

        for row in rows:
            cells = row.find_all(["th", "td"])

            if not cells:
                continue

            rider = parse_rider_row(
                [cell.get_text(" ", strip=True) for cell in cells]
            )

            if rider:
                riders.append(rider)

        # 1〜7番が揃っているテーブルを優先
        car_numbers = {
            r["car_no"]
            for r in riders
            if r.get("car_no")
        }

        if len(car_numbers) >= 7:
            ordered = sorted(
                riders,
                key=lambda x: x["car_no"]
            )

            return ordered[:7]

        if len(riders) > len(best):
            best = riders

    return sorted(
        best,
        key=lambda x: x.get("car_no") or 99
    )[:7]


def get_race_url(venue_code, race_no):
    return (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


def get_race_data(venue_code, venue_name, race_no):
    url = get_race_url(venue_code, race_no)

    html = get_html(url)

    if not html:
        return {
            "venue_code": venue_code,
            "venue_name": venue_name,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "success": False,
        }

    soup = soup_from_html(html)

    if not soup:
        return {
            "venue_code": venue_code,
            "venue_name": venue_name,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "success": False,
        }

    riders = extract_riders(soup)

    return {
        "venue_code": venue_code,
        "venue_name": venue_name,
        "race_no": race_no,
        "url": url,
        "riders": riders,
        "success": len(riders) >= 7,
    }


# ============================================================
# PREDICTION PAGE
# ============================================================

def get_prediction_index_url(venue_name):
    slug = VENUE_NAME_TO_SLUG.get(venue_name)

    if not slug:
        return None

    return (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/{TODAY[4:]}.html"
    )


def get_race_no_from_url(url):
    if not url:
        return None

    # 1007_12.html
    match = re.search(r"_(\d+)\.html", url)

    if match:
        race_no = to_int(match.group(1))

        if race_no and 1 <= race_no <= 12:
            return race_no

    return None


def discover_prediction_pages(venue_name, expected_races):
    """
    予想ページを取得。

    重要：
    1Rは一覧ページ自身に含まれる。
    2R以降はリンク先ページとして存在する。
    """

    index_url = get_prediction_index_url(venue_name)

    if not index_url:
        return {}

    html = get_html(index_url)

    if not html:
        return {}

    soup = soup_from_html(html)

    if not soup:
        return {}

    found = {}

    # --------------------------------------------------------
    # 1R
    # --------------------------------------------------------
    page_text = clean_text(soup.get_text(" ", strip=True))

    if re.search(r"\b1R\b", page_text):
        found[1] = {
            "url": index_url,
            "html": html,
        }

    # --------------------------------------------------------
    # 2R〜
    # --------------------------------------------------------
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = clean_text(a.get_text(" ", strip=True))

        race_no = get_race_no_from_url(href)

        if race_no is None:
            match = re.fullmatch(r"(\d{1,2})R", text)

            if match:
                race_no = int(match.group(1))

        if race_no is None:
            continue

        if race_no not in expected_races:
            continue

        full_url = urljoin(index_url, href)

        if race_no == 1:
            continue

        found[race_no] = {
            "url": full_url,
            "html": None,
        }

    return found


# ============================================================
# PREDICTION TABLE
# ============================================================

def is_prediction_header(values):
    joined = " ".join(values)

    return (
        "車" in joined
        and "印" in joined
        and "選手名" in joined
        and "コメント" in joined
    )


def parse_prediction_table(soup):
    """
    実際のオッズパーク予想表：

    車 | 印 | 選手名 | コメント

    を直接取得する。
    """

    result = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        if not rows:
            continue

        header_found = False
        header_index = -1

        for index, row in enumerate(rows):
            values = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"])
            ]

            if is_prediction_header(values):
                header_found = True
                header_index = index
                break

        if not header_found:
            continue

        for row in rows[header_index + 1:]:
            cells = row.find_all(["th", "td"])

            if len(cells) < 3:
                continue

            values = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in cells
            ]

            car_no = None

            for value in values[:2]:
                if re.fullmatch(r"[1-7]", value):
                    car_no = int(value)
                    break

            if car_no is None:
                continue

            mark = ""

            for value in values:
                if value in PREDICTION_MARK_SCORE:
                    mark = value
                    break

            # 選手名は基本的に3列目
            name = ""
            if len(values) >= 3:
                name = normalize_name(values[2])

            # コメントは3列目以降から名前を除いたもの
            comment_parts = []

            for value in values[3:]:
                if value:
                    comment_parts.append(value)

            comment = clean_text(" ".join(comment_parts))

            result[car_no] = {
                "name": name,
                "mark": mark,
                "comment": comment,
                "prediction_score": PREDICTION_MARK_SCORE.get(
                    mark, 0
                ),
            }

        if len(result) >= 7:
            return result

    return result


# ============================================================
# LINE / DEVELOPMENT
# ============================================================

def looks_like_car_number(value):
    return bool(re.fullmatch(r"[1-7]", clean_text(value)))


def extract_line_table(soup):
    """
    予想ページの

       1 5   2 6   4   7 3
       ← 逃捲 追込 ... 

    の構造を解析。

    空セルをライン境界として扱う。
    """

    tables = soup.find_all("table")

    best = None

    for table in tables:
        rows = table.find_all("tr")

        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"])

            values = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in cells
            ]

            nums = [
                int(v)
                for v in values
                if looks_like_car_number(v)
            ]

            if len(nums) != 7:
                continue

            if set(nums) != set(range(1, 8)):
                continue

            # 次行に脚質があるか確認
            style_score = 0

            nearby = []

            for next_row in rows[i:i + 3]:
                nearby.extend([
                    clean_text(cell.get_text(" ", strip=True))
                    for cell in next_row.find_all(["th", "td"])
                ])

            for value in nearby:
                if value in STYLE_WORDS:
                    style_score += 1

            score = style_score * 10

            if best is None or score > best["score"]:
                best = {
                    "score": score,
                    "values": values,
                    "row": row,
                    "table": table,
                    "index": i,
                }

    if not best:
        return [], []

    values = best["values"]

    # --------------------------------------------------------
    # セル位置からラインを分ける
    # --------------------------------------------------------
    groups = []
    current = []

    for value in values:
        if looks_like_car_number(value):
            current.append(int(value))
        else:
            if current:
                groups.append(current)
                current = []

    if current:
        groups.append(current)

    # 連続している数字しか取れない場合は、
    # 7車が揃っているだけでも1ラインとして保持
    flat = [n for group in groups for n in group]

    if sorted(flat) != list(range(1, 8)):
        return [], []

    # --------------------------------------------------------
    # 脚質行
    # --------------------------------------------------------
    styles = []

    rows = best["table"].find_all("tr")
    start_index = best["index"]

    for row in rows[start_index + 1:start_index + 3]:
        row_values = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]

        candidate = [
            value
            for value in row_values
            if value in STYLE_WORDS
        ]

        if len(candidate) >= 5:
            styles = candidate
            break

    # --------------------------------------------------------
    # 空セルがHTML上で省略されるケース用
    # コメントページの一般的な構造から、
    # グループ長を推定する。
    # --------------------------------------------------------
    if not groups:
        groups = [flat]

    # --------------------------------------------------------
    # 7車全部が1グループになった場合でも、
    # UIで利用できるようlineを保持
    # --------------------------------------------------------
    return flat, groups


def extract_development(soup, race_no):
    """
    予想の並びの直後にある、
    レース全体の予想コメントを取得。
    """

    body = soup.body if soup.body else soup

    # まずテキストブロック単位で探す
    candidates = []

    for element in body.find_all(
        ["p", "div", "td", "li"]
    ):
        text = clean_text(
            element.get_text(" ", strip=True)
        )

        if not text:
            continue

        if len(text) < 25:
            continue

        if len(text) > 400:
            continue

        # 注意書きは除外
        if "予想の並び" in text:
            continue

        if "免責事項" in text:
            continue

        if "正確性" in text:
            continue

        # rider comment単体っぽいものを除外
        if text in ("自力。", "単騎。", "自在。"):
            continue

        candidates.append(text)

    # 予想コメントは通常、
    # 「〜。〜。」の日本語文章になっている
    sentence_candidates = []

    for text in candidates:
        punctuation_count = (
            text.count("。")
            + text.count("！")
            + text.count("？")
        )

        if punctuation_count >= 1:
            sentence_candidates.append(text)

    if sentence_candidates:
        # 長すぎるものより、適度な文章を優先
        sentence_candidates.sort(
            key=lambda x: (
                abs(len(x) - 80),
                -x.count("。")
            )
        )

        return sentence_candidates[0]

    return ""


def parse_prediction_page(html, race_no):
    soup = soup_from_html(html)

    if not soup:
        return {
            "race_no": race_no,
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "success": False,
        }

    prediction = parse_prediction_table(soup)

    line, line_groups = extract_line_table(soup)

    development = extract_development(
        soup,
        race_no
    )

    return {
        "race_no": race_no,
        "comments": prediction,
        "line": line,
        "line_groups": line_groups,
        "development": development,
        "success": (
            len(prediction) >= 7
            or len(line) == 7
            or bool(development)
        ),
    }


def fetch_prediction_page(item):
    race_no, info = item

    html = info.get("html")

    if not html:
        html = get_html(info["url"])

    if not html:
        return race_no, {
            "race_no": race_no,
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "success": False,
        }

    return race_no, parse_prediction_page(
        html,
        race_no
    )


# ============================================================
# MERGE
# ============================================================

def merge_prediction_into_race(
    race,
    prediction,
    prediction_url
):
    race["prediction_url"] = prediction_url

    race["comments"] = {}
    race["line"] = prediction.get("line", [])
    race["line_groups"] = prediction.get(
        "line_groups", []
    )
    race["development"] = prediction.get(
        "development", ""
    )

    comments = prediction.get(
        "comments", {}
    )

    for car_no, info in comments.items():
        race["comments"][str(car_no)] = {
            "name": info.get("name", ""),
            "mark": info.get("mark", ""),
            "comment": info.get("comment", ""),
            "prediction_score": info.get(
                "prediction_score", 0
            ),
        }

    # rider側へ反映
    for rider in race.get("riders", []):
        car_no = rider.get("car_no")

        if car_no is None:
            continue

        info = comments.get(car_no)

        if not info:
            continue

        rider["prediction_mark"] = info.get(
            "mark", ""
        )

        rider["prediction_score"] = info.get(
            "prediction_score", 0
        )

        rider["comment"] = info.get(
            "comment", ""
        )


# ============================================================
# AI SCORE
# ============================================================

def calculate_rider_ai(rider, race):
    """
    v4.7 AI評価。

    現段階では、
    ・競走得点
    ・近況成績
    ・決まり手
    ・予想印
    ・ライン位置
    ・機動力
    を統合。

    今後、実績データが蓄積したら
    回収率重視モデルへ発展可能。
    """

    score = 0.0

    # --------------------------------------------------------
    # 競走得点
    # --------------------------------------------------------
    rider_score = rider.get("score")

    if rider_score is not None:
        score += max(
            0,
            min(30, (rider_score - 70) * 1.2)
        )

    # --------------------------------------------------------
    # 近況成績
    # --------------------------------------------------------
    f1 = rider.get("finish_1") or 0
    f2 = rider.get("finish_2") or 0
    f3 = rider.get("finish_3") or 0

    score += f1 * 1.8
    score += f2 * 0.9
    score += f3 * 0.4

    # --------------------------------------------------------
    # 決まり手
    # --------------------------------------------------------
    nige = rider.get("kimari_nige") or 0
    makuri = rider.get("kimari_makuri") or 0
    sashi = rider.get("kimari_sashi") or 0
    mark = rider.get("kimari_mark") or 0

    style = rider.get("style", "")

    if style in ("逃", "両"):
        score += nige * 0.9
        score += makuri * 1.0

    if style in ("追",):
        score += sashi * 1.0
        score += mark * 0.5

    # --------------------------------------------------------
    # オッズパーク予想印
    # --------------------------------------------------------
    score += rider.get(
        "prediction_score", 0
    )

    # --------------------------------------------------------
    # ライン位置
    # --------------------------------------------------------
    car_no = rider.get("car_no")
    line = race.get("line", [])

    if car_no in line:
        index = line.index(car_no)

        # 先頭
        if index == 0:
            score += 2.0

        # 番手
        elif index == 1:
            score += 4.0

        # 3番手
        elif index == 2:
            score += 2.0

    # --------------------------------------------------------
    # 展開コメントに名前が出ている場合
    # --------------------------------------------------------
    development = race.get(
        "development", ""
    )

    name = rider.get("name", "")

    if name and development:
        short_name = name.replace(" ", "")

        if short_name and short_name in development.replace(
            " ", ""
        ):
            score += 2.5

    return round(score, 2)


def apply_ai(race):
    riders = race.get("riders", [])

    if not riders:
        race["ai"] = {
            "main": None,
            "opponent": None,
            "dark_horse": None,
            "eliminate": [],
        }
        return

    for rider in riders:
        rider["ai_score"] = calculate_rider_ai(
            rider,
            race
        )

    ranked = sorted(
        riders,
        key=lambda x: (
            x.get("ai_score", 0),
            x.get("score") or 0
        ),
        reverse=True
    )

    main = ranked[0] if len(ranked) >= 1 else None
    opponent = ranked[1] if len(ranked) >= 2 else None

    # 穴候補は4〜6番手から、
    # 予想印や機動力を持つ選手を優先
    dark_candidates = ranked[2:6]

    dark_horse = None

    if dark_candidates:
        dark_candidates = sorted(
            dark_candidates,
            key=lambda x: (
                x.get("prediction_score", 0),
                x.get("kimari_makuri") or 0,
                x.get("kimari_sashi") or 0,
            ),
            reverse=True
        )

        dark_horse = dark_candidates[0]

    # 低評価
    eliminate = [
        r["car_no"]
        for r in ranked[4:]
        if r.get("ai_score", 0) < ranked[0].get(
            "ai_score", 0
        ) - 8
    ]

    race["ai"] = {
        "main": (
            main["car_no"]
            if main else None
        ),
        "opponent": (
            opponent["car_no"]
            if opponent else None
        ),
        "dark_horse": (
            dark_horse["car_no"]
            if dark_horse else None
        ),
        "eliminate": eliminate[:3],
        "ranking": [
            {
                "car_no": r.get("car_no"),
                "name": r.get("name"),
                "score": r.get("ai_score", 0),
                "mark": r.get(
                    "prediction_mark", ""
                ),
            }
            for r in ranked
        ],
    }


# ============================================================
# MAIN
# ============================================================

def main():
    started = time.time()

    print("==============================")
    print(" KEIRIN AI DATA UPDATE v4.7")
    print("==============================")
    print(f"対象日: {TODAY_DISPLAY}")
    print("==============================")

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------
    venues = get_venues()

    if not venues:
        print("開催場を取得できませんでした")
        raise SystemExit(1)

    print(f"開催場数: {len(venues)}")

    all_races = []

    for venue in venues:
        race_numbers = get_race_numbers(
            venue["venue_code"]
        )

        venue["race_numbers"] = race_numbers

        print(
            f"  {venue['venue_code']} "
            f"{venue['venue_name']}: "
            f"{len(race_numbers)}レース"
        )

        for race_no in race_numbers:
            all_races.append(
                {
                    "venue_code": venue[
                        "venue_code"
                    ],
                    "venue_name": venue[
                        "venue_name"
                    ],
                    "race_no": race_no,
                }
            )

    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    # --------------------------------------------------------
    # 出走表を並列取得
    # --------------------------------------------------------
    race_data = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                get_race_data,
                item["venue_code"],
                item["venue_name"],
                item["race_no"],
            ): item
            for item in all_races
        }

        for future in as_completed(futures):
            item = futures[future]

            try:
                result = future.result()
            except Exception as e:
                result = {
                    "venue_code": item[
                        "venue_code"
                    ],
                    "venue_name": item[
                        "venue_name"
                    ],
                    "race_no": item["race_no"],
                    "url": get_race_url(
                        item["venue_code"],
                        item["race_no"],
                    ),
                    "riders": [],
                    "success": False,
                    "error": str(e),
                }

            race_data.append(result)

    race_data.sort(
        key=lambda x: (
            int(x["venue_code"]),
            x["race_no"]
        )
    )

    rider_success = sum(
        1
        for race in race_data
        if len(race.get("riders", [])) >= 7
    )

    print(
        f"選手データ取得結果: "
        f"{rider_success}/{len(race_data)}"
    )

    # --------------------------------------------------------
    # 予想ページURL
    # --------------------------------------------------------
    print("予想ページURLを確認中...")

    prediction_maps = {}

    for venue in venues:
        expected = venue["race_numbers"]

        found = discover_prediction_pages(
            venue["venue_name"],
            expected,
        )

        prediction_maps[
            venue["venue_code"]
        ] = found

        print(
            f"  {venue['venue_name']}: "
            f"{len(found)}レース予想URL"
        )

    # --------------------------------------------------------
    # 予想ページを並列取得
    # --------------------------------------------------------
    print(
        "コメント・並び・展開情報取得中..."
    )

    prediction_results = {}

    jobs = []

    for venue_code, pages in prediction_maps.items():
        for race_no, info in pages.items():
            jobs.append(
                (
                    venue_code,
                    race_no,
                    info,
                )
            )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_prediction_page,
                (race_no, info),
            ): (
                venue_code,
                race_no,
            )
            for venue_code, race_no, info in jobs
        }

        for future in as_completed(futures):
            venue_code, race_no = futures[future]

            try:
                _, result = future.result()

            except Exception as e:
                result = {
                    "race_no": race_no,
                    "comments": {},
                    "line": [],
                    "line_groups": [],
                    "development": "",
                    "success": False,
                    "error": str(e),
                }

            prediction_results[
                (venue_code, race_no)
            ] = result

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------
    prediction_race_count = 0
    comment_count = 0
    line_race_count = 0
    development_count = 0

    for race in race_data:
        key = (
            race["venue_code"],
            race["race_no"],
        )

        prediction = prediction_results.get(
            key
        )

        if prediction:
            url_info = prediction_maps.get(
                race["venue_code"],
                {}
            ).get(race["race_no"])

            prediction_url = (
                url_info["url"]
                if url_info
                else ""
            )

            merge_prediction_into_race(
                race,
                prediction,
                prediction_url,
            )

            if len(
                prediction.get("comments", {})
            ) >= 7:
                prediction_race_count += 1

            comment_count += len(
                prediction.get(
                    "comments", {}
                )
            )

            if len(
                prediction.get("line", [])
            ) == 7:
                line_race_count += 1

            if prediction.get(
                "development"
            ):
                development_count += 1

        else:
            race["prediction_url"] = ""
            race["comments"] = {}
            race["line"] = []
            race["line_groups"] = []
            race["development"] = ""

        # ----------------------------------------------------
        # AI
        # ----------------------------------------------------
        apply_ai(race)

    # --------------------------------------------------------
    # Counts
    # --------------------------------------------------------
    rider_count = sum(
        len(race.get("riders", []))
        for race in race_data
    )

    correct_name_count = sum(
        1
        for race in race_data
        for rider in race.get("riders", [])
        if rider.get("name")
        and len(rider["name"]) >= 2
    )

    ai_race_count = sum(
        1
        for race in race_data
        if race.get("ai")
    )

    ai_rider_count = sum(
        1
        for race in race_data
        for rider in race.get("riders", [])
        if rider.get("ai_score") is not None
    )

    failed_races = sum(
        1
        for race in race_data
        if not race.get("success")
    )

    # --------------------------------------------------------
    # Venue output
    # --------------------------------------------------------
    venue_output = []

    for venue in venues:
        venue_output.append(
            {
                "venue_code": venue[
                    "venue_code"
                ],
                "venue_name": venue[
                    "venue_name"
                ],
                "race_count": len(
                    venue["race_numbers"]
                ),
            }
        )

    # --------------------------------------------------------
    # Final JSON
    # --------------------------------------------------------
    output = {
        "version": "4.7",
        "updated_at": datetime.now().isoformat(),
        "target_date": TODAY_DISPLAY,

        "venue_count": len(venues),
        "race_count": len(race_data),

        "rider_count": rider_count,
        "correct_name_count": correct_name_count,

        "prediction_race_count":
            prediction_race_count,

        "comment_count":
            comment_count,

        "line_race_count":
            line_race_count,

        "development_count":
            development_count,

        "ai_race_count":
            ai_race_count,

        "ai_rider_count":
            ai_rider_count,

        "failed_race_count":
            failed_races,

        "venues":
            venue_output,

        "races":
            race_data,
    }

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------
    os.makedirs(
        "data",
        exist_ok=True
    )

    output_path = os.path.join(
        "data",
        "today.json"
    )

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    elapsed = time.time() - started

    # --------------------------------------------------------
    # Console
    # --------------------------------------------------------
    print("==============================")
    print(" 更新完了 v4.7")
    print("==============================")
    print(f"開催場: {len(venues)}")
    print(f"レース: {len(race_data)}")
    print(f"選手: {rider_count}")
    print(
        f"正しい選手名: "
        f"{correct_name_count}"
    )

    print("【取得状況】")

    print(
        f"選手データ成功: "
        f"{rider_success}/{len(race_data)}"
    )

    print(
        f"予想情報レース: "
        f"{prediction_race_count}/{len(race_data)}"
    )

    print(
        f"選手コメント: "
        f"{comment_count}"
    )

    print(
        f"並び取得レース: "
        f"{line_race_count}"
    )

    print(
        f"展開コメント: "
        f"{development_count}"
    )

    print(
        f"AI評価レース: "
        f"{ai_race_count}"
    )

    print(
        f"AI評価選手: "
        f"{ai_rider_count}"
    )

    print(
        f"取得失敗レース: "
        f"{failed_races}"
    )

    print(
        f"処理時間: "
        f"{elapsed:.1f}秒"
    )

    print(
        f"保存先: {output_path}"
    )


if __name__ == "__main__":
    main()
