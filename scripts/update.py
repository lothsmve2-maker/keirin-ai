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
# KEIRIN AI DATA UPDATE v5.0
# 安定版
#
# 役割
# 1. 当日の開催場を確認
# 2. 当日の実在レース数を確定
# 3. OddsPark出走表取得
# 4. OddsPark予想ページ取得
# 5. 選手コメント・予想印・並び・展開取得
# 6. AI評価
# 7. data/today.json保存
# ============================================================

warnings.filterwarnings(
    "ignore",
    category=XMLParsedAsHTMLWarning
)

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

# 当日開催レース一覧の補助情報源
WINTICKET_URL = (
    "https://www.winticket.jp/keirin/racecard/"
)

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

REQUEST_TIMEOUT = 18
RETRY_COUNT = 2
MAX_WORKERS = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
}


# ============================================================
# 開催場
# ============================================================

VENUE_CODE_TO_NAME = {
    "22": "前橋",
    "25": "大宮",
    "38": "静岡",
    "44": "大垣",
    "48": "四日市",
    "53": "奈良",
    "74": "高知",
}

VENUE_NAME_TO_CODE = {
    value: key
    for key, value in VENUE_CODE_TO_NAME.items()
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


# ============================================================
# 予想印
# ============================================================

PREDICTION_MARK_SCORE = {
    "◎": 18,
    "○": 12,
    "▲": 8,
    "△": 5,
    "×": 1,
    "": 0,
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
    "単騎",
}


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# 共通
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\xa0", " ")
    value = value.replace("　", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def to_int(value):
    try:
        return int(str(value).strip())
    except Exception:
        return None


def to_float(value):
    try:
        return float(
            str(value)
            .replace(",", "")
            .strip()
        )
    except Exception:
        return None


def get_html(url):
    if not url:
        return None

    for attempt in range(RETRY_COUNT + 1):
        try:
            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if response.status_code == 200:
                response.encoding = (
                    response.apparent_encoding
                    or response.encoding
                )

                return response.text

            if response.status_code in (404, 410):
                return None

        except requests.RequestException:
            pass

        if attempt < RETRY_COUNT:
            time.sleep(
                0.5 * (attempt + 1)
            )

    return None


def make_soup(html):
    if not html:
        return None

    try:
        return BeautifulSoup(
            html,
            "lxml"
        )
    except Exception:
        try:
            return BeautifulSoup(
                html,
                "html.parser"
            )
        except Exception:
            return None


# ============================================================
# OddsPark 開催場
# ============================================================

def get_oddspark_venues():
    url = (
        f"{BASE_URL}/keirin/RaceListInfo.do"
        f"?kaisaiBi={TODAY}"
    )

    html = get_html(url)

    if not html:
        return []

    soup = make_soup(html)

    if not soup:
        return []

    text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    found = {}

    for code, name in (
        VENUE_CODE_TO_NAME.items()
    ):
        if name in text:
            found[code] = name

    for a in soup.find_all(
        "a",
        href=True
    ):
        href = a.get(
            "href",
            ""
        )

        match = re.search(
            r"joCode=(\d+)",
            href
        )

        if match:
            code = match.group(1)

            if code in VENUE_CODE_TO_NAME:
                found[code] = (
                    VENUE_CODE_TO_NAME[code]
                )

    return [
        {
            "venue_code": code,
            "venue_name": found[code],
        }
        for code in VENUE_CODE_TO_NAME
        if code in found
    ]


# ============================================================
# 当日レース数
#
# v5.0の重要変更
#
# OddsParkの予想リンク数は使わない。
# 当日レースカード一覧から実在Rを確定する。
# ============================================================

def get_real_races_from_winticket():
    url = (
        f"{WINTICKET_URL}"
        f"{TODAY}"
    )

    html = get_html(url)

    if not html:
        return {}

    soup = make_soup(html)

    if not soup:
        return {}

    text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    result = {}

    for venue_name, code in (
        VENUE_NAME_TO_CODE.items()
    ):
        # 開催場名がページに存在するか
        if venue_name not in text:
            continue

        # ページ全体から直接数字を拾わない。
        # 開催場付近のリンクを優先。
        numbers = set()

        for a in soup.find_all(
            "a",
            href=True
        ):
            a_text = clean_text(
                a.get_text(
                    " ",
                    strip=True
                )
            )

            href = a.get(
                "href",
                ""
            )

            if venue_name not in (
                a_text
                + " "
                + href
            ):
                continue

            for match in re.findall(
                r"(?:race|r|R)[=/](\d{1,2})",
                href
            ):
                number = int(match)

                if 1 <= number <= 12:
                    numbers.add(number)

        # WINTICKETのページ構造上、
        # venue名から次の開催場までの範囲を取得
        venue_pos = text.find(
            venue_name
        )

        if venue_pos >= 0:
            next_positions = []

            for other_name in (
                VENUE_NAME_TO_CODE.keys()
            ):
                if other_name == venue_name:
                    continue

                pos = text.find(
                    other_name,
                    venue_pos + len(venue_name)
                )

                if pos >= 0:
                    next_positions.append(
                        pos
                    )

            end_pos = (
                min(next_positions)
                if next_positions
                else len(text)
            )

            section = text[
                venue_pos:end_pos
            ]

            for match in re.findall(
                r"(?<!\d)(1[0-2]|[1-9])R(?!\d)",
                section
            ):
                number = int(match)

                if 1 <= number <= 12:
                    numbers.add(number)

        if numbers:
            result[code] = sorted(
                numbers
            )

    return result


# ============================================================
# 補助：開催R数を固定候補から確定
# ============================================================

def fallback_race_numbers(
    venue_name
):
    """
    WINTICKETが一時的に取得できない場合の保険。

    2026-10-07だけではなく、
    OddsPark予想ページから候補を取得する。
    """

    slug = VENUE_NAME_TO_SLUG.get(
        venue_name
    )

    if not slug:
        return []

    url = (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/"
        f"{TODAY[4:]}.html"
    )

    html = get_html(url)

    if not html:
        return []

    soup = make_soup(html)

    if not soup:
        return []

    found = set()

    # ページ内リンク
    for a in soup.find_all(
        "a",
        href=True
    ):
        href = a.get(
            "href",
            ""
        )

        text = clean_text(
            a.get_text(
                " ",
                strip=True
            )
        )

        matches = []

        matches += re.findall(
            r"_(\d{1,2})\.html",
            href
        )

        matches += re.findall(
            r"(?:^|\s)(\d{1,2})R(?:\s|$)",
            text
        )

        for match in matches:
            number = int(match)

            if 1 <= number <= 12:
                found.add(number)

    # 1Rは一覧ページ自身
    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    if re.search(
        r"(?:^|\s)1R(?:\s|$)",
        page_text
    ):
        found.add(1)

    return sorted(found)


def get_real_races(venues):
    """
    最終的に採用する実在レース。
    """

    winticket_races = (
        get_real_races_from_winticket()
    )

    all_races = []

    for venue in venues:
        code = venue[
            "venue_code"
        ]

        name = venue[
            "venue_name"
        ]

        race_numbers = (
            winticket_races.get(
                code,
                []
            )
        )

        # 補助取得
        if not race_numbers:
            race_numbers = (
                fallback_race_numbers(
                    name
                )
            )

        venue[
            "race_numbers"
        ] = race_numbers

        print(
            f"  {code} {name}: "
            f"{len(race_numbers)}レース"
        )

        for race_no in race_numbers:
            all_races.append(
                {
                    "venue_code": code,
                    "venue_name": name,
                    "race_no": race_no,
                }
            )

    return all_races


# ============================================================
# OddsPark 出走表
# ============================================================

def race_detail_url(
    venue_code,
    race_no
):
    return (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


def extract_age_period(text):
    match = re.search(
        r"(\d{2})\s*/\s*(\d{2,3})",
        clean_text(text)
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2)),
    )


def extract_numbers(text):
    return [
        int(x)
        for x in re.findall(
            r"\d+",
            clean_text(text)
        )
    ]


def parse_finish(text):
    nums = extract_numbers(
        text
    )

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
    nums = extract_numbers(
        text
    )

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
    values = [
        clean_text(x)
        for x in cells
    ]

    if not values:
        return None

    car_no = None

    for value in values[:3]:
        if re.fullmatch(
            r"[1-7]",
            value
        ):
            car_no = int(value)
            break

    if car_no is None:
        return None

    name = ""

    for value in values:
        if not value:
            continue

        if value == str(car_no):
            continue

        if re.search(
            r"\d{2}\s*/\s*\d{2,3}",
            value
        ):
            continue

        if re.fullmatch(
            r"\d{2,3}(?:\.\d+)?",
            value
        ):
            continue

        if len(
            extract_numbers(value)
        ) >= 3:
            continue

        if len(value) >= 2:
            name = value
            break

    if not name:
        return None

    age = None
    period = None

    for value in values:
        a, p = extract_age_period(
            value
        )

        if a is not None:
            age = a
            period = p
            break

    prefectures = {
        "北海道", "青森", "岩手", "宮城",
        "秋田", "山形", "福島", "茨城",
        "栃木", "群馬", "埼玉", "千葉",
        "東京", "神奈川", "新潟", "富山",
        "石川", "福井", "山梨", "長野",
        "岐阜", "静岡", "愛知", "三重",
        "滋賀", "京都", "大阪", "兵庫",
        "奈良", "和歌山", "鳥取", "島根",
        "岡山", "広島", "山口", "徳島",
        "香川", "愛媛", "高知", "福岡",
        "佐賀", "長崎", "熊本", "大分",
        "宮崎", "鹿児島", "沖縄",
    }

    grades = {
        "S1", "S2",
        "A1", "A2", "A3",
        "L1", "L2",
    }

    styles = {
        "逃",
        "捲",
        "追",
        "両",
        "自在",
    }

    prefecture = ""
    grade = ""
    style = ""
    score = None

    for value in values:
        if value in prefectures:
            prefecture = value

        if value in grades:
            grade = value

        if value in styles:
            style = value

        if re.fullmatch(
            r"\d{2,3}(?:\.\d+)?",
            value
        ):
            number = to_float(value)

            if (
                number is not None
                and 60 <= number <= 130
            ):
                score = number

    finish = {}
    kimari = {}

    for value in values:
        nums = extract_numbers(
            value
        )

        if len(nums) >= 4:
            if not finish:
                finish = parse_finish(
                    value
                )
            elif not kimari:
                kimari = parse_kimari(
                    value
                )

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
    best = []

    for table in soup.find_all(
        "table"
    ):
        riders = []

        for row in table.find_all(
            "tr"
        ):
            cells = row.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            rider = parse_rider_row(
                [
                    cell.get_text(
                        " ",
                        strip=True
                    )
                    for cell in cells
                ]
            )

            if rider:
                riders.append(
                    rider
                )

        car_numbers = {
            r["car_no"]
            for r in riders
            if r.get("car_no")
        }

        if len(car_numbers) >= 7:
            return sorted(
                riders,
                key=lambda x:
                x["car_no"]
            )[:7]

        if len(riders) > len(best):
            best = riders

    return sorted(
        best,
        key=lambda x:
        x.get("car_no") or 99
    )[:7]


def get_race_data(item):
    code = item[
        "venue_code"
    ]

    name = item[
        "venue_name"
    ]

    race_no = item[
        "race_no"
    ]

    url = race_detail_url(
        code,
        race_no
    )

    html = get_html(url)

    if not html:
        return {
            "venue_code": code,
            "venue_name": name,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "success": False,
        }

    soup = make_soup(html)

    if not soup:
        return {
            "venue_code": code,
            "venue_name": name,
            "race_no": race_no,
            "url": url,
            "riders": [],
            "success": False,
        }

    riders = extract_riders(
        soup
    )

    return {
        "venue_code": code,
        "venue_name": name,
        "race_no": race_no,
        "url": url,
        "riders": riders,
        "success": len(riders) >= 7,
    }


# ============================================================
# 予想ページ
# ============================================================

def prediction_index_url(
    venue_name
):
    slug = VENUE_NAME_TO_SLUG.get(
        venue_name
    )

    if not slug:
        return None

    return (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/"
        f"{TODAY[4:]}.html"
    )


def race_no_from_url(url):
    patterns = [
        r"_(\d{1,2})\.html",
        r"raceNo=(\d{1,2})",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            url or ""
        )

        if match:
            number = int(
                match.group(1)
            )

            if 1 <= number <= 12:
                return number

    return None


def race_no_from_text(text):
    match = re.search(
        r"(?:^|\s)"
        r"(\d{1,2})R"
        r"(?:\s|$)",
        clean_text(text)
    )

    if not match:
        return None

    number = int(
        match.group(1)
    )

    if 1 <= number <= 12:
        return number

    return None


def discover_prediction_pages(
    venue_name,
    valid_races
):
    index_url = prediction_index_url(
        venue_name
    )

    if not index_url:
        return {}

    html = get_html(
        index_url
    )

    if not html:
        return {}

    soup = make_soup(html)

    if not soup:
        return {}

    found = {}

    valid_set = set(
        valid_races
    )

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    # 1R
    if (
        1 in valid_set
        and re.search(
            r"(?:^|\s)1R(?:\s|$)",
            page_text
        )
    ):
        found[1] = {
            "url": index_url,
            "html": html,
        }

    # 2R以降
    for a in soup.find_all(
        "a",
        href=True
    ):
        href = a.get(
            "href",
            ""
        )

        text = clean_text(
            a.get_text(
                " ",
                strip=True
            )
        )

        race_no = (
            race_no_from_url(
                href
            )
        )

        if race_no is None:
            race_no = (
                race_no_from_text(
                    text
                )
            )

        if race_no is None:
            continue

        if race_no not in valid_set:
            continue

        if race_no == 1:
            continue

        found[race_no] = {
            "url": urljoin(
                index_url,
                href
            ),
            "html": None,
        }

    return dict(
        sorted(
            found.items()
        )
    )


# ============================================================
# 予想表
# ============================================================

def is_prediction_header(
    values
):
    joined = " ".join(values)

    return (
        "車" in joined
        and "印" in joined
        and "選手名" in joined
        and "コメント" in joined
    )


def parse_prediction_table(
    soup
):
    result = {}

    for table in soup.find_all(
        "table"
    ):
        rows = table.find_all(
            "tr"
        )

        header_index = None

        for index, row in enumerate(
            rows
        ):
            values = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in row.find_all(
                    ["th", "td"]
                )
            ]

            if is_prediction_header(
                values
            ):
                header_index = index
                break

        if header_index is None:
            continue

        for row in rows[
            header_index + 1:
        ]:
            values = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in row.find_all(
                    ["th", "td"]
                )
            ]

            if len(values) < 3:
                continue

            car_no = None

            for value in values[:2]:
                if re.fullmatch(
                    r"[1-7]",
                    value
                ):
                    car_no = int(value)
                    break

            if car_no is None:
                continue

            mark = ""

            for value in values:
                if value in PREDICTION_MARK_SCORE:
                    mark = value
                    break

            name = (
                values[2]
                if len(values) >= 3
                else ""
            )

            comment = ""

            if len(values) >= 4:
                comment = clean_text(
                    " ".join(
                        x
                        for x in values[3:]
                        if x
                    )
                )

            result[car_no] = {
                "name": name,
                "mark": mark,
                "comment": comment,
                "prediction_score":
                    PREDICTION_MARK_SCORE.get(
                        mark,
                        0
                    ),
            }

        if len(result) >= 7:
            return result

    return result


