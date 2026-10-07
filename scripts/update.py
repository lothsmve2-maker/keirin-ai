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

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# ============================================================
# KEIRIN AI DATA UPDATE v5.2
# ============================================================

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

TIMEOUT = 18
RETRIES = 2
MAX_WORKERS = 8


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
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

def get_html(url, retries=RETRIES):
    if not url:
        return None

    for attempt in range(retries + 1):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            if r.status_code == 200 and len(r.text) > 500:
                r.encoding = r.apparent_encoding or r.encoding
                return r.text

        except requests.RequestException:
            pass

        if attempt < retries:
            time.sleep(0.5 + attempt * 0.5)

    return None


def soup(html):
    if not html:
        return None

    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def clean(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("　", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# 開催場取得
# ============================================================

def get_venues():
    url = (
        f"{BASE_URL}/keirin/RaceListInfo.do"
        f"?kaisaiBi={TODAY}"
    )

    html = get_html(url)

    if not html:
        return []

    sp = soup(html)

    if not sp:
        return []

    found = {}

    for code, (name, slug) in VENUES.items():
        if name in sp.get_text(" ", strip=True):
            found[code] = {
                "code": code,
                "name": name,
                "slug": slug,
            }

    return list(found.values())


# ============================================================
# 実在レース確認
#
# v5.0で成功した方式を維持
# ============================================================

def get_race_numbers_from_prediction(
    venue_name,
    venue_slug,
):
    url = (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{venue_slug}/{TODAY[:4]}/{TODAY[4:]}.html"
    )

    html = get_html(url)

    if not html:
        return []

    sp = soup(html)

    if not sp:
        return []

    found = set()

    # 1R
    text = clean(
        sp.get_text(" ", strip=True)
    )

    if re.search(r"(?:^|\s)1R(?:\s|$)", text):
        found.add(1)

    # リンク
    for a in sp.find_all("a", href=True):
        href = a.get("href", "")
        label = clean(
            a.get_text(" ", strip=True)
        )

        patterns = [
            r"_(\d{1,2})\.html",
            r"raceNo=(\d{1,2})",
        ]

        for pattern in patterns:
            m = re.search(pattern, href)

            if m:
                n = int(m.group(1))

                if 1 <= n <= 12:
                    found.add(n)

        m = re.search(
            r"(?:^|\s)(\d{1,2})R(?:\s|$)",
            label
        )

        if m:
            n = int(m.group(1))

            if 1 <= n <= 12:
                found.add(n)

    return sorted(found)


def get_races(venues):
    races = []

    for venue in venues:
        code = venue["code"]
        name = venue["name"]
        slug = venue["slug"]

        numbers = get_race_numbers_from_prediction(
            name,
            slug
        )

        venue["race_numbers"] = numbers

        print(
            f"  {code} {name}: "
            f"{len(numbers)}レース"
        )

        for race_no in numbers:
            races.append({
                "venue_code": code,
                "venue_name": name,
                "venue_slug": slug,
                "race_no": race_no,
            })

    return races


# ============================================================
# 出走表
# ============================================================

def race_url(code, race_no):
    return (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


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
    "S1", "S2", "A1", "A2", "A3",
    "L1", "L2",
}

STYLES = {
    "逃", "捲", "追", "両",
}


def parse_age_period(value):
    m = re.search(
        r"(\d{2})\s*/\s*(\d{2,3})",
        value
    )

    if not m:
        return None, None

    return int(m.group(1)), int(m.group(2))


def parse_rider_row(values):
    values = [
        clean(v)
        for v in values
        if clean(v)
    ]

    if not values:
        return None

    car_no = None

    for v in values[:4]:
        if re.fullmatch(r"[1-7]", v):
            car_no = int(v)
            break

    if car_no is None:
        return None

    age = None
    period = None

    for v in values:
        a, p = parse_age_period(v)

        if a is not None:
            age = a
            period = p
            break

    name = ""

    for v in values:
        if v == str(car_no):
            continue

        if re.search(r"\d{2}\s*/\s*\d{2,3}", v):
            continue

        if v in PREFECTURES:
            continue

        if v in GRADES:
            continue

        if v in STYLES:
            continue

        if re.fullmatch(r"\d+(?:\.\d+)?", v):
            continue

        if len(v) >= 2:
            name = v
            break

    if not name:
        return None

    prefecture = ""
    grade = ""
    style = ""

    for v in values:
        if v in PREFECTURES:
            prefecture = v

        if v in GRADES:
            grade = v

        if v in STYLES:
            style = v

    score = None

    for v in values:
        if re.fullmatch(r"\d{2,3}(?:\.\d+)?", v):
            try:
                x = float(v)

                if 60 <= x <= 130:
                    score = x
                    break
            except Exception:
                pass

    numeric_blocks = []

    for v in values:
        nums = [
            int(x)
            for x in re.findall(r"\d+", v)
        ]

        if len(nums) >= 4:
            numeric_blocks.append(nums[:4])

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


def parse_riders(sp):
    candidates = []

    for table in sp.find_all("table"):
        local = {}

        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])

            values = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in cells
            ]

            rider = parse_rider_row(values)

            if rider:
                local[
                    rider["car_no"]
                ] = rider

        rows = list(local.values())

        if len(rows) >= 7:
            return sorted(
                rows,
                key=lambda x: x["car_no"]
            )[:7]

        if len(rows) > len(candidates):
            candidates = rows

    return sorted(
        candidates,
        key=lambda x: x["car_no"]
    )[:7]


