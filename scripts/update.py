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

VERSION = "5.6"

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

TIMEOUT = 15
RETRIES = 1
MAX_WORKERS = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}

VENUES = {
    "22": ("前橋", "maebashi"),
    "25": ("大宮", "omiya"),
    "38": ("静岡", "shizuoka"),
    "44": ("大垣", "ogaki"),
    "48": ("四日市", "yokkaichi"),
    "53": ("奈良", "nara"),
    "74": ("高知", "kochi"),
}

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

GRADES = {"S1", "S2", "A1", "A2", "A3", "L1", "L2"}


# =========================================================
# 基本
# =========================================================

def clean(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("　", " ")

    return re.sub(r"\s+", " ", text).strip()


def get_html(url):
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            if r.status_code == 200 and len(r.text) > 300:
                r.encoding = r.apparent_encoding or r.encoding
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
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


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


def numbers4(text):
    m = re.search(
        r"(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)",
        text,
    )

    if not m:
        return [None, None, None, None]

    return [int(x) for x in m.groups()]


# =========================================================
# 出走表
# =========================================================

def parse_rider_row(tr):
    text = clean(tr.get_text(" ", strip=True))

    if not text:
        return None

    # -----------------------------------------------------
    # 車番
    # -----------------------------------------------------

    car = None

    # 最初の数字を車番として見る
    mcar = re.search(r"\b([1-7])\b", text)

    if mcar:
        car = int(mcar.group(1))

    if car is None or not 1 <= car <= 7:
        return None

    # -----------------------------------------------------
    # 選手名
    #
    # v5.5では PlayerDetail リンクだけに依存したため
    # 一部ページで選手を落とした。
    #
    # v5.6では複数方式で取得する。
    # -----------------------------------------------------

    name = ""

    # ① PlayerDetail
    for a in tr.find_all("a"):
        href = a.get("href", "")
        label = clean(a.get_text(" ", strip=True))

        if (
            label
            and (
                "PlayerDetail.do" in href
                or "player" in href.lower()
            )
        ):
            if not label in MARK_SCORE:
                name = label
                break

    # ② テキスト内の「車番直後」
    if not name:
        cells = [
            clean(c.get_text(" ", strip=True))
            for c in tr.find_all(["th", "td"])
        ]

        for i, v in enumerate(cells):
            if v == str(car):
                for nxt in cells[i + 1:i + 5]:
                    if not nxt:
                        continue

                    if nxt in MARK_SCORE:
                        continue

                    if re.fullmatch(r"[1-7]", nxt):
                        continue

                    if re.fullmatch(r"\d+", nxt):
                        continue

                    # 選手名らしい日本語
                    if re.search(r"[一-龯ぁ-んァ-ヶ]", nxt):
                        if not any(x in nxt for x in (
                            "競走得点",
                            "着順",
                            "決まり手",
                            "今場所",
                            "前場所",
                        )):
                            name = nxt
                            break

    # ③ テキスト解析
    if not name:
        parts = [
            clean(x)
            for x in tr.stripped_strings
            if clean(x)
        ]

        for i, part in enumerate(parts):
            if part == str(car):
                for nxt in parts[i + 1:i + 6]:
                    if (
                        nxt
                        and nxt not in MARK_SCORE
                        and re.search(r"[一-龯]", nxt)
                        and len(nxt) <= 12
                    ):
                        name = nxt
                        break

            if name:
                break

    if not name:
        return None

    # -----------------------------------------------------
    # 年齢・期別
    # -----------------------------------------------------

    age = None
    period = None

    m = re.search(
        r"(\d{1,2})歳\s*[／/]\s*(\d{2,3})期",
        text,
    )

    if m:
        age = int(m.group(1))
        period = int(m.group(2))

    # -----------------------------------------------------
    # 府県
    # -----------------------------------------------------

    prefecture = ""

    if m:
        tail = text[m.end():]

        for p in sorted(PREFECTURES, key=len, reverse=True):
            if p in tail:
                prefecture = p
                break

    if not prefecture:
        for p in sorted(PREFECTURES, key=len, reverse=True):
            if p in text:
                prefecture = p
                break

    # -----------------------------------------------------
    # 級班
    # -----------------------------------------------------

    grade = ""

    mg = re.search(r"\b([SAL]\d)\b", text)

    if mg:
        grade = mg.group(1)

    # -----------------------------------------------------
    # 脚質
    # -----------------------------------------------------

    style = ""

    for s in ("逃", "捲", "追", "両"):
        if re.search(rf"\s{s}\s*[|｜]", text):
            style = s
            break

    if not style:
        for s in ("逃", "捲", "追", "両"):
            if re.search(rf"\s{s}\s+", text):
                style = s
                break

    # -----------------------------------------------------
    # 競走得点
    # -----------------------------------------------------

    score = None

    ms = re.search(
        r"競走得点\s*[:：]?\s*(\d+(?:\.\d+)?)",
        text,
    )

    if ms:
        score = float(ms.group(1))

    # -----------------------------------------------------
    # 着順
    # -----------------------------------------------------

    finish_text = ""

    mf = re.search(
        r"着\s*順\s*[:：]?\s*([^|｜]+)",
        text,
    )

    if mf:
        finish_text = mf.group(1)

    finish = numbers4(finish_text)

    # -----------------------------------------------------
    # 決まり手
    # -----------------------------------------------------

    kimari_text = ""

    mk = re.search(
        r"決まり手\s*[:：]?\s*([^|｜]+)",
        text,
    )

    if mk:
        kimari_text = mk.group(1)

    kimari = numbers4(kimari_text)

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
            best[rider["car_no"]] = rider

    if 5 <= len(best) <= 7:
        return [
            best[k]
            for k in sorted(best)
        ]

    return []


def fetch_race(job):
    url = race_url(
        job["venue_code"],
        job["race_no"],
    )

    sp = soup(get_html(url))

    riders = parse_riders(sp) if sp else []

    return {
        **job,
        "url": url,
        "riders": riders,
        "success": 5 <= len(riders) <= 7,
    }


def discover_real_races(venue):
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
            ex.submit(fetch_race, j)
            for j in jobs
        ]

        for f in as_completed(futures):
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
# 予想ページ
# =========================================================