# ============================================================
# 並び
# ============================================================

def is_car(value):
    return bool(
        re.fullmatch(
            r"[1-7]",
            clean_text(value)
        )
    )


def extract_line(soup):
    best = None

    for table in soup.find_all(
        "table"
    ):
        rows = table.find_all(
            "tr"
        )

        for index, row in enumerate(
            rows
        ):
            values = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in row.find_all(
                    ["th", "td"]
                )
            ]

            cars = [
                int(v)
                for v in values
                if is_car(v)
            ]

            if len(cars) != 7:
                continue

            if set(cars) != set(
                range(1, 8)
            ):
                continue

            nearby = []

            for next_row in rows[
                index:index + 3
            ]:
                nearby.extend(
                    clean_text(
                        cell.get_text(
                            " ",
                            strip=True
                        )
                    )
                    for cell in next_row.find_all(
                        ["th", "td"]
                    )
                )

            style_count = sum(
                1
                for value in nearby
                if value in STYLE_WORDS
            )

            candidate = {
                "score": style_count,
                "cars": cars,
                "values": values,
            }

            if (
                best is None
                or candidate["score"]
                > best["score"]
            ):
                best = candidate

    if not best:
        return [], []

    cars = best["cars"]
    values = best["values"]

    groups = []
    current = []

    for value in values:
        if is_car(value):
            current.append(
                int(value)
            )
        else:
            if current:
                groups.append(
                    current
                )
                current = []

    if current:
        groups.append(
            current
        )

    flat = [
        car
        for group in groups
        for car in group
    ]

    if sorted(flat) != list(
        range(1, 8)
    ):
        groups = [cars]

    return cars, groups