def get_race_data(item):
    url = race_url(
        item["venue_code"],
        item["race_no"]
    )

    html = get_html(url)

    if not html:
        return {
            **item,
            "url": url,
            "riders": [],
            "success": False,
        }

    sp = soup(html)

    riders = parse_riders(sp)

    # 7人取れなかった場合のみ再取得
    if len(riders) < 7:
        time.sleep(0.4)

        retry_html = get_html(url)

        if retry_html:
            retry_sp = soup(retry_html)
            retry_riders = parse_riders(retry_sp)

            if len(retry_riders) > len(riders):
                riders = retry_riders

    return {
        **item,
        "url": url,
        "riders": riders,
        "success": len(riders) == 7,
    }


# ============================================================
# 予想ページURL
# ============================================================

def prediction_index_url(slug):
    return (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/{TODAY[4:]}.html"
    )


def race_no_from_href(href):
    patterns = [
        r"_(\d{1,2})\.html",
        r"raceNo=(\d{1,2})",
    ]

    for pattern in patterns:
        m = re.search(pattern, href or "")

        if m:
            n = int(m.group(1))

            if 1 <= n <= 12:
                return n

    return None


def discover_prediction_urls(
    venue,
    valid_races
):
    index_url = prediction_index_url(
        venue["slug"]
    )

    html = get_html(index_url)

    if not html:
        return {}

    sp = soup(html)

    if not sp:
        return {}

    valid = set(valid_races)
    result = {}

    # 1Rはインデックスページそのもの
    text = clean(
        sp.get_text(" ", strip=True)
    )

    if (
        1 in valid
        and re.search(
            r"(?:^|\s)1R(?:\s|$)",
            text
        )
    ):
        result[1] = index_url

    for a in sp.find_all(
        "a",
        href=True
    ):
        href = a.get("href", "")

        n = race_no_from_href(href)

        if n is None:
            label = clean(
                a.get_text(
                    " ",
                    strip=True
                )
            )

            m = re.search(
                r"(?:^|\s)(\d{1,2})R(?:\s|$)",
                label
            )

            if m:
                n = int(m.group(1))

        if n is None:
            continue

        if n not in valid:
            continue

        absolute = urljoin(
            index_url,
            href
        )

        if "oddspark.com" not in absolute:
            continue

        result[n] = absolute

    return dict(
        sorted(result.items())
    )


# ============================================================
# 予想解析
# ============================================================

MARKS = {
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

        header_index = None

        for i, tr in enumerate(rows):
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
                header_index = i
                break

        if header_index is None:
            continue

        for tr in rows[header_index + 1:]:
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

            if len(values) < 3:
                continue

            car_no = None

            for v in values[:3]:
                if re.fullmatch(r"[1-7]", v):
                    car_no = int(v)
                    break

            if car_no is None:
                continue

            mark = ""

            for v in values:
                if v in MARKS:
                    mark = v
                    break

            # 選手名
            name = ""

            for v in values:
                if not v:
                    continue

                if v == str(car_no):
                    continue

                if v == mark:
                    continue

                if len(v) >= 2:
                    name = v
                    break

            # コメント
            comment = ""

            if values:
                # 多くの場合最後のセルがコメント
                candidates = [
                    v for v in values
                    if v
                    and v != str(car_no)
                    and v != mark
                    and v != name
                ]

                if candidates:
                    comment = clean(
                        " ".join(candidates)
                    )

            result[car_no] = {
                "name": name,
                "mark": mark,
                "comment": comment,
                "prediction_score":
                    MARKS.get(mark, 0),
            }

        if len(result) >= 7:
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
    "差脚",
    "自在",
    "単騎",
}


