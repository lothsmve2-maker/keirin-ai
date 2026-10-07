import json
import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


# ============================================================
# KEIRIN AI DATA UPDATE v4.6
# ============================================================

BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")

REQUEST_TIMEOUT = 25
MAX_WORKERS = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
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


# ============================================================
# HTTP
# ============================================================

def get_html(url, retries=3):
    """
    HTML取得。
    一時的な503/接続エラーなどに備えてリトライ。
    """
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                response.encoding = response.apparent_encoding or "utf-8"
                return response.text

            print(
                f"  HTTP {response.status_code}: "
                f"{url} ({attempt + 1}/{retries})"
            )

        except Exception as e:
            print(
                f"  GET error: {type(e).__name__}: {url} "
                f"({attempt + 1}/{retries})"
            )

        if attempt < retries - 1:
            time.sleep(1.2 * (attempt + 1))

    return ""


# ============================================================
# 基本ユーティリティ
# ============================================================

def clean_text(text):
    if text is None:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def unique_keep_order(values):
    result = []
    seen = set()

    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)

    return result


def normalize_race_no(text):
    if not text:
        return None

    m = re.search(r"(\d{1,2})\s*R", text)
    if not m:
        m = re.search(r"(\d{1,2})レース", text)

    if not m:
        return None

    return int(m.group(1))


def parse_numbers_1_to_7(text):
    """
    1〜7の車番を抽出。
    """
    nums = re.findall(r"(?<!\d)([1-7])(?!\d)", text or "")
    return [int(x) for x in nums]


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

    soup = BeautifulSoup(html, "lxml")

    venues = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = clean_text(a.get_text(" ", strip=True))

        m = re.search(r"joCode=(\d+)", href)

        if not m:
            continue

        code = m.group(1)

        if code not in VENUE_CODE_TO_NAME:
            continue

        name = VENUE_CODE_TO_NAME[code]

        if not any(v["code"] == code for v in venues):
            venues.append({
                "code": code,
                "name": name,
                "text": text,
                "url": urljoin(BASE_URL, href),
            })

    # 今日開催場が見つからなかった場合の保険
    if not venues:
        for code, name in VENUE_CODE_TO_NAME.items():
            venues.append({
                "code": code,
                "name": name,
                "text": name,
                "url": "",
            })

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

    race_numbers = []

    if html:
        soup = BeautifulSoup(html, "lxml")

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = clean_text(a.get_text(" ", strip=True))

            if f"joCode={venue_code}" not in href:
                continue

            if TODAY not in href:
                continue

            race_no = normalize_race_no(text)

            if race_no is None:
                race_no = normalize_race_no(href)

            if race_no is not None and 1 <= race_no <= 12:
                race_numbers.append(race_no)

    race_numbers = sorted(unique_keep_order(race_numbers))

    return race_numbers


# ============================================================
# 選手成績
# ============================================================

def parse_finish(text):
    """
    直近4ヶ月成績などから
    着順1-2-3-外 を取得。
    """
    text = clean_text(text)

    nums = re.findall(r"\d+", text)

    if len(nums) >= 4:
        return {
            "finish_1": int(nums[0]),
            "finish_2": int(nums[1]),
            "finish_3": int(nums[2]),
            "finish_other": int(nums[3]),
        }

    return {
        "finish_1": 0,
        "finish_2": 0,
        "finish_3": 0,
        "finish_other": 0,
    }


def parse_kimari(text):
    """
    決まり手
    逃-捲-差-マ
    """
    text = clean_text(text)

    nums = re.findall(r"\d+", text)

    if len(nums) >= 4:
        return {
            "kimari_nige": int(nums[0]),
            "kimari_maku": int(nums[1]),
            "kimari_sashi": int(nums[2]),
            "kimari_mark": int(nums[3]),
        }

    return {
        "kimari_nige": 0,
        "kimari_maku": 0,
        "kimari_sashi": 0,
        "kimari_mark": 0,
    }


