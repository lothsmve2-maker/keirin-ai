import json
import os
import re
import time
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# =========================================================
# VERSION
# =========================================================

VERSION = "6.1"

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

TIMEOUT = 15
RETRIES = 1
MAX_WORKERS = 8

BET_UNIT = 100
MAX_BETS = 10


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}


# =========================================================
# 競輪場
# =========================================================

VENUES = {
    "22": ("前橋", "maebashi"),
    "25": ("大宮", "omiya"),
    "38": ("静岡", "shizuoka"),
    "44": ("大垣", "ogaki"),
    "48": ("四日市", "yokkaichi"),
    "53": ("奈良", "nara"),
    "74": ("高知", "kochi"),
}


# =========================================================
# 予想設定
# =========================================================

MARK_SCORE = {
    "◎": 18,
    "○": 12,
    "▲": 8,
    "△": 5,
    "×": 1,
    "注": 3,
}

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
    "地差",
    "先行",
}

PREFECTURES = {
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
    "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
    "新潟", "富山", "石川", "福井", "山梨", "長野", "岐阜",
    "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫",
    "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "山口",
    "徳島", "香川", "愛媛", "高知", "福岡", "佐賀", "長崎",
    "熊本", "大分", "宮崎", "鹿児島", "沖縄",
}


GRADES = {
    "S1",
    "S2",
    "A1",
    "A2",
    "A3",
    "L1",
    "L2",
}


# =========================================================
# 基本
# =========================================================

