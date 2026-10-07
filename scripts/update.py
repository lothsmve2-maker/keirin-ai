# -*- coding: utf-8 -*-

"""
KEIRIN AI DATA UPDATE v4.4

・開催場自動取得
・全レース取得
・全選手データ取得
・レース別予想ページ取得
・選手コメント取得
・予想の並び取得
・展開コメント取得
・AI評価
・data/today.json 保存
"""

import json
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
}

MAX_WORKERS = 8
REQUEST_TIMEOUT = 25


# ============================================================
# 競輪場
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
# HTTP
# ============================================================

def get_html(url, timeout=REQUEST_TIMEOUT):
    last_error = None

    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=timeout,
            )

            r.raise_for_status()

            # OddsParkはページによってcharset指定が弱いので補正
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
                r.encoding = r.apparent_encoding or "utf-8"

            return r.text

        except Exception as e:
            last_error = e
            time.sleep(1 + attempt)

    print(f"    GET失敗: {url}")
    print(f"    {last_error}")
    return ""


# ============================================================
# 共通
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


def japanese_name(text):
    if not text:
        return False

    # 日本語名らしい文字を含むか
    if not re.search(r"[一-龯ぁ-んァ-ヶ]", text):
        return False

    # 明らかな不要文字を除外
    bad = [
        "選手名",
        "競走得点",
        "着順",
        "決まり手",
        "予想",
        "コメント",
    ]

    if any(x in text for x in bad):
        return False

    return True


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
        return {}

    soup = BeautifulSoup(html, "html.parser")

    venues = {}

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        m = re.search(
            r"(?:joCode|joCd)=(\d+)",
            href,
            re.I,
        )

        if not m:
            continue

        code = m.group(1)

        if code not in VENUE_CODE_TO_NAME:
            continue

        name = VENUE_CODE_TO_NAME[code]

        venues[code] = {
            "code": code,
            "name": name,
        }

    return venues


# ============================================================
# レース番号取得
# ============================================================