def extract_car(value):
    value = clean(value)

    m = re.fullmatch(r"[1-7]", value)

    if m:
        return int(value)

    return None


def parse_prediction_table(sp):
    """
    予想表を複数方式で解析。

    v5.5では
      「車・印・選手名・コメント」
    の完全一致に依存していたため、
    ページによって0件になった。

    v5.6では
      ① テーブル解析
      ② 行セル解析
      ③ ページテキスト解析
    の順でフォールバック。
    """

    if not sp:
        return {}

    result = {}

    # =====================================================
    # ① テーブル方式
    # =====================================================

    for table in sp.find_all("table"):

        rows = table.find_all("tr")

        for tr in rows:

            cells = [
                clean(c.get_text(" ", strip=True))
                for c in tr.find_all(["th", "td"])
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

            # 選手名候補
            name = ""

            for v in cells:
                if (
                    not v
                    or v == str(car)
                    or v == mark
                    or v in MARK_SCORE
                ):
                    continue

                if re.fullmatch(r"\d+(?:\.\d+)?", v):
                    continue

                if any(x in v for x in (
                    "選手名",
                    "コメント",
                    "予想",
                    "並び",
                )):
                    continue

                if re.search(r"[一-龯]", v):
                    name = v
                    break

            # コメント
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

                if any(x in v for x in (
                    "選手名",
                    "コメント",
                )):
                    continue

                if re.search(r"[ぁ-んァ-ヶ]", v):
                    comments.append(v)

            comment = " ".join(comments)

            if name or comment or mark:
                result[car] = {
                    "name": name,
                    "mark": mark,
                    "comment": comment,
                    "prediction_score": MARK_SCORE.get(
                        mark,
                        0,
                    ),
                }

    if 5 <= len(result) <= 7:
        return result

    # =====================================================
    # ② 行ベース方式
    # =====================================================

    result2 = {}

    for tr in sp.find_all("tr"):

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

        car = int(cars[0])

        if not 1 <= car <= 7:
            continue

        mark = ""

        for m in MARK_SCORE:
            if m in text:
                mark = m
                break

        # 行から明らかなヘッダーを除外
        if "選手名" in text:
            continue

        # コメント候補
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

            if re.search(r"[ぁ-んァ-ヶ]", p):
                if len(p) >= 2:
                    comment = p

        result2[car] = {
            "name": "",
            "mark": mark,
            "comment": comment,
            "prediction_score": MARK_SCORE.get(
                mark,
                0,
            ),
        }

    if 5 <= len(result2) <= 7:
        return result2

    # =====================================================
    # ③ テキスト方式
    # =====================================================

    lines = [
        clean(x)
        for x in sp.get_text(
            "\n",
            strip=True
        ).splitlines()
        if clean(x)
    ]

    result3 = {}

    for i, line in enumerate(lines):

        # 例:
        # 1 ◎ 選手名 自力。
        for m in re.finditer(
            r"(?<!\d)([1-7])\s+([◎○▲△×注])?\s*([^\n]+)",
            line,
        ):

            car = int(m.group(1))
            rest = clean(m.group(3))

            if not rest:
                continue

            mark = m.group(2) or ""

            result3[car] = {
                "name": "",
                "mark": mark,
                "comment": rest,
                "prediction_score": MARK_SCORE.get(
                    mark,
                    0,
                ),
            }

    if 5 <= len(result3) <= 7:
        return result3

    return {}


# =========================================================
# 並び
# =========================================================

def valid_car_set(cars):
    unique = list(dict.fromkeys(cars))

    return (
        5 <= len(unique) <= 7
        and set(unique).issubset(set(range(1, 8)))
    )


def parse_line(sp):
    if not sp:
        return [], []

    candidates = []

    # =====================================================
    # ① テーブルのセル構造から取得
    # =====================================================

    for table in sp.find_all("table"):

        rows = table.find_all("tr")

        for i, tr in enumerate(rows):

            cells = [
                clean(c.get_text(" ", strip=True))
                for c in tr.find_all(["th", "td"])
            ]

            cars = [
                int(v)
                for v in cells
                if re.fullmatch(r"[1-7]", v)
            ]

            cars = list(dict.fromkeys(cars))

            if not valid_car_set(cars):
                continue

            nearby = " ".join(
                clean(x.get_text(" ", strip=True))
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
            reverse=True,
        )

        _, cars, cells = candidates[0]

        # セル境界を使ってグループ化
        groups = []
        current = []

        for v in cells:

            if re.fullmatch(r"[1-7]", v):
                current.append(int(v))

            else:
                if current:
                    groups.append(current)
                    current = []

        if current:
            groups.append(current)

        flat = [
            x
            for g in groups
            for x in g
        ]

        if set(flat) != set(cars):
            groups = [cars]

        return cars, groups

    # =====================================================
    # ② ページ本文から取得
    # =====================================================

    lines = [
        clean(x)
        for x in sp.get_text(
            "\n",
            strip=True
        ).splitlines()
        if clean(x)
    ]

    for i, line in enumerate(lines):

        nums = [
            int(x)
            for x in re.findall(
                r"(?<!\d)([1-7])(?!\d)",
                line,
            )
        ]

        nums = list(dict.fromkeys(nums))

        if not valid_car_set(nums):
            continue

        nearby = " ".join(
            lines[
                max(0, i - 2):
                min(len(lines), i + 3)
            ]
        )

        if any(w in nearby for w in STYLE_WORDS):

            return nums, [nums]

    return [], []


# =========================================================
# 展開コメント
# =========================================================

def is_development_text(text):
    t = clean(text)

    if not (12 <= len(t) <= 350):
        return False

    # 明らかな不要テキスト
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

    if any(x in t for x in bad_words):
        return False

    # 日本語
    if not re.search(r"[ぁ-んァ-ヶ一-龯]", t):
        return False

    # 文章らしさ
    if not re.search(
        r"[。！？，、]",
        t,
    ):
        return False

    return True


def parse_development(sp):
    if not sp:
        return ""

    # =====================================================
    # ① 「←」を基準に後ろを探索
    # =====================================================

    elements = sp.find_all(
        ["tr", "td", "p", "div", "li"]
    )

    for i, el in enumerate(elements):

        txt = clean(
            el.get_text(
                " ",
                strip=True
            )
        )

        if "←" not in txt:
            continue

        for nxt in elements[
            i + 1:
            i + 20
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

            if is_development_text(cand):
                return cand

    # =====================================================
    # ② 脚質行の直後
    # =====================================================

    for i, el in enumerate(elements):

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
            i + 1:
            i + 15
        ]:

            cand = clean(
                nxt.get_text(
                    " ",
                    strip=True
                )
            )

            if is_development_text(cand):
                return cand

    # =====================================================
    # ③ ページ本文
    # =====================================================

    lines = [
        clean(x)
        for x in sp.get_text(
            "\n",
            strip=True
        ).splitlines()
        if clean(x)
    ]

    # 「展開」付近
    for i, line in enumerate(lines):

        if "展開" in line:

            for cand in lines[
                i + 1:
                i + 8
            ]:

                if is_development_text(cand):
                    return cand

    # =====================================================
    # ④ 最後のフォールバック
    #
    # 予想コメントではなく、
    # レース全体について書かれている比較的長い文章を探す。
    # =====================================================

    candidates = []

    for line in lines:

        if not is_development_text(line):
            continue

        # 選手コメントらしい短文を少し除外
        if len(line) < 20:
            continue

        score = len(line)

        if any(x in line for x in (
            "本命",
            "対抗",
            "展開",
            "仕掛け",
            "先行",
            "捲",
            "マーク",
            "番手",
            "ライン",
        )):
            score += 50

        candidates.append(
            (score, line)
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
        race["race_no"],
    )

    html = get_html(url)

    if not html:
        return {
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "url": url,
        }

    sp = soup(html)

    if not sp:
        return {
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
            "url": url,
        }

    comments = parse_prediction_table(sp)

    line, line_groups = parse_line(sp)

    development = parse_development(sp)

    return {
        "comments": comments,
        "line": line,
        "line_groups": line_groups,
        "development": development,
        "url": url,
    }


# =========================================================
# AI
# =========================================================

def calculate_ai_score(rider, race):
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

    if rider.get("car_no") in line:

        p = line.index(
            rider["car_no"]
        )

        if p in (0, 2):
            score += 2

        elif p == 1:
            score += 4

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
        rider["ai_score"] = calculate_ai_score(
            rider,
            race
        )

    ranking = sorted(
        riders,
        key=lambda x: (
            x.get("ai_score", 0),
            x.get("score") or 0,
        ),
        reverse=True,
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
                "car_no": r["car_no"],
                "name": r["name"],
                "score": r["ai_score"],
                "mark": r.get(
                    "prediction_mark",
                    ""
                ),
            }
            for r in ranking
        ],
    }