def clean(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("　", " ")

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def get_html(url):
    if not url:
        return None

    for attempt in range(RETRIES + 1):

        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            if (
                r.status_code == 200
                and len(r.text) > 300
            ):

                r.encoding = (
                    r.apparent_encoding
                    or r.encoding
                )

                return r.text

        except requests.RequestException:
            pass

        if attempt < RETRIES:
            time.sleep(0.4)

    return None


def soup(html):

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


def absolute_url(href):

    if not href:
        return ""

    href = href.strip()

    if href.startswith("http://"):
        return href

    if href.startswith("https://"):
        return href

    if href.startswith("//"):
        return "https:" + href

    if href.startswith("/"):
        return BASE_URL + href

    return BASE_URL + "/" + href


def numbers4(text):

    m = re.search(
        r"(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)",
        text,
    )

    if not m:
        return [
            None,
            None,
            None,
            None,
        ]

    return [
        int(x)
        for x in m.groups()
    ]


# =========================================================
# URL
# =========================================================

def race_url(code, race_no):

    return (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={code}"
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


def odds_url(code, race_no):

    return (
        f"{SP_BASE_URL}/keirin/"
        f"SpOddsInfo.do"
        f"?betType=9"
        f"&dispMode=1"
        f"&joCd={code}"
        f"&joCode={code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


def result_url(code, race_no):

    return (
        f"{SP_BASE_URL}/keirin/"
        f"SpRaceResultInfo.do"
        f"?joCd={code}"
        f"&joCode={code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


# =========================================================
# 出走表
# =========================================================

def parse_rider_row(tr):

    text = clean(
        tr.get_text(
            " ",
            strip=True
        )
    )

    if not text:
        return None

    car = None

    mcar = re.search(
        r"\b([1-7])\b",
        text
    )

    if mcar:
        car = int(
            mcar.group(1)
        )

    if car is None or not 1 <= car <= 7:
        return None

    name = ""

    # ① 選手リンク
    for a in tr.find_all("a"):

        href = a.get(
            "href",
            ""
        )

        label = clean(
            a.get_text(
                " ",
                strip=True
            )
        )

        if (
            label
            and (
                "PlayerDetail.do"
                in href
                or "player"
                in href.lower()
            )
        ):

            if label not in MARK_SCORE:
                name = label
                break

    # ② セル
    if not name:

        cells = [
            clean(
                c.get_text(
                    " ",
                    strip=True
                )
            )
            for c in tr.find_all(
                ["th", "td"]
            )
        ]

        for i, v in enumerate(cells):

            if v == str(car):

                for nxt in cells[
                    i + 1:i + 6
                ]:

                    if not nxt:
                        continue

                    if nxt in MARK_SCORE:
                        continue

                    if re.fullmatch(
                        r"[1-7]",
                        nxt
                    ):
                        continue

                    if re.fullmatch(
                        r"\d+",
                        nxt
                    ):
                        continue

                    if re.search(
                        r"[一-龯ぁ-んァ-ヶ]",
                        nxt
                    ):

                        if not any(
                            x in nxt
                            for x in (
                                "競走得点",
                                "着順",
                                "決まり手",
                                "今場所",
                                "前場所",
                            )
                        ):

                            name = nxt
                            break

    # ③ テキスト
    if not name:

        parts = [
            clean(x)
            for x in tr.stripped_strings
            if clean(x)
        ]

        for i, part in enumerate(parts):

            if part == str(car):

                for nxt in parts[
                    i + 1:i + 6
                ]:

                    if (
                        nxt
                        and nxt not in MARK_SCORE
                        and re.search(
                            r"[一-龯]",
                            nxt
                        )
                        and len(nxt) <= 12
                    ):

                        name = nxt
                        break

            if name:
                break

    if not name:
        return None

    age = None
    period = None

    m = re.search(
        r"(\d{1,2})歳\s*[／/]\s*(\d{2,3})期",
        text
    )

    if m:

        age = int(
            m.group(1)
        )

        period = int(
            m.group(2)
        )

    prefecture = ""

    if m:

        tail = text[
            m.end():
        ]

        for p in sorted(
            PREFECTURES,
            key=len,
            reverse=True
        ):

            if p in tail:
                prefecture = p
                break

    if not prefecture:

        for p in sorted(
            PREFECTURES,
            key=len,
            reverse=True
        ):

            if p in text:
                prefecture = p
                break

    grade = ""

    mg = re.search(
        r"\b([SAL]\d)\b",
        text
    )

    if mg:
        grade = mg.group(1)

    style = ""

    for s in (
        "逃",
        "捲",
        "追",
        "両",
    ):

        if re.search(
            rf"\s{s}\s*[|｜]",
            text
        ):

            style = s
            break

    if not style:

        for s in (
            "逃",
            "捲",
            "追",
            "両",
        ):

            if re.search(
                rf"\s{s}\s+",
                text
            ):

                style = s
                break

    score = None

    ms = re.search(
        r"競走得点\s*[:：]?\s*(\d+(?:\.\d+)?)",
        text
    )

    if ms:
        score = float(
            ms.group(1)
        )

    finish_text = ""

    mf = re.search(
        r"着\s*順\s*[:：]?\s*([^|｜]+)",
        text
    )

    if mf:
        finish_text = mf.group(1)

    finish = numbers4(
        finish_text
    )

    kimari_text = ""

    mk = re.search(
        r"決まり手\s*[:：]?\s*([^|｜]+)",
        text
    )

    if mk:
        kimari_text = mk.group(1)

    kimari = numbers4(
        kimari_text
    )

    return {
        "car_no": car,
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

    if not sp:
        return []

    best = {}

    for tr in sp.find_all("tr"):

        rider = parse_rider_row(tr)

        if rider:
            best[
                rider["car_no"]
            ] = rider

    if 5 <= len(best) <= 7:

        return [
            best[k]
            for k in sorted(best)
        ]

    return []


# =========================================================
# 関連URL
# =========================================================

def discover_related_urls(
    sp,
    race_url_value
):

    result = {
        "odds_url": "",
        "result_url": "",
    }

    if not sp:
        return result

    for a in sp.find_all("a"):

        text = clean(
            a.get_text(
                " ",
                strip=True
            )
        )

        href = a.get(
            "href",
            ""
        )

        if not href:
            continue

        url = absolute_url(
            href
        )

        lower = (
            text.lower()
            + " "
            + href.lower()
        )

        if not result["odds_url"]:

            if (
                "オッズ" in text
                or "oddsinfo" in lower
                or "odds" in lower
            ):

                result["odds_url"] = url

        if not result["result_url"]:

            if (
                "結果" in text
                or "raceresult" in lower
                or "raceresultinfo" in lower
                or "resultinfo" in lower
            ):

                result["result_url"] = url

    return result


# =========================================================
# レース取得
# =========================================================

def fetch_race(job):

    url = race_url(
        job["venue_code"],
        job["race_no"]
    )

    html = get_html(
        url
    )

    sp = soup(
        html
    )

    riders = (
        parse_riders(sp)
        if sp
        else []
    )

    related = discover_related_urls(
        sp,
        url
    )

    return {
        **job,
        "url": url,
        "riders": riders,
        "odds_url": related.get(
            "odds_url",
            ""
        ),
        "result_url": related.get(
            "result_url",
            ""
        ),
        "success": (
            5 <= len(riders) <= 7
        ),
    }


def discover_real_races(
    venue
):

    jobs = []

    for n in range(1, 13):

        jobs.append({
            "venue_code": venue["code"],
            "venue_name": venue["name"],
            "venue_slug": venue["slug"],
            "race_no": n,
        })

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as ex:

        futures = [
            ex.submit(
                fetch_race,
                j
            )
            for j in jobs
        ]

        for f in as_completed(
            futures
        ):

            try:

                r = f.result()

                if r["success"]:
                    results.append(r)

            except Exception:
                pass

    return sorted(
        results,
        key=lambda x: x["race_no"]
    )


# =========================================================
# 予想解析
# =========================================================

def extract_car(value):

    value = clean(value)

    if re.fullmatch(
        r"[1-7]",
        value
    ):
        return int(value)

    return None


def parse_prediction_table(sp):

    if not sp:
        return {}

    result = {}

    for table in sp.find_all(
        "table"
    ):

        rows = table.find_all(
            "tr"
        )

        for tr in rows:

            cells = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in tr.find_all(
                    ["th", "td"]
                )
            ]

            if not cells:
                continue

            car = None

            for v in cells:

                c = extract_car(v)

                if c is not None:
                    car = c
                    break

            if car is None:
                continue

            mark = ""

            for v in cells:

                if v in MARK_SCORE:
                    mark = v
                    break

            name = ""

            for v in cells:

                if (
                    not v
                    or v == str(car)
                    or v == mark
                    or v in MARK_SCORE
                ):
                    continue

                if re.fullmatch(
                    r"\d+(?:\.\d+)?",
                    v
                ):
                    continue

                if any(
                    x in v
                    for x in (
                        "選手名",
                        "コメント",
                        "予想",
                        "並び",
                    )
                ):
                    continue

                if re.search(
                    r"[一-龯]",
                    v
                ):

                    name = v
                    break

            comments = []

            for v in cells:

                if (
                    not v
                    or v == str(car)
                    or v == mark
                    or v == name
                ):
                    continue

                if v in MARK_SCORE:
                    continue

                if any(
                    x in v
                    for x in (
                        "選手名",
                        "コメント",
                    )
                ):
                    continue

                if re.search(
                    r"[ぁ-んァ-ヶ]",
                    v
                ):

                    comments.append(v)

            comment = " ".join(
                comments
            )

            if (
                name
                or comment
                or mark
            ):

                result[car] = {
                    "name": name,
                    "mark": mark,
                    "comment": comment,
                    "prediction_score":
                        MARK_SCORE.get(
                            mark,
                            0
                        ),
                }

    if 5 <= len(result) <= 7:
        return result

    result2 = {}

    for tr in sp.find_all(
        "tr"
    ):

        text = clean(
            tr.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        cars = re.findall(
            r"\b([1-7])\b",
            text
        )

        if not cars:
            continue

        car = int(
            cars[0]
        )

        if not 1 <= car <= 7:
            continue

        mark = ""

        for m in MARK_SCORE:

            if m in text:
                mark = m
                break

        if "選手名" in text:
            continue

        comment = ""

        parts = [
            clean(x)
            for x in tr.stripped_strings
            if clean(x)
        ]

        for p in parts:

            if (
                p == str(car)
                or p == mark
                or p in MARK_SCORE
            ):
                continue

            if re.search(
                r"[ぁ-んァ-ヶ]",
                p
            ):

                if len(p) >= 2:
                    comment = p

        result2[car] = {
            "name": "",
            "mark": mark,
            "comment": comment,
            "prediction_score":
                MARK_SCORE.get(
                    mark,
                    0
                ),
        }

    if 5 <= len(result2) <= 7:
        return result2

    lines = [
        clean(x)
        for x in sp.get_text(
            "\n",
            strip=True
        ).splitlines()
        if clean(x)
    ]

    result3 = {}

    for line in lines:

        for m in re.finditer(
            r"(?<!\d)([1-7])\s+([◎○▲△×注])?\s*([^\n]+)",
            line
        ):

            car = int(
                m.group(1)
            )

            rest = clean(
                m.group(3)
            )

            if not rest:
                continue

            mark = m.group(2) or ""

            result3[car] = {
                "name": "",
                "mark": mark,
                "comment": rest,
                "prediction_score":
                    MARK_SCORE.get(
                        mark,
                        0
                    ),
            }

    if 5 <= len(result3) <= 7:
        return result3

    return {}


# =========================================================
# 並び
# =========================================================

def valid_car_set(cars):

    unique = list(
        dict.fromkeys(cars)
    )

    return (
        5 <= len(unique) <= 7
        and set(unique).issubset(
            set(range(1, 8))
        )
    )


def parse_line(sp):

    if not sp:
        return [], []

    candidates = []

    for table in sp.find_all(
        "table"
    ):

        rows = table.find_all(
            "tr"
        )

        for i, tr in enumerate(
            rows
        ):

            cells = [
                clean(
                    c.get_text(
                        " ",
                        strip=True
                    )
                )
                for c in tr.find_all(
                    ["th", "td"]
                )
            ]

            cars = [
                int(v)
                for v in cells
                if re.fullmatch(
                    r"[1-7]",
                    v
                )
            ]

            cars = list(
                dict.fromkeys(cars)
            )

            if not valid_car_set(
                cars
            ):
                continue

            nearby = " ".join(
                clean(
                    x.get_text(
                        " ",
                        strip=True
                    )
                )
                for x in rows[
                    max(0, i - 1):
                    min(len(rows), i + 3)
                ]
            )

            style_score = sum(
                1
                for w in STYLE_WORDS
                if w in nearby
            )

            candidates.append(
                (
                    style_score,
                    cars,
                    cells,
                )
            )

    if candidates:

        candidates.sort(
            key=lambda x: (
                x[0],
                len(x[1]),
            ),
            reverse=True
        )

        _, cars, cells = candidates[0]

        groups = []
        current = []

        for v in cells:

            if re.fullmatch(
                r"[1-7]",
                v
            ):

                current.append(
                    int(v)
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
            x
            for g in groups
            for x in g
        ]

        if set(flat) != set(cars):
            groups = [cars]

        return cars, groups

    lines = [
        clean(x)
        for x in sp.get_text(
            "\n",
            strip=True
        ).splitlines()
        if clean(x)
    ]

    for i, line in enumerate(
        lines
    ):

        nums = [
            int(x)
            for x in re.findall(
                r"(?<!\d)([1-7])(?!\d)",
                line
            )
        ]

        nums = list(
            dict.fromkeys(nums)
        )

        if not valid_car_set(
            nums
        ):
            continue

        nearby = " ".join(
            lines[
                max(0, i - 2):
                min(len(lines), i + 3)
            ]
        )

        if any(
            w in nearby
            for w in STYLE_WORDS
        ):

            return nums, [nums]

    return [], []


# =========================================================
# 展開
# =========================================================

def is_development_text(text):

    t = clean(text)

    if not (
        12 <= len(t) <= 350
    ):
        return False

    bad_words = (
        "予想の並び",
        "並び内の脚質",
        "免責",
        "Copyright",
        "ページ先頭",
        "投票メニュー",
        "ログイン",
        "会員登録",
        "オッズ",
        "出走表",
        "レース一覧",
        "発売",
        "払戻",
    )

    if any(
        x in t
        for x in bad_words
    ):
        return False

    if not re.search(
        r"[ぁ-んァ-ヶ一-龯]",
        t
    ):
        return False

    if not re.search(
        r"[。！？，、]",
        t
    ):
        return False

    return True


def parse_development(sp):

    if not sp:
        return ""

    elements = sp.find_all(
        [
            "tr",
            "td",
            "p",
            "div",
            "li",
        ]
    )

    for i, el in enumerate(
        elements
    ):

        txt = clean(
            el.get_text(
                " ",
                strip=True
            )
        )

        if "←" not in txt:
            continue

        for nxt in elements[
            i + 1:i + 20
        ]:

            cand = clean(
                nxt.get_text(
                    " ",
                    strip=True
                )
            )

            if not cand:
                continue

            if cand == txt:
                continue

            if "←" in cand:
                continue

            if is_development_text(
                cand
            ):
                return cand

    for i, el in enumerate(
        elements
    ):

        txt = clean(
            el.get_text(
                " ",
                strip=True
            )
        )

        if not txt:
            continue

        style_hits = sum(
            1
            for w in STYLE_WORDS
            if w in txt
        )

        if style_hits < 2:
            continue

        for nxt in elements[
            i + 1:i + 15
        ]:

            cand = clean(
                nxt.get_text(
                    " ",
                    strip=True
                )
            )

            if is_development_text(
                cand
            ):
                return cand

    lines = [
        clean(x)
        for x in sp.get_text(
            "\n",
            strip=True
        ).splitlines()
        if clean(x)
    ]

    for i, line in enumerate(
        lines
    ):

        if "展開" in line:

            for cand in lines[
                i + 1:i + 8
            ]:

                if is_development_text(
                    cand
                ):
                    return cand

    candidates = []

    for line in lines:

        if not is_development_text(
            line
        ):
            continue

        if len(line) < 20:
            continue

        score = len(line)

        if any(
            x in line
            for x in (
                "本命",
                "対抗",
                "展開",
                "仕掛け",
                "先行",
                "捲",
                "マーク",
                "番手",
                "ライン",
            )
        ):
            score += 50

        candidates.append(
            (
                score,
                line
            )
        )

    if candidates:

        candidates.sort(
            key=lambda x: x[0],
            reverse=True
        )

        return candidates[0][1]

    return ""


# =========================================================
# 予想取得
# =========================================================

def fetch_prediction(race):

    url = prediction_url(
        race["venue_slug"],
        race["race_no"]
    )

    html = get_html(
        url
    )

    if not html:

        return {
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "url": url,
        }

    sp = soup(
        html
    )

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

    line, line_groups = parse_line(
        sp
    )

    development = parse_development(
        sp
    )

    return {
        "comments": comments,
        "line": line,
        "line_groups": line_groups,
        "development": development,
        "url": url,
    }


# =========================================================
# AIスコア
# =========================================================

def calculate_ai_score(
    rider,
    race
):

    score = 0.0

    base = rider.get(
        "score"
    )

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

    if rider.get(
        "car_no"
    ) in line:

        p = line.index(
            rider["car_no"]
        )

        if p in (
            0,
            2
        ):
            score += 2

        elif p == 1:
            score += 4

    return round(
        score,
        2
    )


# =========================================================
# AI勝負度
# =========================================================

def calculate_verdict(
    race
):

    riders = sorted(
        race.get(
            "riders",
            []
        ),
        key=lambda x: (
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "score"
            ) or 0,
        ),
        reverse=True
    )

    if not riders:

        return {
            "rank": "C",
            "label": "⚠️ 見送り",
            "score": 0,
            "reason": "AI評価対象データ不足",
        }

    main = riders[0].get(
        "ai_score",
        0
    )

    second = (
        riders[1].get(
            "ai_score",
            0
        )
        if len(riders) > 1
        else main
    )

    third = (
        riders[2].get(
            "ai_score",
            0
        )
        if len(riders) > 2
        else second
    )

    verdict_score = 50

    verdict_score += min(
        20,
        max(
            0,
            (main - second) * 2
        )
    )

    if second - third >= 5:
        verdict_score += 8

    if (
        len(
            race.get(
                "line",
                []
            )
        )
        == len(riders)
    ):
        verdict_score += 8

    if race.get(
        "development"
    ):
        verdict_score += 5

    if race.get(
        "prediction_url"
    ):
        verdict_score += 4

    verdict_score = int(
        max(
            0,
            min(
                100,
                verdict_score
            )
        )
    )

    if verdict_score >= 82:

        rank = "S"
        label = "🔥 勝負"

    elif verdict_score >= 72:

        rank = "A"
        label = "🎯 買い"

    elif verdict_score >= 61:

        rank = "B"
        label = "⭐ 少額"

    else:

        rank = "C"
        label = "⚠️ 見送り"

    return {
        "rank": rank,
        "label": label,
        "score": verdict_score,
        "reason": (
            "本命の優位性・ライン・展開を総合判断"
        ),
    }


# =========================================================
# 買い目
# =========================================================

def make_ticket(
    a,
    b,
    c
):

    if None in (
        a,
        b,
        c
    ):
        return ""

    if len({
        a,
        b,
        c
    }) != 3:
        return ""

    return (
        f"{a}-{b}-{c}"
    )


def generate_bets(
    race
):

    riders = sorted(
        race.get(
            "riders",
            []
        ),
        key=lambda x: (
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "score"
            ) or 0,
        ),
        reverse=True
    )

    if len(riders) < 3:
        return []

    main = riders[0]["car_no"]
    second = riders[1]["car_no"]
    third = riders[2]["car_no"]

    fourth = (
        riders[3]["car_no"]
        if len(riders) > 3
        else None
    )

    fifth = (
        riders[4]["car_no"]
        if len(riders) > 4
        else None
    )

    dark = (
        race.get(
            "ai",
            {}
        ).get(
            "dark_horse"
        )
        or third
    )

    candidates = []

    def add(
        a,
        b,
        c,
        score,
        label
    ):

        ticket = make_ticket(
            a,
            b,
            c
        )

        if not ticket:
            return

        candidates.append({
            "ticket": ticket,
            "score": score,
            "type": label,
            "bet_yen": BET_UNIT,
        })

    add(
        main,
        second,
        third,
        100,
        "🔥 AI本線"
    )

    add(
        main,
        third,
        second,
        97,
        "🔥 本線入替"
    )

    add(
        second,
        main,
        third,
        94,
        "⭐ 対抗頭"
    )

    add(
        main,
        second,
        dark,
        92,
        "🎯 穴絡み"
    )

    add(
        main,
        dark,
        second,
        90,
        "🎯 穴入替"
    )

    groups = race.get(
        "line_groups",
        []
    )

    for group in groups:

        if len(group) >= 2:

            a = group[0]
            b = group[1]

            add(
                a,
                b,
                main,
                88,
                "🚴 ライン本線"
            )

            add(
                b,
                a,
                main,
                85,
                "🔄 番手差し"
            )

            add(
                a,
                main,
                b,
                82,
                "⚡ 先頭残り"
            )

    add(
        third,
        main,
        second,
        82,
        "💥 逆転"
    )

    add(
        second,
        third,
        main,
        80,
        "💥 対抗展開"
    )

    add(
        main,
        fourth,
        second,
        76,
        "🎯 中穴"
    )

    add(
        fourth,
        main,
        third,
        72,
        "💥 高配当"
    )

    add(
        main,
        dark,
        fifth,
        68,
        "💣 大穴警戒"
    )

    add(
        dark,
        main,
        second,
        65,
        "💣 穴頭"
    )

    line = set(
        race.get(
            "line",
            []
        )
    )

    for item in candidates:

        nums = [
            int(x)
            for x in item[
                "ticket"
            ].split("-")
        ]

        item["score"] += sum(
            2
            for n in nums
            if n in line
        )

    unique = {}

    for item in candidates:

        ticket = item[
            "ticket"
        ]

        if (
            ticket not in unique
            or item["score"]
            > unique[ticket]["score"]
        ):

            unique[ticket] = item

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            x["score"],
            -int(
                x["ticket"].replace(
                    "-",
                    ""
                )
            ),
        ),
        reverse=True
    )

    return result[:MAX_BETS]


# =========================================================
# AI適用
# =========================================================

def apply_ai(
    race
):

    riders = race.get(
        "riders",
        []
    )

    for rider in riders:

        rider[
            "ai_score"
        ] = calculate_ai_score(
            rider,
            race
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
            ) or 0,
        ),
        reverse=True
    )

    race["ai"] = {

        "main": (
            ranking[0]["car_no"]
            if ranking
            else None
        ),

        "opponent": (
            ranking[1]["car_no"]
            if len(ranking) > 1
            else None
        ),

        "dark_horse": (
            ranking[2]["car_no"]
            if len(ranking) > 2
            else None
        ),

        "ranking": [
            {
                "car_no":
                    r["car_no"],

                "name":
                    r["name"],

                "score":
                    r["ai_score"],

                "mark":
                    r.get(
                        "prediction_mark",
                        ""
                    ),
            }
            for r in ranking
        ],
    }

    race["verdict"] = calculate_verdict(
        race
    )

    race["bets"] = generate_bets(
        race
    )


