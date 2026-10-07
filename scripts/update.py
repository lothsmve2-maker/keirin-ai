import json
import os
import re
import time
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning


# ============================================================
# KEIRIN AI DATA UPDATE v5.4
# ============================================================

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

TIMEOUT = 15
RETRIES = 1
MAX_WORKERS = 8

# XMLParsedAsHTMLWarning は取得先によって出ることがあるため抑制
warnings.filterwarnings(
    "ignore",
    category=XMLParsedAsHTMLWarning
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}


# ============================================================
# 開催場
# ============================================================

VENUES = {
    "22": ("前橋", "maebashi"),
    "25": ("大宮", "omiya"),
    "38": ("静岡", "shizuoka"),
    "44": ("大垣", "ogaki"),
    "48": ("四日市", "yokkaichi"),
    "53": ("奈良", "nara"),
    "74": ("高知", "kochi"),
}


# ============================================================
# HTTP
# ============================================================

def get_html(url):
    if not url:
        return None

    for attempt in range(RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            if response.status_code == 200:
                if len(response.text) > 300:
                    response.encoding = (
                        response.apparent_encoding
                        or response.encoding
                    )
                    return response.text

        except requests.RequestException:
            pass

        if attempt < RETRIES:
            time.sleep(0.4)

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
        return BeautifulSoup(
            html,
            "html.parser"
        )


def clean(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("　", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# URL
# ============================================================

def race_url(venue_code, race_no):
    return (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


def prediction_url(slug, race_no):
    base = (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/{TODAY[4:]}"
    )

    if race_no == 1:
        return base + ".html"

    return base + f"_{race_no}.html"


# ============================================================
# 出走表判定
# ============================================================

def extract_car_numbers(sp):
    """
    ページ内から車番1～7を抽出。
    5車立てにも対応。
    """

    found = set()

    if not sp:
        return found

    for table in sp.find_all("table"):

        rows = table.find_all("tr")

        for tr in rows:

            cells = tr.find_all(
                ["th", "td"]
            )

            values = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in cells
            ]

            for value in values:

                if re.fullmatch(
                    r"[1-7]",
                    value
                ):
                    found.add(
                        int(value)
                    )

    return found


def is_race_page(sp):
    """
    実在レース判定。

    競輪では5車立て、6車立て、7車立てが
    存在するため5～7車を有効とする。
    """

    if not sp:
        return False

    # --------------------------------------------
    # テーブル単位で判定
    # --------------------------------------------

    for table in sp.find_all("table"):

        rows = table.find_all("tr")

        cars = set()

        for tr in rows:

            cells = tr.find_all(
                ["th", "td"]
            )

            values = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in cells
            ]

            for value in values:

                if re.fullmatch(
                    r"[1-7]",
                    value
                ):
                    cars.add(
                        int(value)
                    )

        if 5 <= len(cars) <= 7:

            # 選手名などの存在も確認
            table_text = clean(
                table.get_text(
                    " ",
                    strip=True
                )
            )

            if (
                "選手名" in table_text
                or "競走得点" in table_text
                or "府県" in table_text
            ):
                return True

    # --------------------------------------------
    # ページ全体の予備判定
    # --------------------------------------------

    text = clean(
        sp.get_text(
            " ",
            strip=True
        )
    )

    if (
        "車番" in text
        and "選手名" in text
    ):
        nums = set(
            int(x)
            for x in re.findall(
                r"(?<!\d)[1-7](?!\d)",
                text
            )
        )

        if 5 <= len(nums) <= 7:
            return True

    return False


# ============================================================
# 出走表解析
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
}


def parse_age_period(text):
    match = re.search(
        r"(\d{2})\s*/\s*(\d{2,3})",
        text
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2))
    )