def parse_rider_row(cells):
    """
    オッズパーク出走表の1選手分を解析。
    """
    values = [clean_text(c.get_text(" ", strip=True)) for c in cells]

    if len(values) < 5:
        return None

    # 車番
    car_no = None

    for value in values[:3]:
        m = re.fullmatch(r"\d+", value)
        if m:
            n = int(value)
            if 1 <= n <= 7:
                car_no = n
                break

    if car_no is None:
        return None

    # 車番以外から名前候補を探す
    name = ""

    for value in values:
        if not value:
            continue

        if value == str(car_no):
            continue

        if re.search(r"[一-龥ぁ-んァ-ヶ]", value):
            # A1/A2/S1などは名前候補から除外
            if re.fullmatch(r"[SA][123]", value):
                continue

            if value in {
                "逃", "追", "両",
                "逃捲", "追込", "自在",
                "先行", "捲り", "差し",
            }:
                continue

            # 年齢/期別のようなものを除外
            if re.fullmatch(r"\d{1,3}/\d{1,3}", value):
                continue

            # 都道府県だけの可能性がある短い文字列を除外
            if value in {
                "北海道", "青森", "岩手", "宮城", "秋田",
                "山形", "福島", "茨城", "栃木", "群馬",
                "埼玉", "千葉", "東京", "神奈川",
                "新潟", "富山", "石川", "福井", "山梨",
                "長野", "岐阜", "静岡", "愛知", "三重",
                "滋賀", "京都", "大阪", "兵庫", "奈良",
                "和歌山", "鳥取", "島根", "岡山", "広島",
                "山口", "徳島", "香川", "愛媛", "高知",
                "福岡", "佐賀", "長崎", "熊本", "大分",
                "宮崎", "鹿児島", "沖縄",
            }:
                continue

            name = value
            break

    if not name:
        return None

    # 年齢/期別
    age = None
    period = None

    for value in values:
        m = re.match(r"(\d{1,3})\s*/\s*(\d{1,3})", value)

        if m:
            age = int(m.group(1))
            period = int(m.group(2))
            break

    # 都道府県
    prefecture = ""

    prefectures = [
        "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
        "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
        "新潟", "富山", "石川", "福井", "山梨", "長野",
        "岐阜", "静岡", "愛知", "三重",
        "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山",
        "鳥取", "島根", "岡山", "広島", "山口",
        "徳島", "香川", "愛媛", "高知",
        "福岡", "佐賀", "長崎", "熊本", "大分",
        "宮崎", "鹿児島", "沖縄",
    ]

    for value in values:
        if value in prefectures:
            prefecture = value
            break

    # 級班
    grade = ""

    for value in values:
        if re.fullmatch(r"[SA][123]", value):
            grade = value
            break

    # 脚質
    style = ""

    for value in values:
        if value in {"逃", "追", "両"}:
            style = value
            break

    # 競走得点
    score = None

    for value in values:
        m = re.fullmatch(r"\d{2,3}(?:\.\d+)?", value)

        if m:
            num = float(value)

            if 60 <= num <= 130:
                score = num
                break

    # 成績
    finish = {
        "finish_1": 0,
        "finish_2": 0,
        "finish_3": 0,
        "finish_other": 0,
    }

    kimari = {
        "kimari_nige": 0,
        "kimari_maku": 0,
        "kimari_sashi": 0,
        "kimari_mark": 0,
    }

    # 「4つの数字が連続するセル」を探す
    for value in values:
        nums = re.findall(r"\d+", value)

        if len(nums) >= 4:
            n = [int(x) for x in nums[:4]]

            if all(0 <= x <= 99 for x in n):
                if "kimari" not in value.lower():
                    finish = {
                        "finish_1": n[0],
                        "finish_2": n[1],
                        "finish_3": n[2],
                        "finish_other": n[3],
                    }

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
    }


def extract_riders(soup):
    riders = []

    # テーブル単位で探す
    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        for row in rows:
            cells = row.find_all(["th", "td"])

            rider = parse_rider_row(cells)

            if rider:
                riders.append(rider)

    # 重複排除
    result = {}
    for rider in riders:
        result[rider["car_no"]] = rider

    return [result[k] for k in sorted(result)]