# ============================================================
# 展開
# ============================================================

def extract_development(
    soup
):
    candidates = []

    body = (
        soup.body
        if soup.body
        else soup
    )

    for element in body.find_all(
        ["p", "div", "td", "li"]
    ):
        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        if len(text) < 25:
            continue

        if len(text) > 400:
            continue

        if "予想の並び" in text:
            continue

        if "免責事項" in text:
            continue

        punctuation = (
            text.count("。")
            + text.count("！")
            + text.count("？")
        )

        if punctuation >= 1:
            candidates.append(
                text
            )

    if not candidates:
        return ""

    candidates.sort(
        key=lambda x: (
            abs(
                len(x) - 80
            ),
            -x.count("。")
        )
    )

    return candidates[0]


def parse_prediction_page(
    html,
    race_no
):
    soup = make_soup(
        html
    )

    if not soup:
        return {
            "race_no": race_no,
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "success": False,
        }

    comments = (
        parse_prediction_table(
            soup
        )
    )

    line, line_groups = (
        extract_line(
            soup
        )
    )

    development = (
        extract_development(
            soup
        )
    )

    return {
        "race_no": race_no,
        "comments": comments,
        "line": line,
        "line_groups": line_groups,
        "development": development,
        "success": (
            len(comments) >= 7
            or len(line) == 7
            or bool(development)
        ),
    }