# =========================================================
# 結果解析
# =========================================================

def parse_result_page(
    html
):

    if not html:

        return {
            "finished": False,
            "finish": [],
            "payout_3tan": None,
            "payout_3tan_yen": 0,
        }

    sp = soup(
        html
    )

    if not sp:

        return {
            "finished": False,
            "finish": [],
            "payout_3tan": None,
            "payout_3tan_yen": 0,
        }

    text = clean(
        sp.get_text(
            " ",
            strip=True
        )
    )

    finish = []

    for tr in sp.find_all(
        "tr"
    ):

        cells = [
            clean(
                c.get_text(
                    " ",
                    strip=True
                )
            )
            for c in tr.find_all(
                ["th", "td"]
            )
        ]

        if not cells:
            continue

        place = None
        car = None

        for v in cells:

            if re.fullmatch(
                r"[1-7]",
                v
            ):

                if place is None:
                    place = int(v)

                elif car is None:
                    car = int(v)

        if (
            place is not None
            and car is not None
            and 1 <= place <= 7
            and 1 <= car <= 7
        ):

            finish.append(
                (
                    place,
                    car
                )
            )

    finish_map = {}

    for place, car in finish:

        if place not in finish_map:
            finish_map[place] = car

    ordered_finish = [
        finish_map[p]
        for p in sorted(
            finish_map
        )
        if 1 <= p <= 7
    ]

    if len(ordered_finish) < 3:

        patterns_finish = [
            r"1\s*着.*?([1-7]).*?"
            r"2\s*着.*?([1-7]).*?"
            r"3\s*着.*?([1-7])",

            r"1着.*?([1-7])\D+"
            r"2着.*?([1-7])\D+"
            r"3着.*?([1-7])",
        ]

        for pattern in patterns_finish:

            m = re.search(
                pattern,
                text
            )

            if m:

                ordered_finish = [
                    int(x)
                    for x in m.groups()
                ]

                break

    payout_ticket = None
    payout_yen = 0

    payout_patterns = [

        r"3連単.{0,80}?"
        r"([1-7])\s*[→＞>]\s*"
        r"([1-7])\s*[→＞>]\s*"
        r"([1-7]).{0,40}?"
        r"([\d,]+)\s*円",

        r"3連単.{0,80}?"
        r"([1-7])\s*[-−]\s*"
        r"([1-7])\s*[-−]\s*"
        r"([1-7]).{0,40}?"
        r"([\d,]+)\s*円",

        r"3連単.{0,80}?"
        r"([1-7])\s*-\s*"
        r"([1-7])\s*-\s*"
        r"([1-7]).{0,40}?"
        r"([\d,]+)",
    ]

    for pattern in payout_patterns:

        m = re.search(
            pattern,
            text
        )

        if m:

            payout_ticket = (
                f"{m.group(1)}-"
                f"{m.group(2)}-"
                f"{m.group(3)}"
            )

            try:

                payout_yen = int(
                    m.group(4).replace(
                        ",",
                        ""
                    )
                )

            except ValueError:
                payout_yen = 0

            break

    if (
        not payout_ticket
        and len(ordered_finish) >= 3
    ):

        payout_ticket = (
            f"{ordered_finish[0]}-"
            f"{ordered_finish[1]}-"
            f"{ordered_finish[2]}"
        )

    finished = (
        len(ordered_finish) >= 3
        or bool(
            payout_ticket
        )
    )

    return {
        "finished": finished,
        "finish": ordered_finish,
        "payout_3tan": payout_ticket,
        "payout_3tan_yen": payout_yen,
    }