def parse_rider(values):

    values = [
        clean(v)
        for v in values
    ]

    car_no = None

    for value in values:

        if re.fullmatch(
            r"[1-7]",
            value
        ):
            car_no = int(value)
            break

    if car_no is None:
        return None

    age = None
    period = None

    for value in values:

        a, p = parse_age_period(value)

        if a is not None:
            age = a
            period = p
            break

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

        if value in PREFECTURES:
            continue

        if value in GRADES:
            continue

        if value in STYLES:
            continue

        if re.fullmatch(
            r"\d+(?:\.\d+)?",
            value
        ):
            continue

        if len(value) >= 2:
            name = value
            break

    if not name:
        return None

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

    score = None

    for value in values:

        if not re.fullmatch(
            r"\d{2,3}(?:\.\d+)?",
            value
        ):
            continue

        try:
            number = float(value)

            if 60 <= number <= 130:
                score = number
                break

        except ValueError:
            pass

    blocks = []

    for value in values:

        numbers = [
            int(x)
            for x in re.findall(
                r"\d+",
                value
            )
        ]

        if len(numbers) >= 4:
            blocks.append(
                numbers[:4]
            )

    finish = (
        blocks[0]
        if len(blocks) >= 1
        else [None] * 4
    )

    kimari = (
        blocks[1]
        if len(blocks) >= 2
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


def parse_riders(sp):

    best = {}

    for table in sp.find_all("table"):

        local = {}

        for tr in table.find_all("tr"):

            cells = tr.find_all(
                ["th", "td"]
            )

            values = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in cells
            ]

            rider = parse_rider(
                values
            )

            if rider:
                local[
                    rider["car_no"]
                ] = rider

        if len(local) > len(best):
            best = local

        # 5～7車を正式採用
        if 5 <= len(local) <= 7:

            return [
                local[i]
                for i in sorted(local)
            ]

    return [
        best[i]
        for i in sorted(best)
    ]


# ============================================================
# レースページ取得
# ============================================================

def fetch_race(item):

    url = race_url(
        item["venue_code"],
        item["race_no"]
    )

    html = get_html(url)
    sp = make_soup(html)

    if not sp:
        return {
            **item,
            "url": url,
            "riders": [],
            "success": False,
        }

    if not is_race_page(sp):
        return {
            **item,
            "url": url,
            "riders": [],
            "success": False,
        }

    riders = parse_riders(sp)

    valid = 5 <= len(riders) <= 7

    return {
        **item,
        "url": url,
        "riders": riders,
        "success": valid,
    }


# ============================================================
# 実在レース確定
# ============================================================

def discover_real_races(venue):

    jobs = []

    for race_no in range(1, 13):

        jobs.append({
            "venue_code": venue["code"],
            "venue_name": venue["name"],
            "venue_slug": venue["slug"],
            "race_no": race_no,
        })

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                fetch_race,
                job
            )
            for job in jobs
        ]

        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

                if result["riders"]:
                    results.append(result)

            except Exception:
                pass

    results.sort(
        key=lambda x: x["race_no"]
    )

    return results


# ============================================================
# 予想表
# ============================================================

MARK_SCORE = {
    "◎": 18,
    "○": 12,
    "▲": 8,
    "△": 5,
    "×": 1,
}