def parse_line(sp):
    best = []
    best_groups = []
    best_style_count = -1

    for table in sp.find_all("table"):
        rows = table.find_all("tr")

        for i, tr in enumerate(rows):
            cells = tr.find_all(["th", "td"])

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
                int(v)
                for v in values
                if re.fullmatch(r"[1-7]", v)
            ]

            if len(cars) != 7:
                continue

            if sorted(cars) != list(range(1, 8)):
                continue

            nearby = []

            for near in rows[i:i + 3]:
                nearby.extend([
                    clean(
                        c.get_text(
                            " ",
                            strip=True
                        )
                    )
                    for c in near.find_all(
                        ["th", "td"]
                    )
                ])

            style_count = sum(
                1
                for v in nearby
                if v in STYLE_WORDS
            )

            if style_count < best_style_count:
                continue

            best_style_count = style_count
            best = cars

            groups = []
            current = []

            for v in values:
                if re.fullmatch(r"[1-7]", v):
                    current.append(int(v))
                else:
                    if current:
                        groups.append(current)
                        current = []

            if current:
                groups.append(current)

            flat = [
                n
                for g in groups
                for n in g
            ]

            if sorted(flat) != list(range(1, 8)):
                groups = [cars]

            best_groups = groups

    return best, best_groups


# ============================================================
# 展開
# ============================================================

def parse_development(sp):
    bad = (
        "免責事項",
        "利用規約",
        "プライバシー",
        "お問い合わせ",
        "Copyright",
    )

    race_words = (
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
    )

    # 「予想の並び」を基準に探す
    markers = sp.find_all(
        string=re.compile(
            r"予想の並び"
        )
    )

    for marker in markers:
        parent = marker.parent

        if not parent:
            continue

        current = parent

        for _ in range(4):
            if not current:
                break

            for element in current.find_all(
                ["p", "div", "td", "li"],
                limit=60
            ):
                text = clean(
                    element.get_text(
                        " ",
                        strip=True
                    )
                )

                if len(text) < 15:
                    continue

                if len(text) > 350:
                    continue

                if any(
                    x in text
                    for x in bad
                ):
                    continue

                if (
                    any(
                        x in text
                        for x in race_words
                    )
                    and (
                        "。" in text
                        or "！" in text
                        or "？" in text
                    )
                ):
                    return text

            current = current.parent

    return ""


# ============================================================
# 予想ページ取得
# ============================================================

def fetch_prediction(
    venue_code,
    race_no,
    url
):
    html = get_html(url)

    if not html:
        return {
            "venue_code": venue_code,
            "race_no": race_no,
            "url": url,
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
        }

    sp = soup(html)

    comments = parse_prediction_table(sp)

    line, groups = parse_line(sp)

    development = parse_development(sp)

    # 弱い場合だけ再取得
    quality = (
        len(comments)
        + (10 if len(line) == 7 else 0)
        + (5 if development else 0)
    )

    if quality < 15:
        time.sleep(0.3)

        retry_html = get_html(url)

        if retry_html:
            retry_sp = soup(retry_html)

            retry_comments = (
                parse_prediction_table(
                    retry_sp
                )
            )

            retry_line, retry_groups = (
                parse_line(retry_sp)
            )

            retry_development = (
                parse_development(
                    retry_sp
                )
            )

            retry_quality = (
                len(retry_comments)
                + (
                    10
                    if len(retry_line) == 7
                    else 0
                )
                + (
                    5
                    if retry_development
                    else 0
                )
            )

            if retry_quality > quality:
                comments = retry_comments
                line = retry_line
                groups = retry_groups
                development = retry_development

    return {
        "venue_code": venue_code,
        "race_no": race_no,
        "url": url,
        "comments": comments,
        "line": line,
        "line_groups": groups,
        "development": development,
    }


# ============================================================
# AI
# ============================================================

def ai_score(rider, race):
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

    car = rider.get("car_no")

    line = race.get("line", [])

    if car in line:
        pos = line.index(car)

        if pos == 0:
            score += 2

        elif pos == 1:
            score += 4

        elif pos == 2:
            score += 2

    return round(score, 2)