def fetch_prediction_job(
    job
):
    venue_code = job[
        "venue_code"
    ]

    race_no = job[
        "race_no"
    ]

    info = job[
        "info"
    ]

    html = info.get(
        "html"
    )

    if not html:
        html = get_html(
            info["url"]
        )

    if not html:
        return (
            venue_code,
            race_no,
            {
                "race_no": race_no,
                "comments": {},
                "line": [],
                "line_groups": [],
                "development": "",
                "success": False,
            }
        )

    result = (
        parse_prediction_page(
            html,
            race_no
        )
    )

    return (
        venue_code,
        race_no,
        result
    )


# ============================================================
# MERGE
# ============================================================

def merge_prediction(
    race,
    prediction,
    prediction_url
):
    race["prediction_url"] = (
        prediction_url
    )

    race["comments"] = {}

    race["line"] = prediction.get(
        "line",
        []
    )

    race["line_groups"] = (
        prediction.get(
            "line_groups",
            []
        )
    )

    race["development"] = (
        prediction.get(
            "development",
            ""
        )
    )

    comments = prediction.get(
        "comments",
        {}
    )

    for car_no, info in (
        comments.items()
    ):
        race["comments"][
            str(car_no)
        ] = {
            "name": info.get(
                "name",
                ""
            ),
            "mark": info.get(
                "mark",
                ""
            ),
            "comment": info.get(
                "comment",
                ""
            ),
            "prediction_score":
                info.get(
                    "prediction_score",
                    0
                ),
        }

    for rider in race.get(
        "riders",
        []
    ):
        car_no = rider.get(
            "car_no"
        )

        info = comments.get(
            car_no
        )

        if not info:
            continue

        rider[
            "prediction_mark"
        ] = info.get(
            "mark",
            ""
        )

        rider[
            "prediction_score"
        ] = info.get(
            "prediction_score",
            0
        )

        rider[
            "comment"
        ] = info.get(
            "comment",
            ""
        )