def parse_prediction_table(sp):

    result = {}

    for table in sp.find_all("table"):

        rows = table.find_all("tr")

        header = None

        for index, tr in enumerate(rows):

            text = clean(
                tr.get_text(
                    " ",
                    strip=True
                )
            )

            if (
                "車" in text
                and "印" in text
                and "選手名" in text
                and "コメント" in text
            ):
                header = index
                break

        if header is None:
            continue

        for tr in rows[header + 1:]:

            cells = tr.find_all(
                ["th", "td"]
            )

            values = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in cells
            ]

            if not values:
                continue

            car_no = None

            for value in values:

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

                if value in MARK_SCORE:
                    mark = value
                    break

            name = ""

            mark_index = -1

            if mark:

                try:
                    mark_index = values.index(
                        mark
                    )

                except ValueError:
                    mark_index = -1

            for index, value in enumerate(
                values
            ):

                if not value:
                    continue

                if value == str(car_no):
                    continue

                if value == mark:
                    continue

                if (
                    mark_index >= 0
                    and index < mark_index
                ):
                    continue

                if len(value) >= 2:
                    name = value
                    break

            comment_candidates = []

            for value in values:

                if not value:
                    continue

                if value == str(car_no):
                    continue

                if value == mark:
                    continue

                if value == name:
                    continue

                comment_candidates.append(
                    value
                )

            comment = ""

            if comment_candidates:
                comment = " ".join(
                    comment_candidates
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

        # 5～7車なら有効
        if 5 <= len(result) <= 7:
            return result

    return result


# ============================================================
# 並び
# ============================================================

STYLE_WORDS = {
    "逃捲",
    "逃げ",
    "先捲",
    "捲り",
    "捲",
    "追込",
    "追捲",
    "自在",
    "単騎",
    "差脚",
}


def parse_line(sp):

    best_cars = []
    best_groups = []

    for table in sp.find_all("table"):

        rows = table.find_all("tr")

        for row_index, tr in enumerate(rows):

            cells = tr.find_all(
                ["th", "td"]
            )

            values = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in cells
            ]

            cars = [
                int(value)
                for value in values
                if re.fullmatch(
                    r"[1-7]",
                    value
                )
            ]

            # 5～7車
            if not (
                5 <= len(set(cars)) <= 7
            ):
                continue

            cars = list(dict.fromkeys(cars))

            nearby_text = ""

            for near in rows[
                row_index:
                row_index + 3
            ]:

                nearby_text += " " + clean(
                    near.get_text(
                        " ",
                        strip=True
                    )
                )

            style_count = sum(
                1
                for word in STYLE_WORDS
                if word in nearby_text
            )

            if (
                not best_cars
                or style_count > 0
            ):

                best_cars = cars

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
                    n
                    for group in groups
                    for n in group
                ]

                if set(flat) != set(
                    best_cars
                ):
                    groups = [
                        best_cars
                    ]

                best_groups = groups

    return best_cars, best_groups


# ============================================================
# 展開コメント
# ============================================================

RACE_WORDS = (
    "先行",
    "捲",
    "番手",
    "ライン",
    "展開",
    "仕掛け",
    "流れ",
    "逃げ",
    "差し",
    "自力",
    "好位",
    "ペース",
    "カマシ",
    "追込",
    "主導権",
)


def is_race_comment(text):

    text = clean(text)

    if len(text) < 15:
        return False

    if len(text) > 300:
        return False

    # 注意書き等を除外
    bad_words = (
        "並び内の脚質",
        "予想の並び",
        "予想情報",
        "オッズ",
        "出走表",
        "投票",
        "Copyright",
        "©",
        "会員",
        "ログイン",
    )

    if any(
        word in text
        for word in bad_words
    ):
        return False

    # レース展開らしいキーワード
    if not any(
        word in text
        for word in RACE_WORDS
    ):
        return False

    # 日本語の文章らしさ
    if not any(
        mark in text
        for mark in (
            "。",
            "！",
            "？",
            "、",
        )
    ):
        return False

    return True


