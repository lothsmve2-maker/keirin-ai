import json
import os
import re
import time
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning


# ============================================================
# KEIRIN AI DATA UPDATE v5.1
#
# v5.0:
#   ・72Rを正しく確定
#
# v5.1:
#   ・出走表の多重解析
#   ・予想表の多重解析
#   ・予想ページ再取得
#   ・コメント復旧
#   ・並び復旧
#   ・展開コメント復旧
#   ・取得失敗時の再試行
# ============================================================

warnings.filterwarnings(
    "ignore",
    category=XMLParsedAsHTMLWarning
)


# ============================================================
# 基本設定
# ============================================================

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

REQUEST_TIMEOUT = 20
RETRY_COUNT = 3

# 同時接続を少し抑えて安定性優先
MAX_WORKERS = 6


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
    name: code
    for code, name in VENUE_CODE_TO_NAME.items()
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

MARK_SCORE = {
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
# HTTP
# ============================================================

def new_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_html(url):
    if not url:
        return None

    for attempt in range(RETRY_COUNT):
        session = new_session()

        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if response.status_code == 200:
                response.encoding = (
                    response.apparent_encoding
                    or response.encoding
                )

                text = response.text

                if len(text) > 500:
                    return text

            if response.status_code in (
                404,
                410,
            ):
                return None

        except requests.RequestException:
            pass

        finally:
            session.close()

        if attempt < RETRY_COUNT - 1:
            time.sleep(
                0.7 * (attempt + 1)
            )

    return None


def soup_from_html(html):
    if not html:
        return None

    try:
        return BeautifulSoup(
            html,
            "lxml"
        )
    except Exception:
        return BeautifulSoup(
            html,
            "html.parser"
        )


def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace(
        "\xa0",
        " "
    )
    value = value.replace(
        "　",
        " "
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def unique_keep_order(values):
    result = []

    seen = set()

    for value in values:
        value = clean_text(
            value
        )

        if not value:
            continue

        if value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


# ============================================================
# 開催場
# ============================================================

def get_venues():
    url = (
        f"{BASE_URL}/keirin/RaceListInfo.do"
        f"?kaisaiBi={TODAY}"
    )

    html = get_html(url)

    if not html:
        return []

    soup = soup_from_html(
        html
    )

    if not soup:
        return []

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    found = {}

    for code, name in (
        VENUE_CODE_TO_NAME.items()
    ):
        if name in page_text:
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

        if not match:
            continue

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
# 実在R
# ============================================================

def get_winticket_races():
    url = (
        "https://www.winticket.jp/keirin/"
        "racecard/"
        f"{TODAY}"
    )

    html = get_html(
        url
    )

    if not html:
        return {}

    soup = soup_from_html(
        html
    )

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
        if venue_name not in text:
            continue

        start = text.find(
            venue_name
        )

        if start < 0:
            continue

        next_positions = []

        for other_name in (
            VENUE_NAME_TO_CODE.keys()
        ):
            if other_name == venue_name:
                continue

            pos = text.find(
                other_name,
                start + len(venue_name)
            )

            if pos >= 0:
                next_positions.append(
                    pos
                )

        end = (
            min(next_positions)
            if next_positions
            else len(text)
        )

        section = text[
            start:end
        ]

        numbers = set()

        for match in re.findall(
            r"(?<!\d)"
            r"(1[0-2]|[1-9])R"
            r"(?!\d)",
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


def fallback_prediction_races(
    venue_name
):
    url = prediction_index_url(
        venue_name
    )

    html = get_html(url)

    if not html:
        return []

    soup = soup_from_html(
        html
    )

    if not soup:
        return []

    found = set()

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

        patterns = [
            r"_(\d{1,2})\.html",
            r"raceNo=(\d{1,2})",
        ]

        for pattern in patterns:
            for match in re.findall(
                pattern,
                href
            ):
                number = int(match)

                if 1 <= number <= 12:
                    found.add(
                        number
                    )

        match = re.search(
            r"(?:^|\s)"
            r"(\d{1,2})R"
            r"(?:\s|$)",
            text
        )

        if match:
            number = int(
                match.group(1)
            )

            if 1 <= number <= 12:
                found.add(
                    number
                )

    return sorted(
        found
    )


def get_real_races(
    venues
):
    winticket = (
        get_winticket_races()
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
            winticket.get(
                code,
                []
            )
        )

        if not race_numbers:
            race_numbers = (
                fallback_prediction_races(
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
# 出走表URL
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


# ============================================================
# 数値
# ============================================================

def numbers(text):
    return [
        int(x)
        for x in re.findall(
            r"\d+",
            clean_text(text)
        )
    ]


def extract_age_period(
    text
):
    match = re.search(
        r"(\d{2})\s*/\s*(\d{2,3})",
        clean_text(text)
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2))
    )


def extract_score(
    values
):
    for value in values:
        if not re.fullmatch(
            r"\d{2,3}(?:\.\d+)?",
            value
        ):
            continue

        try:
            number = float(value)
        except Exception:
            continue

        if 60 <= number <= 130:
            return number

    return None


# ============================================================
# 選手解析
# ============================================================

PREFECTURES = {
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

GRADES = {
    "S1", "S2",
    "A1", "A2", "A3",
    "L1", "L2",
}

STYLES = {
    "逃",
    "捲",
    "追",
    "両",
    "自在",
}


def parse_rider_cells(
    values
):
    values = [
        clean_text(v)
        for v in values
    ]

    values = [
        v
        for v in values
        if v
    ]

    if not values:
        return None

    car_no = None

    for value in values[:4]:
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

        if value == str(car_no):
            continue

        if re.search(
            r"\d{2}\s*/\s*\d{2,3}",
            value
        ):
            continue

        if value in PREFECTURES:
            continue

        if value in GRADES:
            continue

        if value in STYLES:
            continue

        if re.fullmatch(
            r"\d{2,3}(?:\.\d+)?",
            value
        ):
            continue

        nums = numbers(value)

        if len(nums) >= 4:
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

    prefecture = ""

    grade = ""

    style = ""

    for value in values:

        if value in PREFECTURES:
            prefecture = value

        if value in GRADES:
            grade = value

        if value in STYLES:
            style = value

    score = extract_score(
        values
    )

    numeric_blocks = []

    for value in values:
        nums = numbers(
            value
        )

        if len(nums) >= 4:
            numeric_blocks.append(
                nums[:4]
            )

    finish = (
        numeric_blocks[0]
        if len(numeric_blocks) >= 1
        else [None] * 4
    )

    kimari = (
        numeric_blocks[1]
        if len(numeric_blocks) >= 2
        else [None] * 4
    )

    return {
        "car_no": car_no,
        "name": name,
        "age": age,
        "period": period,
        "prefecture": prefecture,
        "grade": grade,
        "style": style,
        "score": score,

        "finish_1": finish[0],
        "finish_2": finish[1],
        "finish_3": finish[2],
        "finish_out": finish[3],

        "kimari_nige": kimari[0],
        "kimari_makuri": kimari[1],
        "kimari_sashi": kimari[2],
        "kimari_mark": kimari[3],

        "comment": "",
        "prediction_mark": "",
        "prediction_score": 0,
        "ai_score": 0,
    }


def parse_riders_from_tables(
    soup
):
    candidates = []

    for table in soup.find_all(
        "table"
    ):

        rows = table.find_all(
            "tr"
        )

        local = []

        for row in rows:

            cells = row.find_all(
                ["th", "td"]
            )

            values = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            rider = parse_rider_cells(
                values
            )

            if rider:
                local.append(
                    rider
                )

        unique = {}

        for rider in local:
            unique[
                rider["car_no"]
            ] = rider

        local = list(
            unique.values()
        )

        if len(local) >= 7:
            return sorted(
                local,
                key=lambda x:
                x["car_no"]
            )[:7]

        if len(local) > len(
            candidates
        ):
            candidates = local

    return sorted(
        candidates,
        key=lambda x:
        x["car_no"]
    )[:7]


def parse_riders_from_text(
    soup
):
    """
    table解析で取れなかった場合の第2経路。
    ページ本文から車番周辺を解析する。
    """

    text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    riders = {}

    # 車番＋選手名らしき部分
    for match in re.finditer(
        r"([1-7])\s+"
        r"([^\d\s]{2,8})"
        r"\s+"
        r"(\d{2})\s*/\s*(\d{2,3})",
        text
    ):

        car_no = int(
            match.group(1)
        )

        name = clean_text(
            match.group(2)
        )

        age = int(
            match.group(3)
        )

        period = int(
            match.group(4)
        )

        if car_no in riders:
            continue

        riders[car_no] = {
            "car_no": car_no,
            "name": name,
            "age": age,
            "period": period,
            "prefecture": "",
            "grade": "",
            "style": "",
            "score": None,

            "finish_1": None,
            "finish_2": None,
            "finish_3": None,
            "finish_out": None,

            "kimari_nige": None,
            "kimari_makuri": None,
            "kimari_sashi": None,
            "kimari_mark": None,

            "comment": "",
            "prediction_mark": "",
            "prediction_score": 0,
            "ai_score": 0,
        }

    return sorted(
        riders.values(),
        key=lambda x:
        x["car_no"]
    )[:7]


def parse_riders(
    soup
):
    riders = (
        parse_riders_from_tables(
            soup
        )
    )

    if len(riders) >= 7:
        return riders

    fallback = (
        parse_riders_from_text(
            soup
        )
    )

    if len(fallback) > len(
        riders
    ):
        riders = fallback

    return riders


# ============================================================
# 出走表取得
# ============================================================

def get_race_data(
    item
):
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

    html = get_html(
        url
    )

    if not html:
        return {
            **item,
            "url": url,
            "riders": [],
            "success": False,
        }

    soup = soup_from_html(
        html
    )

    if not soup:
        return {
            **item,
            "url": url,
            "riders": [],
            "success": False,
        }

    riders = parse_riders(
        soup
    )

    # 失敗時は1回だけ完全再取得
    if len(riders) < 7:
        time.sleep(0.5)

        retry_html = get_html(
            url
        )

        if retry_html:
            retry_soup = soup_from_html(
                retry_html
            )

            retry_riders = (
                parse_riders(
                    retry_soup
                )
            )

            if len(
                retry_riders
            ) > len(riders):
                riders = retry_riders

    return {
        **item,
        "url": url,
        "riders": riders,
        "success": len(riders) >= 7,
    }


# ============================================================
# 予想ページ
# ============================================================

def race_number_from_href(
    href
):
    patterns = [
        r"_(\d{1,2})\.html",
        r"raceNo=(\d{1,2})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            href or ""
        )

        if match:
            number = int(
                match.group(1)
            )

            if 1 <= number <= 12:
                return number

    return None


def race_number_from_text(
    text
):
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

    soup = soup_from_html(
        html
    )

    if not soup:
        return {}

    found = {}

    valid_set = set(
        valid_races
    )

    # --------------------------------------------------------
    # 1R
    # --------------------------------------------------------

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

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

    # --------------------------------------------------------
    # 2R以降
    # --------------------------------------------------------

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

        number = (
            race_number_from_href(
                href
            )
        )

        if number is None:
            number = (
                race_number_from_text(
                    text
                )
            )

        if number is None:
            continue

        if number not in valid_set:
            continue

        absolute = urljoin(
            index_url,
            href
        )

        # OddsPark予想ページ以外を除外
        parsed = urlparse(
            absolute
        )

        if "oddspark.com" not in (
            parsed.netloc or ""
        ):
            continue

        found[number] = {
            "url": absolute,
            "html": None,
        }

    return dict(
        sorted(
            found.items()
        )
    )


# ============================================================
# 予想表解析
# ============================================================

def parse_prediction_table(
    soup
):
    result = {}

    # --------------------------------------------------------
    # 第1経路：table
    # --------------------------------------------------------

    for table in soup.find_all(
        "table"
    ):

        rows = table.find_all(
            "tr"
        )

        header = None

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

            joined = " ".join(
                values
            )

            if (
                "車" in joined
                and "印" in joined
                and "選手名" in joined
                and "コメント" in joined
            ):
                header = index
                break

        if header is None:
            continue

        for row in rows[
            header + 1:
        ]:

            cells = row.find_all(
                ["th", "td"]
            )

            values = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            if len(values) < 3:
                continue

            car_no = None

            for value in values[:3]:
                if re.fullmatch(
                    r"[1-7]",
                    value
                ):
                    car_no = int(
                        value
                    )
                    break

            if car_no is None:
                continue

            mark = ""

            for value in values:
                if value in MARK_SCORE:
                    mark = value
                    break

            name = ""

            for value in values:
                if (
                    value
                    and value != str(car_no)
                    and value != mark
                ):
                    if len(value) >= 2:
                        name = value
                        break

            comment_parts = []

            mark_found = False

            for value in values:

                if value == mark:
                    mark_found = True
                    continue

                if not mark_found:
                    continue

                if value == name:
                    continue

                if value:
                    comment_parts.append(
                        value
                    )

            comment = clean_text(
                " ".join(
                    comment_parts
                )
            )

            result[car_no] = {
                "name": name,
                "mark": mark,
                "comment": comment,
                "prediction_score":
                    MARK_SCORE.get(
                        mark,
                        0
                    ),
            }

        if len(result) >= 7:
            return result

    # --------------------------------------------------------
    # 第2経路：
    # 「選手名」周辺の行を直接検索
    # --------------------------------------------------------

    if len(result) < 7:

        for element in soup.find_all(
            ["tr", "div", "li"]
        ):

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) < 3:
                continue

            match = re.match(
                r"^([1-7])\s*"
                r"([◎○▲△×]?)\s*"
                r"(.+?)\s+"
                r"(.+)$",
                text
            )

            if not match:
                continue

            car_no = int(
                match.group(1)
            )

            mark = (
                match.group(2)
                or ""
            )

            rest = clean_text(
                match.group(3)
            )

            tail = clean_text(
                match.group(4)
            )

            if car_no in result:
                continue

            if len(rest) < 2:
                continue

            result[car_no] = {
                "name": rest,
                "mark": mark,
                "comment": tail,
                "prediction_score":
                    MARK_SCORE.get(
                        mark,
                        0
                    ),
            }

    return result


# ============================================================
# 並び解析
# ============================================================

def extract_line(
    soup
):
    best_line = []
    best_groups = []
    best_score = -1

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

            cars = []

            for value in values:
                if re.fullmatch(
                    r"[1-7]",
                    value
                ):
                    cars.append(
                        int(value)
                    )

            if len(cars) != 7:
                continue

            if sorted(cars) != list(
                range(1, 8)
            ):
                continue

            nearby = []

            for near_row in rows[
                index:index + 3
            ]:

                nearby += [
                    clean_text(
                        cell.get_text(
                            " ",
                            strip=True
                        )
                    )
                    for cell in near_row.find_all(
                        ["th", "td"]
                    )
                ]

            style_count = sum(
                1
                for value in nearby
                if value in STYLE_WORDS
            )

            if style_count > best_score:
                best_score = (
                    style_count
                )

                best_line = cars

                groups = []
                current = []

                for value in values:

                    if re.fullmatch(
                        r"[1-7]",
                        value
                    ):
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

                best_groups = groups

    # --------------------------------------------------------
    # 第2経路
    # --------------------------------------------------------

    if len(best_line) != 7:

        text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        # 7車番が連続する箇所
        patterns = re.findall(
            r"(?<!\d)"
            r"([1-7])\s+"
            r"([1-7])\s+"
            r"([1-7])\s+"
            r"([1-7])\s+"
            r"([1-7])\s+"
            r"([1-7])\s+"
            r"([1-7])"
            r"(?!\d)",
            text
        )

        for match in patterns:

            cars = [
                int(x)
                for x in match
            ]

            if sorted(cars) == list(
                range(1, 8)
            ):
                best_line = cars
                best_groups = [cars]
                break

    return (
        best_line,
        best_groups
    )


# ============================================================
# 展開コメント
# ============================================================

BAD_PHRASES = (
    "免責事項",
    "利用規約",
    "プライバシー",
    "Copyright",
    "お問い合わせ",
    "ログイン",
)


def looks_like_development(
    text
):
    text = clean_text(
        text
    )

    if len(text) < 15:
        return False

    if len(text) > 500:
        return False

    for phrase in BAD_PHRASES:
        if phrase in text:
            return False

    punctuation = (
        text.count("。")
        + text.count("！")
        + text.count("？")
    )

    if punctuation == 0:
        return False

    race_words = (
        "先行",
        "捲",
        "番手",
        "ライン",
        "展開",
        "仕掛け",
        "流れ",
        "逃げ",
        "追込",
        "差し",
        "自力",
    )

    return any(
        word in text
        for word in race_words
    )


def extract_development(
    soup
):
    # --------------------------------------------------------
    # 「予想の並び」付近を最優先
    # --------------------------------------------------------

    markers = soup.find_all(
        string=re.compile(
            r"予想の並び"
        )
    )

    candidates = []

    for marker in markers:

        parent = marker.parent

        if not parent:
            continue

        # 親～祖先を数段たどる
        current = parent

        for _ in range(5):

            if not current:
                break

            texts = []

            for element in current.find_all(
                ["p", "div", "td", "li"],
                limit=40
            ):

                text = clean_text(
                    element.get_text(
                        " ",
                        strip=True
                    )
                )

                if looks_like_development(
                    text
                ):
                    texts.append(
                        text
                    )

            candidates.extend(
                texts
            )

            current = current.parent

    if candidates:

        candidates = unique_keep_order(
            candidates
        )

        candidates.sort(
            key=lambda x: (
                abs(
                    len(x) - 80
                ),
                -x.count("。")
            )
        )

        if candidates:
            return candidates[0]

    # --------------------------------------------------------
    # 第2経路
    # --------------------------------------------------------

    for element in soup.find_all(
        ["p", "div", "td", "li"]
    ):

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if looks_like_development(
            text
        ):
            return text

    return ""


# ============================================================
# 予想ページ全体
# ============================================================

def parse_prediction_page(
    html,
    race_no
):
    soup = soup_from_html(
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

    line, groups = (
        extract_line(
            soup
        )
    )

    development = (
        extract_development(
            soup
        )
    )

    # 最低限どれか取れれば成功
    success = (
        len(comments) >= 7
        or len(line) == 7
        or bool(development)
    )

    return {
        "race_no": race_no,
        "comments": comments,
        "line": line,
        "line_groups": groups,
        "development": development,
        "success": success,
    }


# ============================================================
# 予想取得ジョブ
# ============================================================

def fetch_prediction(
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

    url = info[
        "url"
    ]

    html = info.get(
        "html"
    )

    if not html:
        html = get_html(
            url
        )

    if not html:
        return (
            venue_code,
            race_no,
            url,
            {
                "race_no": race_no,
                "comments": {},
                "line": [],
                "line_groups": [],
                "development": "",
                "success": False,
            }
        )

    result = parse_prediction_page(
        html,
        race_no
    )

    # --------------------------------------------------------
    # 取得内容が弱い場合、もう一度取得
    # --------------------------------------------------------

    weak = (
        len(
            result.get(
                "comments",
                {}
            )
        ) < 7
        and len(
            result.get(
                "line",
                []
            )
        ) < 7
        and not result.get(
            "development"
        )
    )

    if weak:

        time.sleep(0.5)

        retry_html = get_html(
            url
        )

        if retry_html:

            retry_result = (
                parse_prediction_page(
                    retry_html,
                    race_no
                )
            )

            old_quality = (
                len(
                    result.get(
                        "comments",
                        {}
                    )
                )
                + (
                    10
                    if len(
                        result.get(
                            "line",
                            []
                        )
                    ) == 7
                    else 0
                )
                + (
                    5
                    if result.get(
                        "development"
                    )
                    else 0
                )
            )

            new_quality = (
                len(
                    retry_result.get(
                        "comments",
                        {}
                    )
                )
                + (
                    10
                    if len(
                        retry_result.get(
                            "line",
                            []
                        )
                    ) == 7
                    else 0
                )
                + (
                    5
                    if retry_result.get(
                        "development"
                    )
                    else 0
                )
            )

            if new_quality > old_quality:
                result = retry_result

    return (
        venue_code,
        race_no,
        url,
        result
    )


# ============================================================
# AI評価
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

    # 直近着
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

    # ライン位置
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


def apply_ai(
    race
):
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

    main = ranked[0]

    opponent = (
        ranked[1]
        if len(ranked) >= 2
        else None
    )

    dark_horse = None

    if len(ranked) >= 3:

        candidates = ranked[2:6]

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

    main_score = main.get(
        "ai_score",
        0
    )

    eliminate = []

    for rider in ranked[4:]:

        if (
            main_score
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
        "main": main[
            "car_no"
        ],

        "opponent": (
            opponent[
                "car_no"
            ]
            if opponent
            else None
        ),

        "dark_horse": (
            dark_horse[
                "car_no"
            ]
            if dark_horse
            else None
        ),

        "eliminate": eliminate[:3],

        "ranking": [
            {
                "car_no":
                    rider.get(
                        "car_no"
                    ),

                "name":
                    rider.get(
                        "name"
                    ),

                "score":
                    rider.get(
                        "ai_score",
                        0
                    ),

                "mark":
                    rider.get(
                        "prediction_mark",
                        ""
                    ),
            }
            for rider in ranked
        ],
    }


# ============================================================
# 予想情報を選手へ結合
# ============================================================

def merge_prediction(
    race,
    prediction,
    prediction_url
):
    race[
        "prediction_url"
    ] = prediction_url

    race[
        "comments"
    ] = {}

    race[
        "line"
    ] = prediction.get(
        "line",
        []
    )

    race[
        "line_groups"
    ] = prediction.get(
        "line_groups",
        []
    )

    race[
        "development"
    ] = prediction.get(
        "development",
        ""
    )

    comments = prediction.get(
        "comments",
        {}
    )

    for car_no, info in (
        comments.items()
    ):

        race[
            "comments"
        ][
            str(car_no)
        ] = {
            "name":
                info.get(
                    "name",
                    ""
                ),

            "mark":
                info.get(
                    "mark",
                    ""
                ),

            "comment":
                info.get(
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
# MAIN
# ============================================================

def main():

    started = time.time()

    print(
        "=============================="
    )
    print(
        " KEIRIN AI DATA UPDATE v5.1"
    )
    print(
        "=============================="
    )
    print(
        f"対象日: {TODAY_DISPLAY}"
    )
    print(
        "=============================="
    )

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    venues = get_venues()

    if not venues:
        print(
            "開催場取得失敗"
        )
        raise SystemExit(1)

    print(
        f"開催場数: {len(venues)}"
    )

    # --------------------------------------------------------
    # 実在R
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

    if not all_races:
        print(
            "レース0件"
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
                    **item,
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

        pages = (
            discover_prediction_pages(
                name,
                venue[
                    "race_numbers"
                ]
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
    # 予想
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
                fetch_prediction,
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
                    url,
                    result
                ) = future.result()

            except Exception as e:

                venue_code = job[
                    "venue_code"
                ]

                race_no = job[
                    "race_no"
                ]

                url = job[
                    "info"
                ][
                    "url"
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
            ] = {
                "url": url,
                "data": result,
            }

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    prediction_races = 0
    comment_count = 0
    line_count = 0
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

        prediction_info = (
            prediction_results.get(
                key
            )
        )

        if prediction_info:

            prediction = (
                prediction_info[
                    "data"
                ]
            )

            merge_prediction(
                race,
                prediction,
                prediction_info[
                    "url"
                ]
            )

            comments = (
                prediction.get(
                    "comments",
                    {}
                )
            )

            if len(comments) >= 7:
                prediction_races += 1

            comment_count += len(
                comments
            )

            if len(
                prediction.get(
                    "line",
                    []
                )
            ) == 7:
                line_count += 1

            if prediction.get(
                "development"
            ):
                development_count += 1

        else:

            race[
                "prediction_url"
            ] = ""

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
    # 開催場
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
    # 完成度
    # --------------------------------------------------------

    complete = (
        len(race_data) > 0
        and rider_success == len(
            race_data
        )
        and failed_races == 0
    )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    output = {

        "version":
            "5.1",

        "updated_at":
            datetime.now().isoformat(),

        "target_date":
            TODAY_DISPLAY,

        "data_complete":
            complete,

        "venue_count":
            len(venues),

        "race_count":
            len(race_data),

        "rider_count":
            rider_count,

        "correct_name_count":
            correct_name_count,

        "prediction_race_count":
            prediction_races,

        "comment_count":
            comment_count,

        "line_race_count":
            line_count,

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
    # 原子的に保存
    # --------------------------------------------------------

    os.makedirs(
        "data",
        exist_ok=True
    )

    output_path = os.path.join(
        "data",
        "today.json"
    )

    temp_path = (
        output_path
        + ".tmp"
    )

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_path,
        output_path
    )

    elapsed = (
        time.time()
        - started
    )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    print(
        "=============================="
    )
    print(
        " 更新完了 v5.1"
    )
    print(
        "=============================="
    )

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

    print(
        "【取得状況】"
    )

    print(
        f"選手データ成功: "
        f"{rider_success}/"
        f"{len(race_data)}"
    )

    print(
        f"予想情報レース: "
        f"{prediction_races}/"
        f"{len(race_data)}"
    )

    print(
        f"選手コメント: "
        f"{comment_count}"
    )

    print(
        f"並び取得レース: "
        f"{line_count}"
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
        f"データ完全性: "
        f"{'OK' if complete else '要確認'}"
    )

    print(
        f"保存先: {output_path}"
    )


if __name__ == "__main__":
    main()