# ============================================================
# AI
# ============================================================

def calculate_ai_score(
    rider,
    race
):
    score = 0.0

    # 競走得点
    rider_score = rider.get(
        "score"
    )

    if rider_score is not None:
        score += max(
            0,
            min(
                30,
                (
                    rider_score - 70
                ) * 1.2
            )
        )

    # 直近成績
    score += (
        rider.get(
            "finish_1"
        ) or 0
    ) * 1.8

    score += (
        rider.get(
            "finish_2"
        ) or 0
    ) * 0.9

    score += (
        rider.get(
            "finish_3"
        ) or 0
    ) * 0.4

    # 決まり手
    score += (
        rider.get(
            "kimari_nige"
        ) or 0
    ) * 0.8

    score += (
        rider.get(
            "kimari_makuri"
        ) or 0
    ) * 1.0

    score += (
        rider.get(
            "kimari_sashi"
        ) or 0
    ) * 1.0

    score += (
        rider.get(
            "kimari_mark"
        ) or 0
    ) * 0.4

    # 予想印
    score += rider.get(
        "prediction_score",
        0
    )

    # ライン
    car_no = rider.get(
        "car_no"
    )

    line = race.get(
        "line",
        []
    )

    if car_no in line:
        position = line.index(
            car_no
        )

        if position == 0:
            score += 2

        elif position == 1:
            score += 4

        elif position == 2:
            score += 2

    return round(
        score,
        2
    )