def parse_development(sp):
    """
    予想ページからレース全体の展開コメントを抽出。

    ページ構造が多少変わっても対応できるように
    複数の方法で候補を集める。
    """

    candidates = []
    seen = set()

    # --------------------------------------------------------
    # 1. p / div / td / li
    # --------------------------------------------------------

    for element in sp.find_all(
        ["p", "div", "td", "li"]
    ):

        text = clean(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not is_race_comment(text):
            continue

        if text in seen:
            continue

        seen.add(text)
        candidates.append(text)

    # --------------------------------------------------------
    # 2. 見出し・strong・span周辺
    # --------------------------------------------------------

    for element in sp.find_all(
        ["h1", "h2", "h3", "h4", "strong", "span"]
    ):

        text = clean(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not is_race_comment(text):
            continue

        if text in seen:
            continue

        seen.add(text)
        candidates.append(text)

    # --------------------------------------------------------
    # 3. ページ本文を文章単位で確認
    # --------------------------------------------------------

    body = sp.body

    if body:

        body_text = clean(
            body.get_text(
                "\n",
                strip=True
            )
        )

        for line in body_text.splitlines():

            line = clean(line)

            if not is_race_comment(line):
                continue

            if line in seen:
                continue

            seen.add(line)
            candidates.append(line)

    # --------------------------------------------------------
    # 4. 明らかに「選手コメント」ではないものを優先
    # --------------------------------------------------------

    player_comment_words = (
        "自力。",
        "自在。",
        "追込。",
        "単騎。",
        "前々。",
        "頑張る。",
        "任せる。",
        "番手。",
    )

    scored = []

    for text in candidates:

        score = 0

        # 展開ワード
        for word in RACE_WORDS:
            if word in text:
                score += 2

        # 文章の長さ
        if len(text) >= 30:
            score += 2

        if len(text) >= 50:
            score += 2

        # 句点
        score += text.count("。")

        # 選手個別コメントっぽい短文は減点
        if len(text) < 25:
            score -= 3

        if any(
            word in text
            for word in player_comment_words
        ):
            score -= 2

        scored.append(
            (score, text)
        )

    if not scored:
        return ""

    scored.sort(
        key=lambda x: (
            x[0],
            len(x[1])
        ),
        reverse=True
    )

    return scored[0][1]


# ============================================================
# 予想ページ取得
# ============================================================

def fetch_prediction(race):

    url = prediction_url(
        race["venue_slug"],
        race["race_no"]
    )

    html = get_html(url)
    sp = make_soup(html)

    if not sp:
        return {
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "url": url,
        }

    comments = parse_prediction_table(
        sp
    )

    line, groups = parse_line(
        sp
    )

    development = parse_development(
        sp
    )

    return {
        "comments": comments,
        "line": line,
        "line_groups": groups,
        "development": development,
        "url": url,
    }


# ============================================================
# AI
# ============================================================

def calculate_ai_score(
    rider,
    race
):

    score = 0.0

    base = rider.get("score")

    if base is not None:

        score += max(
            0,
            min(
                30,
                (base - 70) * 1.2
            )
        )

    score += (
        rider.get("finish_1") or 0
    ) * 1.8

    score += (
        rider.get("finish_2") or 0
    ) * 0.9

    score += (
        rider.get("finish_3") or 0
    ) * 0.4

    score += (
        rider.get("kimari_nige") or 0
    ) * 0.8

    score += (
        rider.get("kimari_makuri") or 0
    ) * 1.0

    score += (
        rider.get("kimari_sashi") or 0
    ) * 1.0

    score += rider.get(
        "prediction_score",
        0
    )

    line = race.get(
        "line",
        []
    )

    car_no = rider.get(
        "car_no"
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

    for rider in riders:

        rider["ai_score"] = (
            calculate_ai_score(
                rider,
                race
            )
        )

    ranking = sorted(
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

    if not ranking:

        race["ai"] = {
            "main": None,
            "opponent": None,
            "dark_horse": None,
            "ranking": [],
        }

        return

    main = ranking[0]

    opponent = (
        ranking[1]
        if len(ranking) >= 2
        else None
    )

    dark = (
        ranking[2]
        if len(ranking) >= 3
        else None
    )

    race["ai"] = {

        "main":
            main["car_no"],

        "opponent":
            (
                opponent["car_no"]
                if opponent
                else None
            ),

        "dark_horse":
            (
                dark["car_no"]
                if dark
                else None
            ),

        "ranking": [
            {
                "car_no":
                    rider["car_no"],

                "name":
                    rider["name"],

                "score":
                    rider["ai_score"],

                "mark":
                    rider.get(
                        "prediction_mark",
                        ""
                    ),
            }
            for rider in ranking
        ],
    }


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    print("==============================")
    print(" KEIRIN AI DATA UPDATE v5.4")
    print("==============================")
    print(
        f"対象日: {TODAY_DISPLAY}"
    )
    print("==============================")

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    print(
        "開催場を確認中..."
    )

    venues = []

    for code, info in VENUES.items():

        name, slug = info

        venues.append({
            "code": code,
            "name": name,
            "slug": slug,
        })

    print(
        f"開催場数: {len(venues)}"
    )

    # --------------------------------------------------------
    # 実在レース
    # --------------------------------------------------------

    print(
        "実在レースを実ページで確認中..."
    )

    all_races = []

    for venue in venues:

        results = discover_real_races(
            venue
        )

        race_numbers = [
            r["race_no"]
            for r in results
        ]

        venue["race_numbers"] = (
            race_numbers
        )

        print(
            f"  {venue['code']} "
            f"{venue['name']}: "
            f"{len(race_numbers)}レース"
        )

        all_races.extend(
            results
        )

    all_races.sort(
        key=lambda x: (
            int(x["venue_code"]),
            x["race_no"]
        )
    )

    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    # --------------------------------------------------------
    # 選手データ
    # --------------------------------------------------------

    rider_success = sum(
        1
        for race in all_races
        if 5 <= len(
            race.get(
                "riders",
                []
            )
        ) <= 7
    )

    print(
        "選手データ取得結果: "
        f"{rider_success}/"
        f"{len(all_races)}"
    )

    # --------------------------------------------------------
    # 予想データ
    # --------------------------------------------------------

    print(
        "コメント・並び・展開情報取得中..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_prediction,
                race
            ): race
            for race in all_races
        }

        for future in as_completed(
            futures
        ):

            race = futures[future]

            try:

                prediction = (
                    future.result()
                )

            except Exception:

                prediction = {
                    "comments": {},
                    "line": [],
                    "line_groups": [],
                    "development": "",
                    "url": "",
                }

            race["comments"] = (
                prediction["comments"]
            )

            race["line"] = (
                prediction["line"]
            )

            race["line_groups"] = (
                prediction["line_groups"]
            )

            race["development"] = (
                prediction["development"]
            )

            race["prediction_url"] = (
                prediction["url"]
            )

    # --------------------------------------------------------
    # 選手へ予想情報反映
    # --------------------------------------------------------

    for race in all_races:

        comments = race.get(
            "comments",
            {}
        )

        for rider in race.get(
            "riders",
            []
        ):

            car_no = rider[
                "car_no"
            ]

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

        apply_ai(race)

    # --------------------------------------------------------
    # 集計
    # --------------------------------------------------------

    prediction_races = sum(
        1
        for race in all_races
        if (
            len(
                race.get(
                    "comments",
                    {}
                )
            )
            == len(
                race.get(
                    "riders",
                    []
                )
            )
            and len(
                race.get(
                    "riders",
                    []
                )
            ) >= 5
        )
    )

    comment_count = sum(
        len(
            race.get(
                "comments",
                {}
            )
        )
        for race in all_races
    )

    line_races = sum(
        1
        for race in all_races
        if (
            len(
                race.get(
                    "line",
                    []
                )
            )
            == len(
                race.get(
                    "riders",
                    []
                )
            )
            and len(
                race.get(
                    "riders",
                    []
                )
            ) >= 5
        )
    )

    development_races = sum(
        1
        for race in all_races
        if race.get(
            "development"
        )
    )

    rider_count = sum(
        len(
            race.get(
                "riders",
                []
            )
        )
        for race in all_races
    )

    correct_names = sum(
        1
        for race in all_races
        for rider in race.get(
            "riders",
            []
        )
        if rider.get("name")
    )

    ai_race_count = sum(
        1
        for race in all_races
        if race.get("ai")
    )

    ai_rider_count = sum(
        1
        for race in all_races
        for rider in race.get(
            "riders",
            []
        )
        if rider.get(
            "ai_score"
        ) is not None
    )

    # --------------------------------------------------------
    # 不足一覧
    # --------------------------------------------------------

    failed_riders = []
    failed_predictions = []
    failed_lines = []
    failed_development = []

    for race in all_races:

        label = (
            f"{race['venue_name']} "
            f"{race['race_no']}R"
        )

        rider_len = len(
            race.get(
                "riders",
                []
            )
        )

        if not (
            5 <= rider_len <= 7
        ):
            failed_riders.append(
                label
            )

        comment_len = len(
            race.get(
                "comments",
                {}
            )
        )

        if comment_len != rider_len:
            failed_predictions.append(
                label
            )

        line_len = len(
            race.get(
                "line",
                []
            )
        )

        if line_len != rider_len:
            failed_lines.append(
                label
            )

        if not race.get(
            "development"
        ):
            failed_development.append(
                label
            )

    # --------------------------------------------------------
    # 完全性
    # --------------------------------------------------------

    data_complete = (
        len(all_races) == 72
        and rider_success == 72
        and rider_count == 500
        and correct_names == 500
        and prediction_races == 72
        and line_races == 72
        and development_races >= 70
        and not failed_riders
    )

    # --------------------------------------------------------
    # 出力
    # --------------------------------------------------------

    output = {

        "version":
            "5.4",

        "updated_at":
            datetime.now().isoformat(),

        "target_date":
            TODAY_DISPLAY,

        "data_complete":
            data_complete,

        "venue_count":
            len(venues),

        "race_count":
            len(all_races),

        "rider_count":
            rider_count,

        "correct_name_count":
            correct_names,

        "prediction_race_count":
            prediction_races,

        "comment_count":
            comment_count,

        "line_race_count":
            line_races,

        "development_count":
            development_races,

        "ai_race_count":
            ai_race_count,

        "ai_rider_count":
            ai_rider_count,

        "failed_race_count":
            len(failed_riders),

        "venues": [

            {
                "venue_code":
                    venue["code"],

                "venue_name":
                    venue["name"],

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

            for venue in venues
        ],

        "races":
            all_races,
    }

    # --------------------------------------------------------
    # 保存
    # --------------------------------------------------------

    os.makedirs(
        "data",
        exist_ok=True
    )

    path = "data/today.json"
    temp_path = path + ".tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_path,
        path
    )

    elapsed = (
        time.time() - started
    )

    # --------------------------------------------------------
    # 最終ログ
    # --------------------------------------------------------

    print(
        "=============================="
    )

    print(
        " v5.4 取得結果"
    )

    print(
        "=============================="
    )

    print(
        f"開催場: {len(venues)}"
    )

    print(
        f"レース: {len(all_races)}"
    )

    print(
        f"選手: {rider_count}"
    )

    print(
        f"正しい選手名: {correct_names}"
    )

    print(
        "【取得状況】"
    )

    print(
        f"選手データ成功: "
        f"{rider_success}/"
        f"{len(all_races)}"
    )

    print(
        f"予想情報レース: "
        f"{prediction_races}/"
        f"{len(all_races)}"
    )

    print(
        f"選手コメント: "
        f"{comment_count}"
    )

    print(
        f"並び取得レース: "
        f"{line_races}"
    )

    print(
        f"展開コメント: "
        f"{development_races}"
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
        f"{len(failed_riders)}"
    )

    print(
        f"処理時間: "
        f"{elapsed:.1f}秒"
    )

    print(
        f"データ完全性: "
        f"{'OK' if data_complete else '要確認'}"
    )

    # --------------------------------------------------------
    # 詳細診断
    # --------------------------------------------------------

    print(
        "=============================="
    )

    print(
        " 詳細診断"
    )

    print(
        "=============================="
    )

    if failed_riders:

        print(
            "選手データ不足:"
        )

        print(
            "  "
            + ", ".join(
                failed_riders
            )
        )

    else:

        print(
            "選手データ不足: なし"
        )

    if failed_predictions:

        print(
            "予想情報不足:"
        )

        print(
            "  "
            + ", ".join(
                failed_predictions
            )
        )

    else:

        print(
            "予想情報不足: なし"
        )

    if failed_lines:

        print(
            "並び不足:"
        )

        print(
            "  "
            + ", ".join(
                failed_lines
            )
        )

    else:

        print(
            "並び不足: なし"
        )

    if failed_development:

        print(
            "展開コメント不足:"
        )

        print(
            "  "
            + ", ".join(
                failed_development
            )
        )

    else:

        print(
            "展開コメント不足: なし"
        )

    print(
        f"保存先: {path}"
    )


if __name__ == "__main__":
    main()