def get_race_numbers(venue_code):
    url = (
        f"{BASE_URL}/keirin/AllRaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
    )

    html = get_html(url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    races = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        if "RaceList.do" not in href:
            continue

        m = re.search(
            r"raceNo=(\d+)",
            href,
            re.I,
        )

        if m:
            race_no = int(m.group(1))

            if 1 <= race_no <= 12:
                races.add(race_no)

    # 念のためページ本文からも取得
    if not races:
        text = soup.get_text(" ", strip=True)

        for m in re.finditer(r"(\d{1,2})R", text):
            race_no = int(m.group(1))

            if 1 <= race_no <= 12:
                races.add(race_no)

    return sorted(races)


# ============================================================
# 選手データ取得
# ============================================================

def parse_finish(text):
    m = re.search(
        r"着\s*順[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        text,
    )

    if not m:
        return {
            "1": 0,
            "2": 0,
            "3": 0,
            "out": 0,
        }

    return {
        "1": int(m.group(1)),
        "2": int(m.group(2)),
        "3": int(m.group(3)),
        "out": int(m.group(4)),
    }


def parse_kimari(text):
    m = re.search(
        r"決まり手[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        text,
    )

    if not m:
        return {
            "nige": 0,
            "maki": 0,
            "sashi": 0,
            "mark": 0,
        }

    return {
        "nige": int(m.group(1)),
        "maki": int(m.group(2)),
        "sashi": int(m.group(3)),
        "mark": int(m.group(4)),
    }


def parse_rider_row(row):
    cells = row.find_all(["td", "th"])

    if not cells:
        return None

    cell_texts = [
        clean_text(c.get_text(" ", strip=True))
        for c in cells
    ]

    row_text = clean_text(
        row.get_text(" ", strip=True)
    )

    # --------------------------------------------------------
    # 車番
    # --------------------------------------------------------

    car_no = None

    for text in cell_texts[:4]:
        m = re.fullmatch(r"([1-9])", text)

        if m:
            car_no = int(m.group(1))
            break

    if car_no is None:
        m = re.search(r"^\s*([1-9])\s+", row_text)

        if m:
            car_no = int(m.group(1))

    if car_no is None:
        return None

    # --------------------------------------------------------
    # 選手名
    # --------------------------------------------------------

    name = ""

    # まずリンク
    for a in row.find_all("a"):
        t = clean_text(a.get_text(" ", strip=True))

        if japanese_name(t) and len(t) <= 20:
            name = t
            break

    # リンクで取れなければセルから探す
    if not name:
        for text in cell_texts:
            if japanese_name(text) and len(text) <= 20:
                name = text
                break

    if not name:
        return None

    # --------------------------------------------------------
    # 年齢・期
    # --------------------------------------------------------

    age = 0
    period = 0

    m = re.search(
        r"(\d{2})歳\s*[／/]\s*(\d{2,3})期",
        row_text,
    )

    if m:
        age = int(m.group(1))
        period = int(m.group(2))

    # --------------------------------------------------------
    # 級班
    # --------------------------------------------------------

    grade = ""

    m = re.search(
        r"\b([AS][1-3])\b",
        row_text,
        re.I,
    )

    if m:
        grade = m.group(1).upper()

    # --------------------------------------------------------
    # 脚質
    # --------------------------------------------------------

    style = ""

    for text in cell_texts:
        if text in ("逃", "捲", "差", "追", "両"):
            style = text
            break

    # --------------------------------------------------------
    # 競走得点
    # --------------------------------------------------------

    score = 0.0

    m = re.search(
        r"競走得点[:：]?\s*([\d.]+)",
        row_text,
    )

    if m:
        score = float(m.group(1))

    else:
        # セル単位のフォールバック
        for text in cell_texts:
            m2 = re.fullmatch(r"\d{2,3}(?:\.\d+)?", text)

            if m2:
                value = float(text)

                if 70 <= value <= 130:
                    score = value
                    break

    # --------------------------------------------------------
    # 成績
    # --------------------------------------------------------

    finish = parse_finish(row_text)

    # --------------------------------------------------------
    # 決まり手
    # --------------------------------------------------------

    kimari = parse_kimari(row_text)

    return {
        "car_no": car_no,
        "name": name,
        "age": age,
        "period": period,
        "prefecture": "",
        "grade": grade,
        "style": style,
        "score": score,
        "finish": finish,
        "kimari": kimari,
        "recent_results": [],
        "comment": "",
        "line": [],
        "role": "",
        "jump_probability": 0.0,
        "front_probability": 0.0,
        "attack_probability": 0.0,
        "comment_type": "不明",
    }


def extract_riders(html):
    soup = BeautifulSoup(html, "html.parser")

    riders = {}

    # --------------------------------------------------------
    # まずtr単位で探す
    # --------------------------------------------------------

    for row in soup.find_all("tr"):
        rider = parse_rider_row(row)

        if not rider:
            continue

        car_no = rider["car_no"]

        if car_no not in riders:
            riders[car_no] = rider

    # --------------------------------------------------------
    # table単位のフォールバック
    # --------------------------------------------------------

    if len(riders) < 5:

        for table in soup.find_all("table"):

            for row in table.find_all("tr"):

                rider = parse_rider_row(row)

                if not rider:
                    continue

                car_no = rider["car_no"]

                if car_no not in riders:
                    riders[car_no] = rider

    return [
        riders[n]
        for n in sorted(riders)
    ]


def get_race_data(venue_code, race_no):
    url = (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )

    html = get_html(url)

    if not html:
        return None

    riders = extract_riders(html)

    if len(riders) < 5:
        return None

    return {
        "venue_code": venue_code,
        "race_no": race_no,
        "riders": riders,
        "source_url": url,
    }


# ============================================================
# 予想ページURL
# ============================================================

def get_prediction_url(venue_name, race_no):
    slug = VENUE_NAME_TO_SLUG.get(venue_name)

    if not slug:
        return None

    mmdd = TODAY[4:]

    # OddsPark SPのレース別ページ
    return (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{TODAY[:4]}/"
        f"{mmdd}_{race_no}.html"
    )


# ============================================================
# 予想ページ解析
# ============================================================

def parse_prediction_page(html):
    if not html:
        return {
            "comments": {},
            "line": [],
            "development": "",
        }

    soup = BeautifulSoup(html, "html.parser")

    comments = {}
    line = []
    development = ""

    # --------------------------------------------------------
    # 選手コメント
    # --------------------------------------------------------

    for table in soup.find_all("table"):

        text = clean_text(
            table.get_text(" ", strip=True)
        )

        if "選手名" not in text:
            continue

        if "コメント" not in text:
            continue

        for row in table.find_all("tr"):

            cells = row.find_all(["td", "th"])

            if len(cells) < 3:
                continue

            values = [
                clean_text(
                    c.get_text(" ", strip=True)
                )
                for c in cells
            ]

            car = None

            for value in values:
                m = re.fullmatch(r"([1-9])", value)

                if m:
                    car = int(m.group(1))
                    break

            if car is None:
                continue

            comment = ""

            for value in reversed(values):
                if (
                    value
                    and value != str(car)
                    and "コメント" not in value
                    and "選手名" not in value
                ):
                    comment = value
                    break

            if comment:
                comments[car] = comment

    # --------------------------------------------------------
    # 並び
    # --------------------------------------------------------

    all_text = []

    for element in soup.find_all(
        ["p", "div", "td", "th", "li"]
    ):
        t = clean_text(
            element.get_text(" ", strip=True)
        )

        if t:
            all_text.append(t)

    style_words = (
        "逃捲",
        "追込",
        "自在",
        "逃げ",
        "捲り",
        "差し",
        "追込",
    )

    # 例:
    # 1 5 2 6 4 7 3
    # ← 逃捲 追込 ...
    for i, text in enumerate(all_text):

        if not any(x in text for x in style_words):
            continue

        nums = re.findall(r"(?<!\d)([1-7])(?!\d)", text)

        if len(nums) >= 5:
            candidate = [int(x) for x in nums]

            # 同じ車番が大量に並ぶ場合は除外
            if len(set(candidate)) >= 5:

                line = candidate

                # 7車を超えない
                line = line[:7]

                break

    # --------------------------------------------------------
    # 展開コメント
    # --------------------------------------------------------

    candidates = []

    for p in soup.find_all(["p", "div"]):

        t = clean_text(
            p.get_text(" ", strip=True)
        )

        if len(t) < 20:
            continue

        bad_words = [
            "選手名",
            "車番",
            "コメント",
            "オッズ",
            "出走表",
        ]

        if any(x in t for x in bad_words):
            continue

        if re.search(
            r"[。！!]",
            t,
        ):
            candidates.append(t)

    if candidates:
        # 最後の長めの文章を展開コメント候補
        candidates.sort(
            key=len,
            reverse=True,
        )

        development = candidates[0]

    return {
        "comments": comments,
        "line": line,
        "development": development,
    }


def get_prediction_data(venue_name, race_no):
    url = get_prediction_url(
        venue_name,
        race_no,
    )

    if not url:
        return None

    html = get_html(url)

    if not html:
        return None

    data = parse_prediction_page(html)

    data["url"] = url

    return data


# ============================================================
# AI計算
# ============================================================

def calc_probabilities(rider):
    score = float(rider.get("score") or 0)

    kimari = rider.get("kimari", {})

    nige = kimari.get("nige", 0)
    maki = kimari.get("maki", 0)
    sashi = kimari.get("sashi", 0)

    total = nige + maki + sashi

    if total <= 0:
        total = 1

    front = (
        nige / total * 100
    )

    attack = (
        (nige + maki) / total * 100
    )

    jump = 0.0

    if rider.get("style") in ("逃", "捲"):
        jump += 25

    if score >= 95:
        jump += 10
    elif score >= 90:
        jump += 5

    return {
        "jump_probability": round(
            min(jump, 95),
            1,
        ),
        "front_probability": round(
            min(front, 95),
            1,
        ),
        "attack_probability": round(
            min(attack, 95),
            1,
        ),
    }


def calc_ai_score(rider):
    score = float(
        rider.get("score") or 0
    )

    finish = rider.get(
        "finish",
        {},
    )

    first = finish.get("1", 0)
    second = finish.get("2", 0)
    third = finish.get("3", 0)
    out = finish.get("out", 0)

    total = first + second + third + out

    if total <= 0:
        total = 1

    finish_rate = (
        first * 1.0
        + second * 0.6
        + third * 0.35
    ) / total

    kimari = rider.get(
        "kimari",
        {},
    )

    attack_count = (
        kimari.get("nige", 0)
        + kimari.get("maki", 0)
        + kimari.get("sashi", 0)
    )

    value = (
        score
        + finish_rate * 10
        + min(attack_count, 20) * 0.15
    )

    return round(value, 2)


def apply_ai(race):
    riders = race["riders"]

    for rider in riders:

        probabilities = calc_probabilities(
            rider
        )

        rider.update(probabilities)

        rider["ai_score"] = calc_ai_score(
            rider
        )

    riders.sort(
        key=lambda x: x["ai_score"],
        reverse=True,
    )

    for rank, rider in enumerate(
        riders,
        start=1,
    ):

        rider["ai_rank"] = rank

        if rank == 1:
            rider["ai_label"] = "本命"
        elif rank == 2:
            rider["ai_label"] = "対抗"
        elif rank == 3:
            rider["ai_label"] = "単穴"
        elif rank <= 5:
            rider["ai_label"] = "連下"
        else:
            rider["ai_label"] = "穴"

    return race


# ============================================================
# メイン
# ============================================================

def main():

    started = time.time()

    print("==============================")
    print(" KEIRIN AI DATA UPDATE v4.4")
    print("==============================")
    print(f"対象日: {TODAY}")
    print("==============================")

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    venues = get_venues()

    if not venues:
        print("開催場を取得できませんでした")
        return

    print(f"開催場数: {len(venues)}")

    all_races = []

    for code, venue in venues.items():

        races = get_race_numbers(code)

        print(
            f"  {code} {venue['name']}: "
            f"{len(races)}レース"
        )

        for race_no in races:

            all_races.append({
                "venue_code": code,
                "venue_name": venue["name"],
                "race_no": race_no,
            })

    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    # --------------------------------------------------------
    # 選手データ
    # --------------------------------------------------------

    print("選手データ取得中...")

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for race in all_races:

            future = executor.submit(
                get_race_data,
                race["venue_code"],
                race["race_no"],
            )

            futures[future] = race

        for future in as_completed(futures):

            base = futures[future]

            try:
                data = future.result()

                if data:

                    data["venue_name"] = (
                        base["venue_name"]
                    )

                    results.append(data)

            except Exception as e:

                print(
                    f"  選手取得エラー "
                    f"{base['venue_name']} "
                    f"{base['race_no']}R: {e}"
                )

    results.sort(
        key=lambda x: (
            x["venue_code"],
            x["race_no"],
        )
    )

    print(
        f"選手データ取得結果: "
        f"{len(results)}/{len(all_races)}"
    )

    # --------------------------------------------------------
    # 予想情報
    # --------------------------------------------------------

    print(
        "コメント・並び・展開情報取得中..."
    )

    prediction_success = 0
    comment_count = 0
    line_count = 0
    development_count = 0

    def prediction_job(race):
        return (
            race,
            get_prediction_data(
                race["venue_name"],
                race["race_no"],
            ),
        )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                prediction_job,
                race,
            )
            for race in results
        ]

        for future in as_completed(futures):

            try:

                race, pred = future.result()

                if not pred:
                    continue

                prediction_success += 1

                # コメント
                comments = pred.get(
                    "comments",
                    {},
                )

                for rider in race["riders"]:

                    car = rider["car_no"]

                    if car in comments:

                        rider["comment"] = (
                            comments[car]
                        )

                        comment_count += 1

                # 並び
                line = pred.get(
                    "line",
                    [],
                )

                if line:

                    race["line"] = line

                    line_count += 1

                    # 車番から役割を推定
                    for index, car in enumerate(line):

                        for rider in race["riders"]:

                            if rider["car_no"] == car:

                                if index == 0:
                                    rider["role"] = "先頭"
                                elif index == 1:
                                    rider["role"] = "番手"
                                else:
                                    rider["role"] = "追走"

                else:
                    race["line"] = []

                # 展開
                development = pred.get(
                    "development",
                    "",
                )

                if development:

                    race["development_comment"] = (
                        development
                    )

                    development_count += 1

                race["prediction_url"] = (
                    pred.get("url", "")
                )

            except Exception as e:

                print(
                    f"  予想情報エラー: {e}"
                )

    # --------------------------------------------------------
    # AI
    # --------------------------------------------------------

    print("AI評価計算中...")

    for race in results:
        apply_ai(race)

    # --------------------------------------------------------
    # 出力
    # --------------------------------------------------------

    total_riders = sum(
        len(r["riders"])
        for r in results
    )

    correct_names = sum(
        1
        for r in results
        for rider in r["riders"]
        if rider.get("name")
    )

    output = {
        "updated_at": datetime.now().isoformat(),
        "target_date": TODAY,
        "version": "4.4",
        "venues": list(venues.values()),
        "races": results,
        "summary": {
            "venue_count": len(venues),
            "race_count": len(results),
            "rider_count": total_riders,
            "correct_name_count": correct_names,
            "prediction_race_count": prediction_success,
            "comment_count": comment_count,
            "line_race_count": line_count,
            "development_count": development_count,
        },
    }

    # --------------------------------------------------------
    # 保存
    # --------------------------------------------------------

    import os

    os.makedirs(
        "data",
        exist_ok=True,
    )

    with open(
        "data/today.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    elapsed = time.time() - started

    print("==============================")
    print(" 更新完了 v4.4")
    print("==============================")
    print(
        f"開催場: {len(venues)}"
    )
    print(
        f"レース: {len(results)}"
    )
    print(
        f"選手: {total_riders}"
    )
    print(
        f"正しい選手名: {correct_names}"
    )
    print("【取得状況】")
    print(
        f"選手データ成功: "
        f"{len(results)}/{len(all_races)}"
    )
    print(
        f"予想情報レース: "
        f"{prediction_success}/{len(results)}"
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
        f"{len(results)}"
    )
    print(
        f"AI評価選手: "
        f"{total_riders}"
    )
    print(
        f"処理時間: {elapsed:.1f}秒"
    )
    print(
        "保存先: data/today.json"
    )


if __name__ == "__main__":
    main()