def apply_ai(race):
    riders = race.get("riders", [])

    for rider in riders:
        rider["ai_score"] = ai_score(
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

    if not ranked:
        race["ai"] = {
            "main": None,
            "opponent": None,
            "dark_horse": None,
            "ranking": [],
        }
        return

    main = ranked[0]

    opponent = (
        ranked[1]
        if len(ranked) > 1
        else None
    )

    dark = None

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

        dark = candidates[0]

    race["ai"] = {
        "main": main["car_no"],

        "opponent": (
            opponent["car_no"]
            if opponent
            else None
        ),

        "dark_horse": (
            dark["car_no"]
            if dark
            else None
        ),

        "ranking": [
            {
                "car_no": x["car_no"],
                "name": x["name"],
                "score": x["ai_score"],
                "mark": x.get(
                    "prediction_mark",
                    ""
                ),
            }
            for x in ranked
        ],
    }


# ============================================================
# MAIN
# ============================================================

def main():
    started = time.time()

    print("==============================")
    print(" KEIRIN AI DATA UPDATE v5.2")
    print("==============================")
    print(f"対象日: {TODAY_DISPLAY}")
    print("==============================")

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    venues = get_venues()

    if not venues:
        raise RuntimeError(
            "開催場を取得できませんでした"
        )

    print(
        f"開催場数: {len(venues)}"
    )

    # --------------------------------------------------------
    # レース
    # --------------------------------------------------------

    print("実在レースを確認中...")

    races = get_races(venues)

    print(
        f"詳細取得対象レース: {len(races)}"
    )

    if not races:
        raise RuntimeError(
            "実在レースが0件です"
        )

    # --------------------------------------------------------
    # 出走表
    # --------------------------------------------------------

    print("選手データ取得中...")

    race_data = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                get_race_data,
                item
            ): item
            for item in races
        }

        for future in as_completed(futures):
            item = futures[future]

            try:
                result = future.result()

            except Exception as e:
                result = {
                    **item,
                    "url": race_url(
                        item["venue_code"],
                        item["race_no"]
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
        for r in race_data
        if len(r.get("riders", [])) == 7
    )

    print(
        f"選手データ取得結果: "
        f"{rider_success}/{len(race_data)}"
    )

    # --------------------------------------------------------
    # 予想URL
    # --------------------------------------------------------

    print("予想ページURLを確認中...")

    prediction_urls = {}

    for venue in venues:

        urls = discover_prediction_urls(
            venue,
            venue["race_numbers"]
        )

        prediction_urls[
            venue["code"]
        ] = urls

        print(
            f"  {venue['code']} "
            f"{venue['name']}: "
            f"{len(urls)}レース予想URL"
        )

    # --------------------------------------------------------
    # URL不足チェック
    # --------------------------------------------------------

    missing_urls = []

    for venue in venues:
        code = venue["code"]

        expected = set(
            venue["race_numbers"]
        )

        actual = set(
            prediction_urls.get(
                code,
                {}
            ).keys()
        )

        missing = sorted(
            expected - actual
        )

        for n in missing:
            missing_urls.append(
                f"{venue['name']} {n}R"
            )

    if missing_urls:
        print(
            "予想URL不足:"
        )

        for x in missing_urls:
            print(
                f"  {x}"
            )

    else:
        print(
            "予想URL: 72/72 OK"
        )

    # --------------------------------------------------------
    # 予想取得
    # --------------------------------------------------------

    print(
        "コメント・並び・展開情報取得中..."
    )

    jobs = []

    for venue in venues:
        code = venue["code"]

        urls = prediction_urls.get(
            code,
            {}
        )

        for race_no, url in urls.items():
            jobs.append(
                (
                    code,
                    race_no,
                    url
                )
            )

    predictions = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_prediction,
                code,
                race_no,
                url
            ): (
                code,
                race_no
            )
            for code, race_no, url in jobs
        }

        for future in as_completed(futures):

            key = futures[future]

            try:
                result = future.result()

            except Exception as e:
                result = {
                    "venue_code": key[0],
                    "race_no": key[1],
                    "url": "",
                    "comments": {},
                    "line": [],
                    "line_groups": [],
                    "development": "",
                    "error": str(e),
                }

            predictions[key] = result

    # --------------------------------------------------------
    # 結合
    # --------------------------------------------------------

    prediction_count = 0
    comment_count = 0
    line_count = 0
    development_count = 0

    for race in race_data:

        key = (
            race["venue_code"],
            race["race_no"]
        )

        p = predictions.get(key)

        race["comments"] = {}
        race["line"] = []
        race["line_groups"] = []
        race["development"] = ""
        race["prediction_url"] = ""

        if p:

            race["prediction_url"] = p["url"]

            comments = p.get(
                "comments",
                {}
            )

            race["comments"] = {
                str(k): v
                for k, v in comments.items()
            }

            race["line"] = p.get(
                "line",
                []
            )

            race["line_groups"] = p.get(
                "line_groups",
                []
            )

            race["development"] = p.get(
                "development",
                ""
            )

            if len(comments) >= 7:
                prediction_count += 1

            comment_count += len(comments)

            if len(race["line"]) == 7:
                line_count += 1

            if race["development"]:
                development_count += 1

            # 選手へコメントを反映
            for rider in race.get(
                "riders",
                []
            ):

                car = rider["car_no"]

                info = comments.get(car)

                if not info:
                    continue

                rider["prediction_mark"] = (
                    info.get("mark", "")
                )

                rider["prediction_score"] = (
                    info.get(
                        "prediction_score",
                        0
                    )
                )

                rider["comment"] = (
                    info.get("comment", "")
                )

        apply_ai(race)

    # --------------------------------------------------------
    # 不足Rを一覧表示
    # --------------------------------------------------------

    failed_riders = []
    failed_predictions = []
    failed_lines = []
    failed_development = []

    for race in race_data:

        label = (
            f"{race['venue_name']} "
            f"{race['race_no']}R"
        )

        if len(
            race.get("riders", [])
        ) != 7:
            failed_riders.append(label)

        if len(
            race.get("comments", {})
        ) < 7:
            failed_predictions.append(label)

        if len(
            race.get("line", [])
        ) != 7:
            failed_lines.append(label)

        if not race.get(
            "development"
        ):
            failed_development.append(label)

    # --------------------------------------------------------
    # 診断
    # --------------------------------------------------------

    print(
        "=============================="
    )
    print(
        " 不足データ診断"
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

    # --------------------------------------------------------
    # 集計
    # --------------------------------------------------------

    rider_count = sum(
        len(r.get("riders", []))
        for r in race_data
    )

    correct_names = sum(
        1
        for r in race_data
        for rider in r.get(
            "riders",
            []
        )
        if rider.get("name")
    )

    ai_riders = sum(
        1
        for r in race_data
        for rider in r.get(
            "riders",
            []
        )
        if rider.get("ai_score") is not None
    )

    failed_races = sum(
        1
        for r in race_data
        if not r.get("success")
    )

    complete = (
        len(race_data) == 72
        and rider_success == 72
        and failed_races == 0
    )

    # --------------------------------------------------------
    # 出力
    # --------------------------------------------------------

    output = {
        "version": "5.2",
        "updated_at": datetime.now().isoformat(),
        "target_date": TODAY_DISPLAY,

        "data_complete": complete,

        "venue_count": len(venues),
        "race_count": len(race_data),
        "rider_count": rider_count,
        "correct_name_count": correct_names,

        "prediction_race_count": prediction_count,
        "comment_count": comment_count,
        "line_race_count": line_count,
        "development_count": development_count,

        "ai_race_count": sum(
            1
            for r in race_data
            if r.get("ai")
        ),

        "ai_rider_count": ai_riders,

        "failed_race_count": failed_races,

        "venues": [
            {
                "venue_code": v["code"],
                "venue_name": v["name"],
                "race_count": len(
                    v["race_numbers"]
                ),
                "race_numbers": v[
                    "race_numbers"
                ],
            }
            for v in venues
        ],

        "races": race_data,
    }

    os.makedirs(
        "data",
        exist_ok=True
    )

    path = "data/today.json"
    tmp = path + ".tmp"

    with open(
        tmp,
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
        tmp,
        path
    )

    elapsed = time.time() - started

    # --------------------------------------------------------
    # 最終結果
    # --------------------------------------------------------

    print(
        "=============================="
    )
    print(
        " 更新完了 v5.2"
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
        f"正しい選手名: {correct_names}"
    )

    print(
        "【取得状況】"
    )

    print(
        f"選手データ成功: "
        f"{rider_success}/{len(race_data)}"
    )

    print(
        f"予想情報レース: "
        f"{prediction_count}/{len(race_data)}"
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
        f"{sum(1 for r in race_data if r.get('ai'))}"
    )

    print(
        f"AI評価選手: "
        f"{ai_riders}"
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
        f"保存先: {path}"
    )


if __name__ == "__main__":
    main()