def apply_ai(race):
    riders = race.get(
        "riders",
        []
    )

    if not riders:
        race["ai"] = {
            "main": None,
            "opponent": None,
            "dark_horse": None,
            "eliminate": [],
            "ranking": [],
        }
        return

    for rider in riders:
        rider[
            "ai_score"
        ] = calculate_ai_score(
            rider,
            race
        )

    ranked = sorted(
        riders,
        key=lambda x: (
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "score"
            ) or 0
        ),
        reverse=True
    )

    main = (
        ranked[0]
        if ranked
        else None
    )

    opponent = (
        ranked[1]
        if len(ranked) >= 2
        else None
    )

    dark_horse = None

    candidates = ranked[2:6]

    if candidates:
        candidates.sort(
            key=lambda x: (
                x.get(
                    "prediction_score",
                    0
                ),
                x.get(
                    "kimari_makuri"
                ) or 0,
                x.get(
                    "kimari_sashi"
                ) or 0,
            ),
            reverse=True
        )

        dark_horse = candidates[0]

    leader_score = (
        main.get(
            "ai_score",
            0
        )
        if main
        else 0
    )

    eliminate = []

    for rider in ranked[4:]:
        if (
            leader_score
            - rider.get(
                "ai_score",
                0
            )
            >= 8
        ):
            eliminate.append(
                rider["car_no"]
            )

    race["ai"] = {
        "main": (
            main["car_no"]
            if main
            else None
        ),
        "opponent": (
            opponent["car_no"]
            if opponent
            else None
        ),
        "dark_horse": (
            dark_horse["car_no"]
            if dark_horse
            else None
        ),
        "eliminate": eliminate[:3],
        "ranking": [
            {
                "car_no": rider.get(
                    "car_no"
                ),
                "name": rider.get(
                    "name"
                ),
                "score": rider.get(
                    "ai_score",
                    0
                ),
                "mark": rider.get(
                    "prediction_mark",
                    ""
                ),
            }
            for rider in ranked
        ],
    }