# ============================================================
# レースデータ
# ============================================================

def get_race_url(venue_code, race_no):
    return (
        f"{BASE_URL}/keirin/RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )


def get_race_data(venue, race_no):
    url = get_race_url(venue["code"], race_no)

    html = get_html(url)

    if not html:
        return {
            "venue_code": venue["code"],
            "venue_name": venue["name"],
            "race_no": race_no,
            "riders": [],
            "success": False,
        }

    soup = BeautifulSoup(html, "lxml")

    riders = extract_riders(soup)

    return {
        "venue_code": venue["code"],
        "venue_name": venue["name"],
        "race_no": race_no,
        "riders": riders,
        "success": len(riders) > 0,
    }


# ============================================================
# 予想ページURL
# ============================================================

def get_prediction_index_url(venue_name):
    slug = VENUE_NAME_TO_SLUG.get(venue_name)

    if not slug:
        return ""

    year = TODAY[:4]
    mmdd = TODAY[4:]

    return (
        f"{SP_BASE_URL}/keirin/yosou/"
        f"{slug}/{year}/{mmdd}.html"
    )


def discover_prediction_urls(venue_name, expected_races):
    """
    v4.6重要修正

    OddsPark SP予想ページは、

      1R → 現在開いているページ本体
      2R〜 → ページ内リンク

    という構造。

    v4.5では1Rをリンクとして探していたため、
    各競輪場で1Rだけ欠落していた。

    ここでは最初から1R=index_urlとして登録する。
    """

    index_url = get_prediction_index_url(venue_name)

    if not index_url:
        return {}

    html = get_html(index_url)

    if not html:
        return {}

    soup = BeautifulSoup(html, "lxml")

    found = {}

    # --------------------------------------------------------
    # ★ v4.6
    # 現在ページ自身が1R
    # --------------------------------------------------------
    page_text = clean_text(soup.get_text(" ", strip=True))

    if re.search(r"\b1R\b", page_text) or "1R" in page_text:
        if 1 in expected_races:
            found[1] = index_url

    # --------------------------------------------------------
    # 2R以降はリンクから取得
    # --------------------------------------------------------
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = clean_text(a.get_text(" ", strip=True))

        race_no = normalize_race_no(text)

        if race_no is None:
            race_no = normalize_race_no(href)

        if race_no is None:
            continue

        if race_no not in expected_races:
            continue

        full_url = urljoin(index_url, href)

        # 同じページなら1R
        if full_url == index_url and race_no != 1:
            continue

        found[race_no] = full_url

    return dict(sorted(found.items()))


# ============================================================
# 予想ページ解析
# ============================================================

def extract_comments(soup):
    """
    選手コメントを取得。
    """

    comments = {}

    # --------------------------------------------------------
    # まずテーブルを探す
    # --------------------------------------------------------
    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        for row in rows:
            cells = row.find_all(["th", "td"])

            if len(cells) < 2:
                continue

            values = [
                clean_text(c.get_text(" ", strip=True))
                for c in cells
            ]

            car_no = None

            for value in values[:2]:
                if re.fullmatch(r"[1-7]", value):
                    car_no = int(value)
                    break

            if car_no is None:
                continue

            # コメント候補
            for value in values:
                if not value or value == str(car_no):
                    continue

                if value in {
                    "▲", "△", "○", "◎", "×", "…",
                }:
                    continue

                if re.search(r"[ぁ-んァ-ヶ一-龥]", value):
                    # 選手名らしい長い文字列ではなく
                    # コメントを優先
                    if value.endswith(("。", "。 ")):
                        comments[car_no] = value
                        break

    # --------------------------------------------------------
    # 予備処理
    # --------------------------------------------------------
    if len(comments) < 3:
        texts = [
            clean_text(x.get_text(" ", strip=True))
            for x in soup.find_all(["td", "p", "div"])
        ]

        for text in texts:
            m = re.match(
                r"([1-7])\s+(.{1,80})$",
                text
            )

            if not m:
                continue

            car_no = int(m.group(1))
            comment = clean_text(m.group(2))

            if comment and "。" in comment:
                comments.setdefault(car_no, comment)

    return comments


def extract_race_comment(soup):
    """
    レース全体の予想コメント。
    """

    # まず段落・divから候補を探す
    candidates = []

    for tag in soup.find_all(["p", "div", "td"]):
        text = clean_text(tag.get_text(" ", strip=True))

        if not text:
            continue

        if len(text) < 20:
            continue

        if len(text) > 500:
            continue

        # 選手コメントのような短文を除外
        if re.fullmatch(r"[1-7]\s+.*", text):
            continue

        # 予想コメントにありがちな語
        keywords = (
            "期待",
            "本命",
            "対抗",
            "狙い",
            "展開",
            "先行",
            "捲り",
            "差し",
            "上位",
            "中心",
            "一撃",
            "自力",
        )

        if any(k in text for k in keywords):
            candidates.append(text)

    # 長すぎる重複を除外
    candidates = unique_keep_order(candidates)

    if candidates:
        # 最初の適切なもの
        return candidates[0]

    return ""


# ============================================================
# 並び解析
# ============================================================

STYLE_WORDS = (
    "逃捲",
    "追込",
    "自在",
    "逃げ",
    "捲り",
    "差し",
    "先行",
)


def looks_like_line_numbers(nums):
    """
    予想の並びとして妥当か。
    """
    if len(nums) < 5:
        return False

    if len(nums) > 7:
        nums = nums[:7]

    if len(set(nums)) < 5:
        return False

    return all(1 <= x <= 7 for x in nums)


def extract_line_from_tables(soup):
    """
    予想ページの「予想の並び」をテーブル構造から取得。

    実ページでは、

      1 5 | 2 6 | 4 | 7 3
      ← 逃捲 追込 | 自在 追込 | 追込 | 逃捲 追込

    のように車番行と脚質行が近接している。
    """

    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")

        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"])

            values = [
                clean_text(c.get_text(" ", strip=True))
                for c in cells
            ]

            # 車番候補
            nums = []

            for value in values:
                if re.fullmatch(r"[1-7]", value):
                    nums.append(int(value))

            if not looks_like_line_numbers(nums):
                continue

            # 7車全て揃っていれば最優先
            candidate = nums[:7]

            if sorted(candidate) != list(range(1, 8)):
                continue

            # 次行・前行に脚質があるか確認
            nearby = []

            if i > 0:
                nearby.extend(
                    clean_text(rows[i - 1].get_text(" ", strip=True)).split()
                )

            if i + 1 < len(rows):
                nearby.extend(
                    clean_text(rows[i + 1].get_text(" ", strip=True)).split()
                )

            nearby_text = " ".join(nearby)

            if any(word in nearby_text for word in STYLE_WORDS):
                return candidate

    return []


def extract_line_from_text(soup):
    """
    テーブル解析で取れなかった場合の予備処理。
    """

    # まずブロック単位
    blocks = soup.find_all(
        ["td", "div", "p", "span", "li"]
    )

    for block in blocks:
        text = clean_text(block.get_text(" ", strip=True))

        if not any(word in text for word in STYLE_WORDS):
            continue

        nums = parse_numbers_1_to_7(text)

        if not looks_like_line_numbers(nums):
            continue

        candidate = nums[:7]

        if sorted(candidate) == list(range(1, 8)):
            return candidate

    # ページ全体を数行単位で見る
    text = soup.get_text("\n", strip=True)

    lines = [
        clean_text(x)
        for x in text.splitlines()
        if clean_text(x)
    ]

    for i, line in enumerate(lines):
        nums = parse_numbers_1_to_7(line)

        if not looks_like_line_numbers(nums):
            continue

        candidate = nums[:7]

        if sorted(candidate) != list(range(1, 8)):
            continue

        nearby = " ".join(
            lines[max(0, i - 2):min(len(lines), i + 3)]
        )

        if (
            "←" in nearby
            or any(word in nearby for word in STYLE_WORDS)
        ):
            return candidate

    return []


def extract_line_groups(soup):
    """
    可能なら並びのグループも取得。

    例:
      1-5 / 2-6 / 4 / 7-3

    取得できない場合は空配列。
    """

    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"])

            if not cells:
                continue

            groups = []
            current = []

            for cell in cells:
                text = clean_text(cell.get_text(" ", strip=True))

                if text == "":
                    if current:
                        groups.append(current)
                        current = []
                    continue

                if re.fullmatch(r"[1-7]", text):
                    current.append(int(text))
                else:
                    # 数字以外が混ざったら一旦区切る
                    if current:
                        groups.append(current)
                        current = []

            if current:
                groups.append(current)

            # 1〜7が全部揃うなら候補
            flat = [
                n
                for group in groups
                for n in group
            ]

            if (
                len(flat) == 7
                and sorted(flat) == list(range(1, 8))
            ):
                # 連続した1要素グループはそのまま
                return groups

    return []


def parse_prediction_page(soup):
    comments = extract_comments(soup)

    line = extract_line_from_tables(soup)

    if not line:
        line = extract_line_from_text(soup)

    groups = extract_line_groups(soup)

    race_comment = extract_race_comment(soup)

    return {
        "comments": comments,
        "line": line,
        "line_groups": groups,
        "development": race_comment,
    }


def get_prediction_data(
    venue_name,
    race_no,
    url,
):
    html = get_html(url)

    if not html:
        return {
            "venue_name": venue_name,
            "race_no": race_no,
            "success": False,
            "comments": {},
            "line": [],
            "line_groups": [],
            "development": "",
        }

    soup = BeautifulSoup(html, "lxml")

    data = parse_prediction_page(soup)

    success = bool(
        data["comments"]
        or data["line"]
        or data["development"]
    )

    return {
        "venue_name": venue_name,
        "race_no": race_no,
        "url": url,
        "success": success,
        **data,
    }


# ============================================================
# AI評価
# ============================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def calculate_rider_ai(rider, line_position=None):
    """
    無料版AI評価。

    競走得点
    直近成績
    決まり手
    並び位置

    を組み合わせて0〜100点にする。
    """

    score = safe_float(rider.get("score"))

    # 競走得点を0〜50点程度へ
    score_part = max(
        0.0,
        min(
            50.0,
            (score - 70.0) * 1.25
        )
    )

    f1 = safe_float(rider.get("finish_1"))
    f2 = safe_float(rider.get("finish_2"))
    f3 = safe_float(rider.get("finish_3"))
    fo = safe_float(rider.get("finish_other"))

    total = f1 + f2 + f3 + fo

    if total > 0:
        recent_part = (
            f1 * 18.0
            + f2 * 11.0
            + f3 * 7.0
        ) / max(total, 1.0)
    else:
        recent_part = 0.0

    nige = safe_float(rider.get("kimari_nige"))
    maku = safe_float(rider.get("kimari_maku"))
    sashi = safe_float(rider.get("kimari_sashi"))
    mark = safe_float(rider.get("kimari_mark"))

    kimari_total = nige + maku + sashi + mark

    if kimari_total > 0:
        tactical_part = (
            nige * 0.7
            + maku * 1.0
            + sashi * 1.1
            + mark * 0.45
        ) / kimari_total * 10.0
    else:
        tactical_part = 0.0

    position_bonus = 0.0

    if line_position is not None:
        if line_position == 0:
            position_bonus = 5.0
        elif line_position == 1:
            position_bonus = 3.0
        else:
            position_bonus = 1.0

    total_score = (
        score_part
        + recent_part
        + tactical_part
        + position_bonus
    )

    return round(
        max(0.0, min(100.0, total_score)),
        1
    )


def apply_ai(race):
    riders = race.get("riders", [])

    if not riders:
        race["ai"] = {
            "top": [],
            "confidence": 0,
        }
        return race

    line = race.get("line", [])

    position_map = {}

    for index, car_no in enumerate(line):
        position_map[car_no] = index

    for rider in riders:
        rider["ai_score"] = calculate_rider_ai(
            rider,
            position_map.get(rider["car_no"])
        )

    riders_sorted = sorted(
        riders,
        key=lambda x: x.get("ai_score", 0),
        reverse=True
    )

    top = [
        {
            "car_no": r["car_no"],
            "name": r["name"],
            "score": r["ai_score"],
        }
        for r in riders_sorted[:3]
    ]

    confidence = 0

    if top:
        confidence = min(
            99,
            max(
                45,
                int(top[0]["score"] * 1.15)
            )
        )

    race["ai"] = {
        "top": top,
        "confidence": confidence,
    }

    return race


# ============================================================
# メイン
# ============================================================

def main():
    started = time.time()

    print("==============================")
    print(" KEIRIN AI DATA UPDATE v4.6")
    print("==============================")
    print(f"対象日: {TODAY_DISPLAY}")
    print("==============================")

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------
    venues = get_venues()

    # 今日の開催場が取れない場合
    if not venues:
        print("開催場を取得できませんでした。")
        return

    # 実際に取得する競輪場だけに絞る
    venues = [
        v for v in venues
        if v["code"] in VENUE_CODE_TO_NAME
    ]

    print(f"開催場数: {len(venues)}")

    all_races = []

    # --------------------------------------------------------
    # レース番号
    # --------------------------------------------------------
    venue_races = {}

    for venue in venues:
        race_numbers = get_race_numbers(
            venue["code"]
        )

        if not race_numbers:
            # 最低限1〜12Rを推測
            # ただし通常はここには来ない
            race_numbers = list(range(1, 13))

        venue_races[venue["name"]] = race_numbers

        print(
            f"  {venue['code']} {venue['name']}: "
            f"{len(race_numbers)}レース"
        )

        for race_no in race_numbers:
            all_races.append({
                "venue": venue,
                "race_no": race_no,
            })

    print(
        f"詳細取得対象レース: {len(all_races)}"
    )

    # --------------------------------------------------------
    # 出走表
    # --------------------------------------------------------
    print("選手データ取得中...")

    race_results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                get_race_data,
                item["venue"],
                item["race_no"],
            ): item
            for item in all_races
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                race_results.append(result)
            except Exception as e:
                item = futures[future]

                race_results.append({
                    "venue_code": item["venue"]["code"],
                    "venue_name": item["venue"]["name"],
                    "race_no": item["race_no"],
                    "riders": [],
                    "success": False,
                    "error": str(e),
                })

    race_results.sort(
        key=lambda x: (
            x["venue_name"],
            x["race_no"],
        )
    )

    rider_success_count = sum(
        1 for x in race_results
        if x.get("success")
    )

    print(
        f"選手データ取得結果: "
        f"{rider_success_count}/{len(race_results)}"
    )

    # --------------------------------------------------------
    # 予想URL
    # --------------------------------------------------------
    print("予想ページURLを確認中...")

    prediction_urls = {}

    for venue in venues:
        venue_name = venue["name"]

        expected = venue_races.get(
            venue_name,
            []
        )

        found = discover_prediction_urls(
            venue_name,
            expected,
        )

        prediction_urls[venue_name] = found

        print(
            f"  {venue_name}: "
            f"{len(found)}レース予想URL"
        )

    # --------------------------------------------------------
    # 予想情報
    # --------------------------------------------------------
    print(
        "コメント・並び・展開情報取得中..."
    )

    prediction_results = []

    prediction_tasks = []

    for venue_name, race_map in prediction_urls.items():
        for race_no, url in race_map.items():
            prediction_tasks.append({
                "venue_name": venue_name,
                "race_no": race_no,
                "url": url,
            })

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                get_prediction_data,
                task["venue_name"],
                task["race_no"],
                task["url"],
            ): task
            for task in prediction_tasks
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                prediction_results.append(result)
            except Exception as e:
                task = futures[future]

                prediction_results.append({
                    "venue_name": task["venue_name"],
                    "race_no": task["race_no"],
                    "url": task["url"],
                    "success": False,
                    "comments": {},
                    "line": [],
                    "line_groups": [],
                    "development": "",
                    "error": str(e),
                })

    # 参照しやすいよう辞書化
    prediction_map = {}

    for result in prediction_results:
        key = (
            result["venue_name"],
            result["race_no"],
        )

        prediction_map[key] = result

    # --------------------------------------------------------
    # 結合
    # --------------------------------------------------------
    final_races = []

    for race in race_results:
        key = (
            race["venue_name"],
            race["race_no"],
        )

        prediction = prediction_map.get(
            key,
            {
                "success": False,
                "comments": {},
                "line": [],
                "line_groups": [],
                "development": "",
                "url": "",
            },
        )

        comments = prediction.get(
            "comments",
            {}
        )

        line = prediction.get(
            "line",
            []
        )

        line_groups = prediction.get(
            "line_groups",
            []
        )

        development = prediction.get(
            "development",
            ""
        )

        # 選手にコメントを紐付け
        for rider in race["riders"]:
            car = rider["car_no"]

            rider["comment"] = comments.get(
                car,
                ""
            )

        race["prediction_url"] = prediction.get(
            "url",
            ""
        )

        race["comments"] = comments
        race["line"] = line
        race["line_groups"] = line_groups
        race["development"] = development

        # AI
        apply_ai(race)

        final_races.append(race)

    # --------------------------------------------------------
    # 集計
    # --------------------------------------------------------
    rider_count = sum(
        len(r.get("riders", []))
        for r in final_races
    )

    correct_name_count = sum(
        1
        for r in final_races
        for rider in r.get("riders", [])
        if rider.get("name")
    )

    prediction_race_count = sum(
        1
        for r in final_races
        if (
            r.get("comments")
            or r.get("line")
            or r.get("development")
        )
    )

    comment_count = sum(
        len(r.get("comments", {}))
        for r in final_races
    )

    line_race_count = sum(
        1
        for r in final_races
        if r.get("line")
    )

    development_count = sum(
        1
        for r in final_races
        if r.get("development")
    )

    ai_race_count = sum(
        1
        for r in final_races
        if r.get("ai", {}).get("top")
    )

    ai_rider_count = sum(
        1
        for r in final_races
        for rider in r.get("riders", [])
        if "ai_score" in rider
    )

    failed_races = [
        r
        for r in final_races
        if not r.get("success")
    ]

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------
    output = {
        "updated_at": datetime.now().isoformat(),
        "target_date": TODAY_DISPLAY,

        "version": "4.6",

        "venue_count": len(venues),
        "race_count": len(final_races),

        "rider_count": rider_count,
        "correct_name_count": correct_name_count,

        "prediction_race_count": prediction_race_count,
        "comment_count": comment_count,
        "line_race_count": line_race_count,
        "development_count": development_count,

        "ai_race_count": ai_race_count,
        "ai_rider_count": ai_rider_count,

        "venues": [
            {
                "code": v["code"],
                "name": v["name"],
                "race_count": len(
                    venue_races.get(
                        v["name"],
                        []
                    )
                ),
            }
            for v in venues
        ],

        "races": final_races,
    }

    # --------------------------------------------------------
    # 保存
    # --------------------------------------------------------
    os.makedirs("data", exist_ok=True)

    output_path = "data/today.json"

    with open(
        output_path,
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

    # --------------------------------------------------------
    # 結果表示
    # --------------------------------------------------------
    print("==============================")
    print(" 更新完了 v4.6")
    print("==============================")
    print(f"開催場: {len(venues)}")
    print(f"レース: {len(final_races)}")
    print(f"選手: {rider_count}")
    print(f"正しい選手名: {correct_name_count}")

    print("【取得状況】")
    print(
        f"選手データ成功: "
        f"{rider_success_count}/{len(final_races)}"
    )

    print(
        f"予想情報レース: "
        f"{prediction_race_count}/{len(final_races)}"
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
        f"{len(failed_races)}"
    )

    print(
        f"処理時間: "
        f"{elapsed:.1f}秒"
    )

    print(
        f"保存先: "
        f"{output_path}"
    )


if __name__ == "__main__":
    main()