# =========================================================
# MAIN
# =========================================================

def main():

    started = time.time()

    print("==============================")
    print(f" KEIRIN AI DATA UPDATE v{VERSION}")
    print("==============================")
    print(f"対象日: {TODAY_DISPLAY}")
    print("==============================")

    venues = [
        {
            "code": code,
            "name": name,
            "slug": slug,
        }
        for code, (name, slug)
        in VENUES.items()
    ]

    # -----------------------------------------------------
    # 開催場
    # -----------------------------------------------------

    print("開催場を確認中...")
    print(
        f"開催場数: {len(venues)}"
    )

    # -----------------------------------------------------
    # 実在レース
    # -----------------------------------------------------

    print("実在レースを実ページで確認中...")

    all_races = []

    for venue in venues:

        races = discover_real_races(
            venue
        )

        venue["race_numbers"] = [
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
            int(x["venue_code"]),
            x["race_no"],
        )
    )

    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    rider_success = sum(
        5 <= len(r["riders"]) <= 7
        for r in all_races
    )

    print(
        f"選手データ取得結果: "
        f"{rider_success}/"
        f"{len(all_races)}"
    )

    # -----------------------------------------------------
    # 予想
    # -----------------------------------------------------

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

        for f in as_completed(futures):

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
                "comments": p[
                    "comments"
                ],

                "line": p[
                    "line"
                ],

                "line_groups": p[
                    "line_groups"
                ],

                "development": p[
                    "development"
                ],

                "prediction_url": p[
                    "url"
                ],
            })

    # -----------------------------------------------------
    # 選手へ予想情報を反映
    # -----------------------------------------------------

    for race in all_races:

        comments = race.get(
            "comments",
            {}
        )

        for rider in race["riders"]:

            info = comments.get(
                rider["car_no"],
                {}
            )

            rider["prediction_mark"] = info.get(
                "mark",
                ""
            )

            rider["prediction_score"] = info.get(
                "prediction_score",
                0
            )

            rider["comment"] = info.get(
                "comment",
                ""
            )

        apply_ai(race)

    # -----------------------------------------------------
    # 集計
    # -----------------------------------------------------

    prediction_races = sum(
        len(r.get("comments", {}))
        == len(r["riders"])
        and len(r["riders"]) >= 5
        for r in all_races
    )

    comment_count = sum(
        len(r.get("comments", {}))
        for r in all_races
    )

    line_races = sum(
        len(r.get("line", []))
        == len(r["riders"])
        and len(r["riders"]) >= 5
        for r in all_races
    )

    development_races = sum(
        bool(r.get("development"))
        for r in all_races
    )

    rider_count = sum(
        len(r["riders"])
        for r in all_races
    )

    correct_names = sum(
        bool(rider.get("name"))
        for r in all_races
        for rider in r["riders"]
    )

    ai_race_count = sum(
        bool(r.get("ai"))
        for r in all_races
    )

    ai_rider_count = sum(
        r.get("ai_score") is not None
        for race in all_races
        for r in race["riders"]
    )

    # -----------------------------------------------------
    # 詳細診断
    # -----------------------------------------------------

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
            r.get("comments", {})
        ) != len(
            r["riders"]
        ):
            failed_predictions.append(
                label
            )

        if len(
            r.get("line", [])
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

    # -----------------------------------------------------
    # 完全性
    # -----------------------------------------------------

    data_complete = (
        len(all_races) == 72
        and rider_success == 72
        and rider_count == 500
        and correct_names == 500
        and prediction_races == 72
        and comment_count == 500
        and line_races == 72
        and development_races == 72
        and ai_race_count == 72
        and ai_rider_count == 500
        and not failed_riders
        and not failed_predictions
        and not failed_lines
    )

    # -----------------------------------------------------
    # JSON
    # -----------------------------------------------------

    output = {
        "version": VERSION,
        "updated_at": datetime.now().isoformat(),
        "target_date": TODAY_DISPLAY,

        "data_complete": data_complete,

        "venue_count": len(venues),
        "race_count": len(all_races),

        "rider_count": rider_count,
        "correct_name_count": correct_names,

        "prediction_race_count": prediction_races,
        "comment_count": comment_count,

        "line_race_count": line_races,
        "development_count": development_races,

        "ai_race_count": ai_race_count,
        "ai_rider_count": ai_rider_count,

        "failed_race_count": len(
            failed_riders
        ),

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

        "races": all_races,
    }

    # -----------------------------------------------------
    # 保存
    # -----------------------------------------------------

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
            indent=2,
        )

    os.replace(
        tmp,
        path
    )

    # -----------------------------------------------------
    # 結果
    # -----------------------------------------------------

    elapsed = (
        time.time()
        - started
    )

    print("==============================")
    print(
        f" v{VERSION} 取得結果"
    )
    print("==============================")

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

    print("【取得状況】")

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
        "データ完全性: "
        + (
            "OK"
            if data_complete
            else "要確認"
        )
    )

    print("==============================")
    print(" 詳細診断")
    print("==============================")

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

    print(
        f"保存先: {path}"
    )


if __name__ == "__main__":
    main()