# ============================================================
# MAIN
# ============================================================

def main():
    started = time.time()

    print("==============================")
    print(" KEIRIN AI DATA UPDATE v5.0")
    print("==============================")
    print(
        f"対象日: {TODAY_DISPLAY}"
    )
    print("==============================")

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    venues = get_oddspark_venues()

    if not venues:
        print(
            "開催場を取得できませんでした"
        )
        raise SystemExit(1)

    print(
        f"開催場数: {len(venues)}"
    )

    # --------------------------------------------------------
    # 実在R確定
    # --------------------------------------------------------

    print(
        "実在レースを確認中..."
    )

    all_races = get_real_races(
        venues
    )

    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    # --------------------------------------------------------
    # 安全チェック
    # --------------------------------------------------------

    if len(all_races) == 0:
        print(
            "レースが0件のため中止"
        )
        raise SystemExit(1)

    # --------------------------------------------------------
    # 出走表
    # --------------------------------------------------------

    print(
        "選手データ取得中..."
    )

    race_data = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                get_race_data,
                item
            ): item
            for item in all_races
        }

        for future in as_completed(
            futures
        ):
            item = futures[
                future
            ]

            try:
                result = future.result()

            except Exception as e:
                result = {
                    "venue_code":
                        item[
                            "venue_code"
                        ],
                    "venue_name":
                        item[
                            "venue_name"
                        ],
                    "race_no":
                        item[
                            "race_no"
                        ],
                    "url":
                        race_detail_url(
                            item[
                                "venue_code"
                            ],
                            item[
                                "race_no"
                            ]
                        ),
                    "riders": [],
                    "success": False,
                    "error": str(e),
                }

            race_data.append(
                result
            )

    race_data.sort(
        key=lambda x: (
            int(
                x[
                    "venue_code"
                ]
            ),
            x[
                "race_no"
            ]
        )
    )

    rider_success = sum(
        1
        for race in race_data
        if len(
            race.get(
                "riders",
                []
            )
        ) >= 7
    )

    print(
        f"選手データ取得結果: "
        f"{rider_success}/"
        f"{len(race_data)}"
    )

    # --------------------------------------------------------
    # 予想URL
    # --------------------------------------------------------

    print(
        "予想ページURLを確認中..."
    )

    prediction_maps = {}

    for venue in venues:
        code = venue[
            "venue_code"
        ]

        name = venue[
            "venue_name"
        ]

        valid_races = venue[
            "race_numbers"
        ]

        pages = (
            discover_prediction_pages(
                name,
                valid_races
            )
        )

        prediction_maps[
            code
        ] = pages

        print(
            f"  {code} {name}: "
            f"{len(pages)}レース予想URL"
        )

    # --------------------------------------------------------
    # 予想情報
    # --------------------------------------------------------

    print(
        "コメント・並び・展開情報取得中..."
    )

    jobs = []

    for venue_code, pages in (
        prediction_maps.items()
    ):
        for race_no, info in (
            pages.items()
        ):
            jobs.append(
                {
                    "venue_code":
                        venue_code,
                    "race_no":
                        race_no,
                    "info":
                        info,
                }
            )

    prediction_results = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_prediction_job,
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
                (
                    venue_code,
                    race_no,
                    result
                ) = future.result()

            except Exception as e:
                venue_code = job[
                    "venue_code"
                ]

                race_no = job[
                    "race_no"
                ]

                result = {
                    "race_no":
                        race_no,
                    "comments": {},
                    "line": [],
                    "line_groups": [],
                    "development": "",
                    "success": False,
                    "error":
                        str(e),
                }

            prediction_results[
                (
                    venue_code,
                    race_no
                )
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
            race[
                "venue_code"
            ],
            race[
                "race_no"
            ]
        )

        prediction = (
            prediction_results.get(
                key
            )
        )

        page_info = (
            prediction_maps
            .get(
                race[
                    "venue_code"
                ],
                {}
            )
            .get(
                race[
                    "race_no"
                ]
            )
        )

        prediction_url = ""

        if page_info:
            prediction_url = (
                page_info[
                    "url"
                ]
            )

        if prediction:

            merge_prediction(
                race,
                prediction,
                prediction_url
            )

            if len(
                prediction.get(
                    "comments",
                    {}
                )
            ) >= 7:
                prediction_race_count += 1

            comment_count += len(
                prediction.get(
                    "comments",
                    {}
                )
            )

            if len(
                prediction.get(
                    "line",
                    []
                )
            ) == 7:
                line_race_count += 1

            if prediction.get(
                "development"
            ):
                development_count += 1

        else:

            race[
                "prediction_url"
            ] = prediction_url

            race[
                "comments"
            ] = {}

            race[
                "line"
            ] = []

            race[
                "line_groups"
            ] = []

            race[
                "development"
            ] = ""

        apply_ai(
            race
        )

    # --------------------------------------------------------
    # 集計
    # --------------------------------------------------------

    rider_count = sum(
        len(
            race.get(
                "riders",
                []
            )
        )
        for race in race_data
    )

    correct_name_count = sum(
        1
        for race in race_data
        for rider in race.get(
            "riders",
            []
        )
        if rider.get(
            "name"
        )
        and len(
            rider[
                "name"
            ]
        ) >= 2
    )

    ai_race_count = sum(
        1
        for race in race_data
        if race.get(
            "ai"
        )
    )

    ai_rider_count = sum(
        1
        for race in race_data
        for rider in race.get(
            "riders",
            []
        )
        if rider.get(
            "ai_score"
        ) is not None
    )

    failed_races = sum(
        1
        for race in race_data
        if not race.get(
            "success"
        )
    )

    # --------------------------------------------------------
    # 開催場出力
    # --------------------------------------------------------

    venue_output = []

    for venue in venues:
        venue_output.append(
            {
                "venue_code":
                    venue[
                        "venue_code"
                    ],
                "venue_name":
                    venue[
                        "venue_name"
                    ],
                "race_count":
                    len(
                        venue[
                            "race_numbers"
                        ]
                    ),
                "race_numbers":
                    venue[
                        "race_numbers"
                    ],
            }
        )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    output = {
        "version": "5.0",

        "updated_at":
            datetime.now().isoformat(),

        "target_date":
            TODAY_DISPLAY,

        "venue_count":
            len(venues),

        "race_count":
            len(race_data),

        "rider_count":
            rider_count,

        "correct_name_count":
            correct_name_count,

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
    # 保存
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
            indent=2
        )

    elapsed = (
        time.time()
        - started
    )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    print("==============================")
    print(" 更新完了 v5.0")
    print("==============================")

    print(
        f"開催場: {len(venues)}"
    )

    print(
        f"レース: {len(race_data)}"
    )

    print(
        f"選手: {rider_count}"
    )

    print(
        f"正しい選手名: "
        f"{correct_name_count}"
    )

    print("【取得状況】")

    print(
        f"選手データ成功: "
        f"{rider_success}/"
        f"{len(race_data)}"
    )

    print(
        f"予想情報レース: "
        f"{prediction_race_count}/"
        f"{len(race_data)}"
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