# =========================================================
# 結果取得
# =========================================================

def fetch_result(
    race
):

    urls = []

    discovered = race.get(
        "result_url",
        ""
    )

    if discovered:
        urls.append(
            discovered
        )

    urls.append(
        result_url(
            race["venue_code"],
            race["race_no"]
        )
    )

    checked = set()

    for url in urls:

        if not url:
            continue

        if url in checked:
            continue

        checked.add(url)

        html = get_html(
            url
        )

        result = parse_result_page(
            html
        )

        if result.get(
            "finished"
        ):

            result["url"] = url

            return result

    return {
        "finished": False,
        "finish": [],
        "payout_3tan": None,
        "payout_3tan_yen": 0,
        "url": urls[0]
        if urls
        else "",
    }


# =========================================================
# オッズ解析
# =========================================================

def parse_odds_page(
    html
):

    if not html:

        return {
            "available": False,
            "odds": {},
        }

    sp = soup(
        html
    )

    if not sp:

        return {
            "available": False,
            "odds": {},
        }

    odds = {}

    # -----------------------------------------------------
    # ① テーブルの行
    # -----------------------------------------------------

    for tr in sp.find_all(
        "tr"
    ):

        cells = [
            clean(
                c.get_text(
                    " ",
                    strip=True
                )
            )
            for c in tr.find_all(
                ["th", "td"]
            )
        ]

        if not cells:
            continue

        for i, cell in enumerate(
            cells
        ):

            m = re.fullmatch(
                r"([1-7])\s*[→＞>]\s*"
                r"([1-7])\s*[→＞>]\s*"
                r"([1-7])",
                cell
            )

            if not m:

                m = re.fullmatch(
                    r"([1-7])\s*[-−]\s*"
                    r"([1-7])\s*[-−]\s*"
                    r"([1-7])",
                    cell
                )

            if not m:
                continue

            ticket = (
                f"{m.group(1)}-"
                f"{m.group(2)}-"
                f"{m.group(3)}"
            )

            candidates = []

            # 同じ行の数字
            for j, other in enumerate(
                cells
            ):

                if j == i:
                    continue

                om = re.fullmatch(
                    r"(\d+(?:\.\d+)?)",
                    other
                )

                if om:

                    try:

                        value = float(
                            om.group(1)
                        )

                        if (
                            0 < value < 100000
                        ):
                            candidates.append(
                                value
                            )

                    except ValueError:
                        pass

            if candidates:
                odds[ticket] = candidates[0]

    # -----------------------------------------------------
    # ② 本文
    # -----------------------------------------------------

    text = clean(
        sp.get_text(
            " ",
            strip=True
        )
    )

    patterns = [

        r"([1-7])\s*→\s*"
        r"([1-7])\s*→\s*"
        r"([1-7])\s+"
        r"(\d+(?:\.\d+)?)",

        r"([1-7])\s*＞\s*"
        r"([1-7])\s*＞\s*"
        r"([1-7])\s+"
        r"(\d+(?:\.\d+)?)",

        r"([1-7])\s*>\s*"
        r"([1-7])\s*>\s*"
        r"([1-7])\s+"
        r"(\d+(?:\.\d+)?)",

        r"([1-7])\s*-\s*"
        r"([1-7])\s*-\s*"
        r"([1-7])\s+"
        r"(\d+(?:\.\d+)?)",

        r"([1-7])\s*−\s*"
        r"([1-7])\s*−\s*"
        r"([1-7])\s+"
        r"(\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:

        for m in re.finditer(
            pattern,
            text
        ):

            ticket = (
                f"{m.group(1)}-"
                f"{m.group(2)}-"
                f"{m.group(3)}"
            )

            try:

                value = float(
                    m.group(4)
                )

            except ValueError:
                continue

            if (
                0 < value < 100000
            ):

                odds[ticket] = value

    # -----------------------------------------------------
    # ③ テーブル単位
    # -----------------------------------------------------

    if not odds:

        for table in sp.find_all(
            "table"
        ):

            table_text = clean(
                table.get_text(
                    " ",
                    strip=True
                )
            )

            for m in re.finditer(
                r"([1-7])\s*→\s*"
                r"([1-7])\s*→\s*"
                r"([1-7])\s+"
                r"(\d+(?:\.\d+)?)",
                table_text
            ):

                ticket = (
                    f"{m.group(1)}-"
                    f"{m.group(2)}-"
                    f"{m.group(3)}"
                )

                try:

                    value = float(
                        m.group(4)
                    )

                except ValueError:
                    continue

                if (
                    0 < value < 100000
                ):

                    odds[ticket] = value

    return {
        "available": len(odds) > 0,
        "odds": odds,
    }


# =========================================================
# オッズ取得
# =========================================================

def fetch_odds(
    race
):

    code = str(
        race["venue_code"]
    )

    race_no = int(
        race["race_no"]
    )

    urls = []

    discovered = race.get(
        "odds_url",
        ""
    )

    if discovered:
        urls.append(
            discovered
        )

    # 直接SPオッズ
    urls.append(
        odds_url(
            code,
            race_no
        )
    )

    # betTypeなし
    urls.append(
        f"{SP_BASE_URL}/keirin/"
        f"SpOddsInfo.do"
        f"?joCd={code}"
        f"&joCode={code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )

    checked = set()

    for url in urls:

        if not url:
            continue

        if url in checked:
            continue

        checked.add(url)

        html = get_html(
            url
        )

        if not html:
            continue

        parsed = parse_odds_page(
            html
        )

        if parsed.get(
            "available"
        ):

            parsed["url"] = url

            return parsed

    return {
        "available": False,
        "odds": {},
        "url": (
            urls[0]
            if urls
            else ""
        ),
    }


# =========================================================
# 買い目にオッズ情報を追加
# =========================================================

def attach_bet_odds(
    race
):

    odds_data = race.get(
        "odds",
        {}
    )

    odds_map = odds_data.get(
        "odds",
        {}
    )

    frozen = race.get(
        "frozen_prediction",
        {}
    )

    bets = frozen.get(
        "bets",
        []
    )

    if not bets:
        return

    sorted_odds = sorted(
        odds_map.items(),
        key=lambda x: x[1]
    )

    rank_map = {
        ticket: i
        for i, (
            ticket,
            value
        ) in enumerate(
            sorted_odds,
            start=1
        )
    }

    for bet in bets:

        ticket = bet.get(
            "ticket",
            ""
        )

        value = odds_map.get(
            ticket
        )

        if value is None:

            bet["odds"] = None
            bet["market_rank"] = None
            bet["value_flag"] = (
                "オッズ未取得"
            )

            continue

        bet["odds"] = value

        bet["market_rank"] = rank_map.get(
            ticket
        )

        if value >= 30:

            flag = (
                "💎 穴・期待値候補"
            )

        elif value >= 15:

            flag = "🎯 中穴"

        elif value >= 8:

            flag = "⭐ 適正"

        else:

            flag = "⚠️ 人気"

        bet["value_flag"] = flag


# =========================================================
# 買い目精算
# =========================================================

def settle_bets(
    race
):

    bets = race.get(
        "frozen_prediction",
        {}
    ).get(
        "bets",
        []
    )

    result = race.get(
        "result",
        {}
    )

    total_bet = sum(
        int(
            b.get(
                "bet_yen",
                BET_UNIT
            )
        )
        for b in bets
    )

    # -----------------------------------------------------
    # 未確定
    # -----------------------------------------------------

    if not result.get(
        "finished"
    ):

        return {
            "status": "pending",
            "bet_count": len(bets),
            "investment": total_bet,
            "hit": False,
            "hit_ticket": "",
            "payout": 0,
            "profit": 0,
            "roi": 0,
        }

    payout_ticket = result.get(
        "payout_3tan"
    )

    payout_yen = int(
        result.get(
            "payout_3tan_yen",
            0
        )
        or 0
    )

    hit = False
    hit_ticket = ""

    for b in bets:

        if (
            b.get("ticket")
            == payout_ticket
        ):

            hit = True

            hit_ticket = b[
                "ticket"
            ]

            break

    payout = (
        payout_yen
        if hit
        else 0
    )

    profit = (
        payout
        - total_bet
    )

    roi = (
        payout
        / total_bet
        * 100
        if total_bet > 0
        else 0
    )

    return {
        "status": "settled",
        "bet_count": len(bets),
        "investment": total_bet,
        "hit": hit,
        "hit_ticket": hit_ticket,
        "payout": payout,
        "profit": profit,
        "roi": round(
            roi,
            1
        ),
    }


# =========================================================
# 既存データ
# =========================================================

def load_existing():

    path = "data/today.json"

    if not os.path.exists(
        path
    ):
        return {}

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if data.get(
            "target_date"
        ) == TODAY_DISPLAY:

            return data

    except Exception:
        pass

    return {}


# =========================================================
# AI予想凍結
# =========================================================

def freeze_prediction(
    race,
    existing_race=None
):

    # 既存の凍結予想は絶対に変更しない
    if existing_race:

        old = existing_race.get(
            "frozen_prediction"
        )

        if old:

            race[
                "frozen_prediction"
            ] = old

            return

    verdict = race.get(
        "verdict",
        {}
    )

    ai = race.get(
        "ai",
        {}
    )

    bets = race.get(
        "bets",
        []
    )

    race[
        "frozen_prediction"
    ] = {

        "created_at":
            datetime.now().isoformat(),

        "main":
            ai.get(
                "main"
            ),

        "opponent":
            ai.get(
                "opponent"
            ),

        "dark_horse":
            ai.get(
                "dark_horse"
            ),

        "verdict":
            verdict,

        "ranking":
            ai.get(
                "ranking",
                []
            ),

        "bets":
            bets,
    }


# =========================================================
# 収支集計
# =========================================================

def calculate_summary(
    races
):

    total_races = len(
        races
    )

    investment = 0
    payout = 0
    hit_races = 0

    settled_races = 0
    pending_races = 0

    by_rank = {

        "S": {
            "races": 0,
            "investment": 0,
            "payout": 0,
            "profit": 0,
            "hits": 0,
        },

        "A": {
            "races": 0,
            "investment": 0,
            "payout": 0,
            "profit": 0,
            "hits": 0,
        },

        "B": {
            "races": 0,
            "investment": 0,
            "payout": 0,
            "profit": 0,
            "hits": 0,
        },

        "C": {
            "races": 0,
            "investment": 0,
            "payout": 0,
            "profit": 0,
            "hits": 0,
        },
    }

    by_venue = {}

    for race in races:

        settlement = race.get(
            "settlement",
            {}
        )

        status = settlement.get(
            "status"
        )

        # 未確定は収支に入れない
        if status != "settled":

            pending_races += 1

            continue

        settled_races += 1

        inv = int(
            settlement.get(
                "investment",
                0
            )
        )

        pay = int(
            settlement.get(
                "payout",
                0
            )
        )

        profit = (
            pay - inv
        )

        investment += inv
        payout += pay

        if settlement.get(
            "hit"
        ):

            hit_races += 1

        rank = (
            race.get(
                "frozen_prediction",
                {}
            )
            .get(
                "verdict",
                {}
            )
            .get(
                "rank",
                "C"
            )
        )

        if rank not in by_rank:
            rank = "C"

        by_rank[rank][
            "races"
        ] += 1

        by_rank[rank][
            "investment"
        ] += inv

        by_rank[rank][
            "payout"
        ] += pay

        by_rank[rank][
            "profit"
        ] += profit

        if settlement.get(
            "hit"
        ):

            by_rank[rank][
                "hits"
            ] += 1

        venue = race.get(
            "venue_name",
            ""
        )

        if venue not in by_venue:

            by_venue[venue] = {
                "races": 0,
                "investment": 0,
                "payout": 0,
                "profit": 0,
                "hits": 0,
            }

        by_venue[venue][
            "races"
        ] += 1

        by_venue[venue][
            "investment"
        ] += inv

        by_venue[venue][
            "payout"
        ] += pay

        by_venue[venue][
            "profit"
        ] += profit

        if settlement.get(
            "hit"
        ):

            by_venue[venue][
                "hits"
            ] += 1

    profit = (
        payout
        - investment
    )

    roi = (
        payout
        / investment
        * 100
        if investment > 0
        else 0
    )

    return {

        "races":
            total_races,

        "settled_races":
            settled_races,

        "pending_races":
            pending_races,

        "investment":
            investment,

        "payout":
            payout,

        "profit":
            profit,

        "hits":
            hit_races,

        "roi":
            round(
                roi,
                1
            ),

        "by_rank":
            by_rank,

        "by_venue":
            by_venue,
    }


# =========================================================
# MAIN
# =========================================================

def main():

    started = time.time()

    print(
        "=============================="
    )

    print(
        f" KEIRIN AI DATA UPDATE v{VERSION}"
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

    existing = load_existing()

    existing_races = {}

    for r in existing.get(
        "races",
        []
    ):

        key = (
            str(
                r.get(
                    "venue_code"
                )
            ),
            int(
                r.get(
                    "race_no",
                    0
                )
            ),
        )

        existing_races[key] = r

    if existing_races:

        print(
            f"既存データ: "
            f"{len(existing_races)}レース"
        )

        print(
            "凍結済みAI予想を保護します"
        )

    venues = [
        {
            "code": code,
            "name": name,
            "slug": slug,
        }
        for code, (
            name,
            slug
        ) in VENUES.items()
    ]

    # =====================================================
    # 開催場
    # =====================================================

    print(
        "開催場を確認中..."
    )

    print(
        f"開催場数: {len(venues)}"
    )

    # =====================================================
    # 実在レース
    # =====================================================

    print(
        "実在レースを実ページで確認中..."
    )

    all_races = []

    for venue in venues:

        races = discover_real_races(
            venue
        )

        venue[
            "race_numbers"
        ] = [
            r["race_no"]
            for r in races
        ]

        print(
            f"  {venue['code']} "
            f"{venue['name']}: "
            f"{len(races)}レース"
        )

        all_races.extend(
            races
        )

    all_races.sort(
        key=lambda x: (
            int(
                x["venue_code"]
            ),
            x["race_no"],
        )
    )

    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    rider_success = sum(
        5 <= len(
            r["riders"]
        ) <= 7
        for r in all_races
    )

    print(
        f"選手データ取得結果: "
        f"{rider_success}/"
        f"{len(all_races)}"
    )

    # =====================================================
    # 予想
    # =====================================================

    print(
        "コメント・並び・展開情報取得中..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as ex:

        futures = {
            ex.submit(
                fetch_prediction,
                r
            ): r
            for r in all_races
        }

        for f in as_completed(
            futures
        ):

            race = futures[f]

            try:

                p = f.result()

            except Exception:

                p = {
                    "comments": {},
                    "line": [],
                    "line_groups": [],
                    "development": "",
                    "url": "",
                }

            race.update({

                "comments":
                    p["comments"],

                "line":
                    p["line"],

                "line_groups":
                    p["line_groups"],

                "development":
                    p["development"],

                "prediction_url":
                    p["url"],
            })

    # =====================================================
    # 選手へ予想情報反映
    # =====================================================

    for race in all_races:

        comments = race.get(
            "comments",
            {}
        )

        for rider in race[
            "riders"
        ]:

            info = comments.get(
                rider["car_no"],
                {}
            )

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

        # AI
        apply_ai(
            race
        )

        # 凍結
        key = (
            str(
                race[
                    "venue_code"
                ]
            ),
            int(
                race[
                    "race_no"
                ]
            ),
        )

        freeze_prediction(
            race,
            existing_races.get(
                key
            )
        )

    # =====================================================
    # 結果取得
    # =====================================================

    print(
        "レース結果を確認中..."
    )

    result_count = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as ex:

        futures = {
            ex.submit(
                fetch_result,
                r
            ): r
            for r in all_races
        }

        for f in as_completed(
            futures
        ):

            race = futures[f]

            try:

                result = f.result()

            except Exception:

                result = {
                    "finished": False,
                    "finish": [],
                    "payout_3tan": None,
                    "payout_3tan_yen": 0,
                    "url": "",
                }

            race[
                "result"
            ] = result

            if result.get(
                "finished"
            ):

                result_count += 1

    print(
        f"結果取得: "
        f"{result_count}/"
        f"{len(all_races)}"
    )

    # =====================================================
    # オッズ取得
    # =====================================================

    print(
        "オッズ情報を確認中..."
    )

    odds_count = 0
    odds_failed = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as ex:

        futures = {
            ex.submit(
                fetch_odds,
                r
            ): r
            for r in all_races
        }

        for f in as_completed(
            futures
        ):

            race = futures[f]

            key = (
                str(
                    race[
                        "venue_code"
                    ]
                ),
                int(
                    race[
                        "race_no"
                    ]
                ),
            )

            old_race = existing_races.get(
                key,
                {}
            )

            old_odds = old_race.get(
                "odds",
                {}
            )

            try:

                odds = f.result()

            except Exception as e:

                odds = {
                    "available": False,
                    "odds": {},
                    "url": "",
                    "error": str(e),
                }

            # 既存オッズを優先して保持
            if (
                old_odds.get(
                    "available"
                )
                and old_odds.get(
                    "odds"
                )
            ):

                race[
                    "odds"
                ] = old_odds

            else:

                race[
                    "odds"
                ] = odds

            if race[
                "odds"
            ].get(
                "available"
            ):

                odds_count += 1

            else:

                odds_failed.append(
                    f"{race['venue_name']} "
                    f"{race['race_no']}R"
                )

    print(
        f"オッズ取得: "
        f"{odds_count}/"
        f"{len(all_races)}"
    )

    if odds_failed:

        print(
            "オッズ未取得: "
            + ", ".join(
                odds_failed
            )
        )

    # =====================================================
    # 買い目へオッズ情報
    # =====================================================

    for race in all_races:

        attach_bet_odds(
            race
        )

    # =====================================================
    # 精算
    # =====================================================

    for race in all_races:

        race[
            "settlement"
        ] = settle_bets(
            race
        )

    # =====================================================
    # 集計用データ
    # =====================================================

    prediction_races = sum(
        len(
            r.get(
                "comments",
                {}
            )
        )
        == len(
            r["riders"]
        )
        and len(
            r["riders"]
        ) >= 5
        for r in all_races
    )

    comment_count = sum(
        len(
            r.get(
                "comments",
                {}
            )
        )
        for r in all_races
    )

    line_races = sum(
        len(
            r.get(
                "line",
                []
            )
        )
        == len(
            r["riders"]
        )
        and len(
            r["riders"]
        ) >= 5
        for r in all_races
    )

    development_races = sum(
        bool(
            r.get(
                "development"
            )
        )
        for r in all_races
    )

    rider_count = sum(
        len(
            r["riders"]
        )
        for r in all_races
    )

    correct_names = sum(
        bool(
            rider.get(
                "name"
            )
        )
        for race in all_races
        for rider in race[
            "riders"
        ]
    )

    ai_race_count = sum(
        bool(
            r.get(
                "ai"
            )
        )
        for r in all_races
    )

    ai_rider_count = sum(
        r.get(
            "ai_score"
        ) is not None
        for race in all_races
        for r in race[
            "riders"
        ]
    )

    frozen_count = sum(
        bool(
            r.get(
                "frozen_prediction"
            )
        )
        for r in all_races
    )

    # =====================================================
    # 詳細診断
    # =====================================================

    failed_riders = []
    failed_predictions = []
    failed_lines = []
    failed_development = []

    for r in all_races:

        label = (
            f"{r['venue_name']} "
            f"{r['race_no']}R"
        )

        if not 5 <= len(
            r["riders"]
        ) <= 7:

            failed_riders.append(
                label
            )

        if len(
            r.get(
                "comments",
                {}
            )
        ) != len(
            r["riders"]
        ):

            failed_predictions.append(
                label
            )

        if len(
            r.get(
                "line",
                []
            )
        ) != len(
            r["riders"]
        ):

            failed_lines.append(
                label
            )

        if not r.get(
            "development"
        ):

            failed_development.append(
                label
            )

    # =====================================================
    # 完全性
    # =====================================================

    data_complete = (
        len(all_races) > 0
        and rider_success
        == len(all_races)
        and correct_names
        == rider_count
        and ai_race_count
        == len(all_races)
        and ai_rider_count
        == rider_count
        and frozen_count
        == len(all_races)
        and not failed_riders
    )

    # =====================================================
    # 収支
    # =====================================================

    summary = calculate_summary(
        all_races
    )

    # =====================================================
    # JSON
    # =====================================================

    output = {

        "version":
            VERSION,

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

        "frozen_prediction_count":
            frozen_count,

        "result_count":
            result_count,

        "odds_count":
            odds_count,

        "summary":
            summary,

        "venues": [
            {
                "venue_code":
                    v["code"],

                "venue_name":
                    v["name"],

                "race_count":
                    len(
                        v[
                            "race_numbers"
                        ]
                    ),

                "race_numbers":
                    v[
                        "race_numbers"
                    ],
            }
            for v in venues
        ],

        "races":
            all_races,
    }

    # =====================================================
    # 保存
    # =====================================================

    os.makedirs(
        "data",
        exist_ok=True
    )

    path = (
        "data/today.json"
    )

    tmp = (
        path
        + ".tmp"
    )

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

    # =====================================================
    # 結果表示
    # =====================================================

    elapsed = (
        time.time()
        - started
    )

    print(
        "=============================="
    )

    print(
        f" v{VERSION} 取得結果"
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
        f"正しい選手名: "
        f"{correct_names}"
    )

    print(
        f"AI予想凍結: "
        f"{frozen_count}"
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
        f"結果取得: "
        f"{result_count}"
    )

    print(
        f"オッズ取得: "
        f"{odds_count}"
    )

    print(
        "=============================="
    )

    print(
        " 【AI収支】"
    )

    print(
        "=============================="
    )

    print(
        f"確定レース: "
        f"{summary['settled_races']}"
    )

    print(
        f"未確定レース: "
        f"{summary['pending_races']}"
    )

    print(
        f"投資額: "
        f"¥{summary['investment']:,}"
    )

    print(
        f"払戻: "
        f"¥{summary['payout']:,}"
    )

    print(
        f"収支: "
        f"{summary['profit']:+,}円"
    )

    print(
        f"的中レース: "
        f"{summary['hits']}"
    )

    print(
        f"回収率: "
        f"{summary['roi']:.1f}%"
    )

    print(
        "=============================="
    )

    print(
        " 【S/A/B/C別収支】"
    )

    print(
        "=============================="
    )

    for rank in (
        "S",
        "A",
        "B",
        "C",
    ):

        s = summary[
            "by_rank"
        ][rank]

        print(
            f"{rank}: "
            f"{s['races']}R / "
            f"投資¥{s['investment']:,} / "
            f"払戻¥{s['payout']:,} / "
            f"収支{s['profit']:+,}円 / "
            f"的中{s['hits']}"
        )

    print(
        "=============================="
    )

    print(
        f"取得失敗レース: "
        f"{len(failed_riders)}"
    )

    print(
        f"オッズ未取得レース: "
        f"{len(odds_failed)}"
    )

    print(
        f"処理時間: "
        f"{elapsed:.1f}秒"
    )

    print(
        "データ完全性: "
        + (
            "OK"
            if data_complete
            else "要確認"
        )
    )

    print(
        "=============================="
    )

    print(
        " 詳細診断"
    )

    print(
        "=============================="
    )

    print(
        "選手データ不足: "
        + (
            ", ".join(
                failed_riders
            )
            if failed_riders
            else "なし"
        )
    )

    print(
        "予想情報不足: "
        + (
            ", ".join(
                failed_predictions
            )
            if failed_predictions
            else "なし"
        )
    )

    print(
        "並び不足: "
        + (
            ", ".join(
                failed_lines
            )
            if failed_lines
            else "なし"
        )
    )

    print(
        "展開コメント不足: "
        + (
            ", ".join(
                failed_development
            )
            if failed_development
            else "なし"
        )
    )

    if odds_failed:

        print(
            "オッズ未取得: "
            + ", ".join(
                odds_failed
            )
        )

    else:

        print(
            "オッズ未取得: なし"
        )

    print(
        f"保存先: {path}"
    )

    print(
        "=============================="
    )

    print(
        " UPDATE COMPLETE"
    )

    print(
        "=============================="
    )


if __name__ == "__main__":
    main()
