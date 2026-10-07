# -*- coding: utf-8 -*-

"""
KEIRIN AI DATA UPDATE v4.3

方針
----
1. RaceListInfo.do
   -> 当日の開催場を取得

2. AllRaceList.do
   -> 各開催場のレース数を取得

3. RaceList.do
   -> 各レースの選手データを取得
   -> ここを最重要データとして独立処理

4. OddsPark 予想情報
   -> 開催場ごとに1ページ取得
   -> 選手コメント
   -> 印
   -> 予想ライン
   -> 展開脚質
   -> 展開コメント

5. AI評価
   -> 選手データ + ライン + コメント情報から計算

6. 最後に data/today.json へ一括保存

重要
----
予想情報の取得に失敗しても、
RaceList.do の選手データを消さない。
"""

import os
import re
import json
import time
import math
import html
import unicodedata
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup


# =========================================================
# 基本設定
# =========================================================

TODAY = datetime.now().strftime("%Y%m%d")

BASE_ODDSPARK = "https://www.oddspark.com/keirin"
BASE_SP = "https://sp.oddspark.com/keirin"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}

TIMEOUT = 20
MAX_WORKERS = 8

OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "today.json")


# =========================================================
# 開催場コード
# =========================================================

VENUE_NAMES = {
    "01": "函館",
    "02": "青森",
    "03": "いわき平",
    "04": "弥彦",
    "05": "前橋",
    "06": "取手",
    "07": "宇都宮",
    "08": "大宮",
    "09": "西武園",
    "10": "京王閣",
    "11": "立川",
    "12": "松戸",
    "13": "千葉",
    "14": "花月園",
    "15": "川崎",
    "16": "平塚",
    "17": "小田原",
    "18": "伊東",
    "19": "静岡",
    "20": "名古屋",
    "21": "岐阜",
    "22": "大垣",
    "23": "豊橋",
    "24": "富山",
    "25": "松阪",
    "26": "四日市",
    "27": "福井",
    "28": "奈良",
    "29": "向日町",
    "30": "和歌山",
    "31": "岸和田",
    "32": "玉野",
    "33": "広島",
    "34": "防府",
    "35": "高松",
    "36": "小松島",
    "37": "高知",
    "38": "松山",
    "39": "小倉",
    "40": "久留米",
    "41": "武雄",
    "42": "佐世保",
    "43": "別府",
    "44": "熊本",
}


# 実際の OddsPark コードと現在の表示用コードを補正
# 現在のサイトで確認できるコードを優先
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


# =========================================================
# HTTP Session
# =========================================================

session = requests.Session()
session.headers.update(HEADERS)


def get_text(url, timeout=TIMEOUT):
    """
    ページ取得。
    文字コードは response.apparent_encoding を優先。
    """
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()

        if not r.encoding:
            r.encoding = r.apparent_encoding or "utf-8"

        return r.text

    except Exception as e:
        print(f"[GET ERROR] {url}")
        print(f"  {type(e).__name__}: {e}")
        return None


# =========================================================
# 共通文字処理
# =========================================================

def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = unicodedata.normalize("NFKC", value)

    value = value.replace("\xa0", " ")
    value = value.replace("\u3000", " ")

    value = re.sub(r"\s+", " ", value)

    return value.strip()


def clean_name(value):
    value = clean_text(value)

    # 府県等が括弧で付くケースを除去
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"（[^）]*）", "", value)

    return clean_text(value)


def to_int(value, default=0):
    try:
        s = clean_text(value)
        s = re.sub(r"[^\d\-]", "", s)
        if not s:
            return default
        return int(s)
    except Exception:
        return default


def to_float(value, default=0.0):
    try:
        s = clean_text(value)
        s = s.replace(",", "")
        s = re.sub(r"[^\d.\-]", "", s)

        if not s:
            return default

        return float(s)

    except Exception:
        return default


# =========================================================
# 開催場取得
# =========================================================

def get_venues():
    """
    RaceListInfo.do から当日開催場を取得。
    """

    url = (
        f"{BASE_ODDSPARK}/RaceListInfo.do"
        f"?kaisaiBi={TODAY}"
    )

    text = get_text(url)

    if not text:
        return []

    soup = BeautifulSoup(text, "html.parser")

    venues = []

    # -----------------------------------------------------
    # URLから joCode / joCd を探す
    # -----------------------------------------------------

    for a in soup.find_all("a"):
        href = a.get("href", "")
        label = clean_text(a.get_text(" ", strip=True))

        m = re.search(
            r"(?:joCode|joCd)=(\d+)",
            href,
            flags=re.I
        )

        if not m:
            continue

        code = m.group(1)

        # 1桁コード対策
        code = code.zfill(2)

        name = label

        if code in VENUE_CODE_TO_NAME:
            name = VENUE_CODE_TO_NAME[code]

        if not name:
            name = VENUE_NAMES.get(code, "")

        if not name:
            continue

        if code not in [v["venue_code"] for v in venues]:
            venues.append({
                "venue_code": code,
                "venue_name": name,
            })

    # -----------------------------------------------------
    # HTMLにリンク構造が違う場合の補助検索
    # -----------------------------------------------------

    if not venues:

        page_text = clean_text(soup.get_text(" "))

        for code, name in VENUE_CODE_TO_NAME.items():
            if name in page_text:
                venues.append({
                    "venue_code": code,
                    "venue_name": name,
                })

    return venues


# =========================================================
# レース番号取得
# =========================================================

def get_race_numbers(venue_code):
    """
    AllRaceList.do から開催場のレース番号を取得。
    """

    url = (
        f"{BASE_ODDSPARK}/AllRaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
    )

    text = get_text(url)

    if not text:
        return []

    soup = BeautifulSoup(text, "html.parser")

    race_numbers = set()

    # -----------------------------------------------------
    # hrefから raceNo を取得
    # -----------------------------------------------------

    for a in soup.find_all("a"):
        href = a.get("href", "")

        m = re.search(
            r"raceNo=(\d+)",
            href,
            flags=re.I
        )

        if m:
            race_no = int(m.group(1))

            if 1 <= race_no <= 12:
                race_numbers.add(race_no)

    # -----------------------------------------------------
    # 取得できない場合はページテキストから補助
    # -----------------------------------------------------

    if not race_numbers:

        for m in re.finditer(
            r"(?<!\d)(1[0-2]|[1-9])R",
            soup.get_text(" ", strip=True)
        ):
            race_numbers.add(int(m.group(1)))

    return sorted(race_numbers)


# =========================================================
# RaceList.do のテーブル探索
# =========================================================

def find_race_table(soup):
    """
    RaceList.do内から選手テーブルを探す。

    v4.2のようにHTML全体を雑に正規表現解析せず、
    「選手名」「競走得点」「着順」「逃」「捲」「差」「マ」
    を持つテーブルを優先する。
    """

    candidates = []

    for table in soup.find_all("table"):

        text = clean_text(table.get_text(" "))

        score_ok = "競走得点" in text
        name_ok = "選手名" in text
        finish_ok = (
            "着順" in text
            or "1-2-3-外" in text
        )

        if score_ok and name_ok and finish_ok:
            candidates.append(table)

    if candidates:
        return candidates[0]

    return None


# =========================================================
# テーブル行から選手を抽出
# =========================================================

def extract_riders_from_table(table):
    """
    選手テーブルを可能な限り柔軟に解析。

    HTMLの列順が多少変わっても、
    ヘッダー名から列位置を決定する。
    """

    if table is None:
        return []

    rows = table.find_all("tr")

    if not rows:
        return []

    # -----------------------------------------------------
    # ヘッダー探索
    # -----------------------------------------------------

    header_row = None
    headers = []

    for tr in rows:

        cells = tr.find_all(["th", "td"])

        vals = [
            clean_text(c.get_text(" ", strip=True))
            for c in cells
        ]

        joined = " ".join(vals)

        if (
            "選手名" in joined
            and (
                "競走得点" in joined
                or "得点" in joined
            )
        ):
            header_row = tr
            headers = vals
            break

    # ヘッダーが見つからない場合
    if not headers:
        # 旧構造対策
        for tr in rows[:5]:

            vals = [
                clean_text(c.get_text(" ", strip=True))
                for c in tr.find_all(["th", "td"])
            ]

            if len(vals) >= 8:
                headers = vals
                header_row = tr
                break

    if not headers:
        return []

    # -----------------------------------------------------
    # 列インデックス
    # -----------------------------------------------------

    def find_col(*names):
        for i, h in enumerate(headers):
            for name in names:
                if name in h:
                    return i
        return None

    idx_car = find_col("車番")
    idx_name = find_col("選手名")
    idx_age = find_col("年齢")
    idx_period = find_col("期別")
    idx_pref = find_col("府県")
    idx_class = find_col("級班")
    idx_style = find_col("脚質")
    idx_score = find_col("競走得点")
    idx_finish = find_col("着順")

    idx_nige = find_col("逃")
    idx_maki = find_col("捲")
    idx_sashi = find_col("差")
    idx_mark = find_col("マ")

    # -----------------------------------------------------
    # 行処理
    # -----------------------------------------------------

    riders = []

    header_index = rows.index(header_row)

    for tr in rows[header_index + 1:]:

        cells = tr.find_all(["td", "th"])

        vals = [
            clean_text(c.get_text(" ", strip=True))
            for c in cells
        ]

        if len(vals) < 5:
            continue

        # -------------------------------------------------
        # 車番
        # -------------------------------------------------

        car_no = 0

        if idx_car is not None and idx_car < len(vals):
            car_no = to_int(vals[idx_car])

        if not (1 <= car_no <= 9):

            # 行先頭から車番を探す
            for v in vals[:4]:
                n = to_int(v)

                if 1 <= n <= 9:
                    car_no = n
                    break

        if not (1 <= car_no <= 9):
            continue

        # -------------------------------------------------
        # 選手名
        # -------------------------------------------------

        name = ""

        if idx_name is not None and idx_name < len(vals):
            name = clean_name(vals[idx_name])

        # 名前が空なら日本語らしいセルを探索
        if not name:

            for v in vals:

                if (
                    re.search(r"[一-龯ぁ-んァ-ヶ]", v)
                    and len(v) >= 2
                    and not any(
                        x in v
                        for x in [
                            "A1", "A2",
                            "S1", "S2",
                            "選手名",
                            "競走得点",
                        ]
                    )
                ):
                    name = clean_name(v)
                    break

        if not name:
            continue

        # -------------------------------------------------
        # 基本値
        # -------------------------------------------------

        age = (
            to_int(vals[idx_age])
            if idx_age is not None and idx_age < len(vals)
            else 0
        )

        period = (
            to_int(vals[idx_period])
            if idx_period is not None and idx_period < len(vals)
            else 0
        )

        prefecture = (
            clean_text(vals[idx_pref])
            if idx_pref is not None and idx_pref < len(vals)
            else ""
        )

        rider_class = (
            clean_text(vals[idx_class])
            if idx_class is not None and idx_class < len(vals)
            else ""
        )

        style = (
            clean_text(vals[idx_style])
            if idx_style is not None and idx_style < len(vals)
            else ""
        )

        score = (
            to_float(vals[idx_score])
            if idx_score is not None and idx_score < len(vals)
            else 0.0
        )

        # -------------------------------------------------
        # 着順
        # -------------------------------------------------

        finish = {
            "1": 0,
            "2": 0,
            "3": 0,
            "out": 0,
        }

        if idx_finish is not None and idx_finish < len(vals):

            s = vals[idx_finish]

            # 例:
            # 12-4-0-5
            # 12 4 0 5

            nums = re.findall(r"\d+", s)

            if len(nums) >= 4:

                finish["1"] = to_int(nums[0])
                finish["2"] = to_int(nums[1])
                finish["3"] = to_int(nums[2])
                finish["out"] = to_int(nums[3])

        # -------------------------------------------------
        # 決まり手
        # -------------------------------------------------

        kimari = {
            "nige": 0,
            "maki": 0,
            "sashi": 0,
            "mark": 0,
        }

        if idx_nige is not None and idx_nige < len(vals):
            kimari["nige"] = to_int(vals[idx_nige])

        if idx_maki is not None and idx_maki < len(vals):
            kimari["maki"] = to_int(vals[idx_maki])

        if idx_sashi is not None and idx_sashi < len(vals):
            kimari["sashi"] = to_int(vals[idx_sashi])

        if idx_mark is not None and idx_mark < len(vals):
            kimari["mark"] = to_int(vals[idx_mark])

        rider = {
            "car_no": car_no,
            "name": name,
            "age": age,
            "period": period,
            "prefecture": prefecture,
            "class": rider_class,
            "style": style,
            "score": score,
            "finish": finish,
            "kimari": kimari,

            "recent_results": [],

            # 予想情報
            "comment": "",
            "line": [],
            "role": "",
            "prediction_mark": "",

            # 展開
            "jump_probability": 0.0,
            "front_probability": 0.0,
            "attack_probability": 0.0,

            "comment_type": "不明",

            # AI
            "ai_score": 0.0,
            "ai_rank": 0,
            "ai_label": "",
        }

        riders.append(rider)

    # -----------------------------------------------------
    # 車番順
    # -----------------------------------------------------

    riders.sort(key=lambda x: x["car_no"])

    # 重複除去
    unique = {}

    for r in riders:
        unique[r["car_no"]] = r

    return [
        unique[k]
        for k in sorted(unique.keys())
    ]


# =========================================================
# RaceList.do 1レース取得
# =========================================================

def get_race_data(venue, race_no):
    """
    RaceList.do から1レース取得。

    ここは予想ページと完全に分離。
    """

    venue_code = venue["venue_code"]
    venue_name = venue["venue_name"]

    url = (
        f"{BASE_ODDSPARK}/RaceList.do"
        f"?joCode={venue_code}"
        f"&joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )

    # 重複パラメータが嫌われるケース対策
    url = (
        f"{BASE_ODDSPARK}/RaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={TODAY}"
        f"&raceNo={race_no}"
    )

    text = get_text(url)

    result = {
        "venue_code": venue_code,
        "venue_name": venue_name,
        "race_no": race_no,
        "url": url,
        "riders": [],
        "success": False,
    }

    if not text:
        return result

    soup = BeautifulSoup(text, "html.parser")

    table = find_race_table(soup)

    riders = extract_riders_from_table(table)

    # -----------------------------------------------------
    # フォールバック:
    # v4.0系ページでtable探索が合わない場合、
    # すべてのtableから車番1〜9を持つものを探す。
    # -----------------------------------------------------

    if len(riders) < 3:

        best = []

        for table2 in soup.find_all("table"):

            rr = extract_riders_from_table(table2)

            if len(rr) > len(best):
                best = rr

        if len(best) > len(riders):
            riders = best

    # -----------------------------------------------------
    # 7車 / 9車なら成功と判断
    # -----------------------------------------------------

    if len(riders) >= 5:
        result["riders"] = riders
        result["success"] = True

    return result


# =========================================================
# 予想情報ページ
# =========================================================

def get_prediction_page(venue_name):
    """
    OddsPark SP予想情報。

    現在確認できている形式:
    /keirin/yosou/{slug}/{YYYY}/{MMDD}.html

    例:
    /keirin/yosou/omiya/2026/1007.html
    """

    slug = VENUE_NAME_TO_SLUG.get(venue_name)

    if not slug:
        return None

    url = (
        f"{BASE_SP}/yosou/"
        f"{slug}/{TODAY[:4]}/{TODAY[4:]}.html"
    )

    return get_text(url)


# =========================================================
# 予想ページのレースブロック分割
# =========================================================

def split_prediction_blocks(soup):
    """
    予想ページを

        1R
        2R
        ...
    
    に分割。

    現在のSPページは、
    各レースに

    車 | 印 | 選手名 | コメント
    並び
    展開コメント

    が連続して掲載される構造。
    """

    blocks = {}

    headings = []

    for tag in soup.find_all(
        ["h1", "h2", "h3", "h4", "div", "p"]
    ):

        text = clean_text(tag.get_text(" ", strip=True))

        m = re.fullmatch(
            r"([1-9]|1[0-2])R",
            text
        )

        if m:
            race_no = int(m.group(1))

            if race_no not in headings:
                headings.append((race_no, tag))

    # 見出しが取れない場合
    if not headings:
        return blocks

    for i, (race_no, tag) in enumerate(headings):

        block_nodes = []

        current = tag

        next_heading_tag = (
            headings[i + 1][1]
            if i + 1 < len(headings)
            else None
        )

        # 次のRまでを収集
        for node in tag.next_elements:

            if node is next_heading_tag:
                break

            if getattr(node, "name", None):
                block_nodes.append(node)

        blocks[race_no] = block_nodes

    return blocks


# =========================================================
# 予想ページのテキスト解析
# =========================================================

def parse_prediction_block(block_nodes):
    """
    1レース分の予想情報を解析。
    """

    result = {
        "riders": {},
        "line": [],
        "styles": [],
        "comment": "",
    }

    if not block_nodes:
        return result

    # -----------------------------------------------------
    # ノードから重複を避けたテキスト行を作る
    # -----------------------------------------------------

    lines = []

    seen = set()

    for node in block_nodes:

        if not hasattr(node, "get_text"):
            continue

        text = clean_text(
            node.get_text(" ", strip=True)
        )

        if not text:
            continue

        if text in seen:
            continue

        seen.add(text)

        lines.append(text)

    # -----------------------------------------------------
    # 「車 印 選手名 コメント」の表を探す
    # -----------------------------------------------------

    for node in block_nodes:

        if getattr(node, "name", None) != "table":
            continue

        rows = node.find_all("tr")

        for tr in rows:

            cells = tr.find_all(["th", "td"])

            vals = [
                clean_text(
                    c.get_text(" ", strip=True)
                )
                for c in cells
            ]

            if len(vals) < 3:
                continue

            # ヘッダー
            if (
                "選手名" in " ".join(vals)
                and "コメント" in " ".join(vals)
            ):
                continue

            # 車番
            car_no = to_int(vals[0])

            if not (1 <= car_no <= 9):
                continue

            mark = ""
            name = ""
            comment = ""

            if len(vals) >= 2:
                mark = clean_text(vals[1])

            if len(vals) >= 3:
                name = clean_name(vals[2])

            if len(vals) >= 4:
                comment = clean_text(
                    " ".join(vals[3:])
                )

            if not name:
                continue

            result["riders"][car_no] = {
                "name": name,
                "mark": mark,
                "comment": comment,
            }

    # -----------------------------------------------------
    # 予想ライン
    #
    # 現在のページは例えば:
    #
    # | 1 | 5 | | 2 | 6 | | 4 | | 7 | 3
    #
    # のような構造。
    # -----------------------------------------------------

    for node in block_nodes:

        if getattr(node, "name", None) != "table":
            continue

        rows = node.find_all("tr")

        if not rows:
            continue

        all_text = clean_text(
            node.get_text(" ", strip=True)
        )

        # 選手コメント表は除外
        if "選手名" in all_text and "コメント" in all_text:
            continue

        numeric_rows = []

        for tr in rows:

            cells = tr.find_all(["td", "th"])

            vals = [
                clean_text(
                    c.get_text(" ", strip=True)
                )
                for c in cells
            ]

            if not vals:
                continue

            numeric_rows.append(vals)

        if not numeric_rows:
            continue

        # 数字を集める
        numbers = []

        for row in numeric_rows:

            for value in row:

                if re.fullmatch(r"[1-9]", value):
                    numbers.append(int(value))

        # 7〜9車程度の数字列ならライン候補
        if len(numbers) >= 5:

            # 同じ車番が大量にあるページナビ等を避ける
            unique_numbers = []

            for n in numbers:

                if n not in unique_numbers:
                    unique_numbers.append(n)

            if len(unique_numbers) >= 5:

                result["line"] = numbers

                # 次の行を脚質として取得
                style_candidates = []

                for row in numeric_rows:
                    for value in row:
                        if any(
                            x in value
                            for x in [
                                "逃捲",
                                "先捲",
                                "追込",
                                "差脚",
                                "自在",
                                "自力",
                                "前々",
                                "単騎",
                                "捲先",
                                "捲差",
                                "先差",
                                "追捲",
                            ]
                        ):
                            style_candidates.append(value)

                result["styles"] = style_candidates

                break

    # -----------------------------------------------------
    # 展開コメント
    #
    # 「1R出走表」の直前付近にある文章を優先
    # -----------------------------------------------------

    comments = []

    for line in lines:

        if not line:
            continue

        if (
            "出走表" in line
            or "投票" in line
            or "初心者にもオススメ" in line
            or "的中率重視" in line
            or "回収率重視" in line
            or "大穴的中重視" in line
            or "予想の並び" in line
            or "並び内の脚質" in line
            or "掲載されている情報" in line
        ):
            continue

        # 長めの日本語文章をコメント候補
        if len(line) >= 20 and re.search(
            r"[一-龯ぁ-んァ-ヶ]",
            line
        ):
            comments.append(line)

    if comments:
        # 最後の長文が展開コメントになりやすい
        result["comment"] = comments[-1]

    return result


# =========================================================
# 予想情報全体取得
# =========================================================

def get_prediction_data(venue_name):
    """
    開催場ごとに予想ページを1回取得。
    """

    text = get_prediction_page(venue_name)

    if not text:
        return {}

    soup = BeautifulSoup(text, "html.parser")

    blocks = split_prediction_blocks(soup)

    results = {}

    # 見出し解析がうまくいかない場合の強力なフォールバック
    if not blocks:

        page_text = soup.get_text("\n")

        raw_blocks = re.split(
            r"(?m)^\s*(1[0-2]|[1-9])R\s*$",
            page_text
        )

        # split結果:
        # ['', '1', '内容', '2', '内容', ...]
        if len(raw_blocks) >= 3:

            for i in range(1, len(raw_blocks), 2):

                try:
                    race_no = int(raw_blocks[i])
                except Exception:
                    continue

                body = raw_blocks[i + 1]

                results[race_no] = {
                    "raw_text": body,
                    "riders": {},
                    "line": [],
                    "styles": [],
                    "comment": "",
                }

        return results

    for race_no, nodes in blocks.items():

        results[race_no] = parse_prediction_block(
            nodes
        )

    return results


# =========================================================
# コメントタイプ判定
# =========================================================

def classify_comment(comment):
    c = clean_text(comment)

    if not c:
        return "不明"

    if any(
        x in c
        for x in [
            "先行",
            "先に行く",
            "主導権",
            "駆ける",
            "逃げる",
        ]
    ):
        return "先行意欲"

    if any(
        x in c
        for x in [
            "自力",
            "自力で",
            "捲",
            "まく",
            "仕掛ける",
        ]
    ):
        return "自力型"

    if any(
        x in c
        for x in [
            "番手",
            "マーク",
            "任せ",
            "後ろ",
        ]
    ):
        return "番手型"

    if any(
        x in c
        for x in [
            "前々",
            "自在",
            "何かする",
            "何でも",
        ]
    ):
        return "自在型"

    if any(
        x in c
        for x in [
            "単騎",
            "決めず",
        ]
    ):
        return "単騎"

    return "その他"


# =========================================================
# ライン整形
# =========================================================

def build_line_groups(line_numbers):
    """
    [1,5,2,6,4,7,3]
    のような車番列から、
    空白を失っていても基本的なラインを推定する。

    ※空白情報が消えた場合は完全復元できないので、
    「ライン候補列」として扱う。
    """

    if not line_numbers:
        return []

    cleaned = []

    for n in line_numbers:

        n = to_int(n)

        if 1 <= n <= 9:
            cleaned.append(n)

    # 重複除去
    result = []

    for n in cleaned:
        if n not in result:
            result.append(n)

    return result


# =========================================================
# ラインから役割を推定
# =========================================================

def assign_roles(riders, prediction):
    """
    コメント + ライン + 脚質から役割を付ける。
    """

    pred_riders = prediction.get("riders", {})
    line = build_line_groups(
        prediction.get("line", [])
    )

    # -----------------------------------------------------
    # まずコメント
    # -----------------------------------------------------

    for rider in riders:

        car_no = rider["car_no"]

        p = pred_riders.get(car_no, {})

        comment = clean_text(
            p.get("comment", "")
        )

        if comment:
            rider["comment"] = comment

        rider["prediction_mark"] = clean_text(
            p.get("mark", "")
        )

        rider["comment_type"] = classify_comment(
            rider["comment"]
        )

    # -----------------------------------------------------
    # ライン情報
    # -----------------------------------------------------

    if line:

        for rider in riders:

            car = rider["car_no"]

            if car in line:
                rider["line"] = line

    # -----------------------------------------------------
    # 役割
    # -----------------------------------------------------

    for rider in riders:

        comment = rider["comment"]
        style = rider["style"]

        if any(
            x in comment
            for x in [
                "番手",
                "マーク",
                "任せ",
                "後ろ",
            ]
        ):
            rider["role"] = "番手"

        elif any(
            x in comment
            for x in [
                "先行",
                "主導権",
                "駆け",
                "逃げ",
            ]
        ):
            rider["role"] = "先行"

        elif any(
            x in comment
            for x in [
                "自力",
                "捲",
                "まく",
                "仕掛け",
            ]
        ):
            rider["role"] = "自力"

        elif any(
            x in comment
            for x in [
                "単騎",
                "決めず",
            ]
        ):
            rider["role"] = "単騎"

        elif "追" in style:
            rider["role"] = "追込"

        elif "逃" in style:
            rider["role"] = "先行"

        elif "両" in style:
            rider["role"] = "自在"

        else:
            rider["role"] = "不明"


# =========================================================
# 展開確率
# =========================================================

def calculate_probabilities(rider):
    """
    既存データから展開確率を算出。

    ここは「実測データ」ではなくAI推定値。
    JSON上でも推定値として利用。
    """

    kimari = rider.get("kimari", {})

    nige = to_float(kimari.get("nige", 0))
    maki = to_float(kimari.get("maki", 0))
    sashi = to_float(kimari.get("sashi", 0))

    total = nige + maki + sashi

    if total <= 0:
        front = 0.0
        attack = 0.0
        jump = 0.0
    else:

        front = (
            (nige * 0.80 + maki * 0.45)
            / total
            * 100
        )

        attack = (
            (nige * 0.45 + maki * 0.90)
            / total
            * 100
        )

        jump = (
            (nige * 0.30 + sashi * 0.20)
            / total
            * 100
        )

    # コメント補正
    ctype = rider.get("comment_type", "")

    if ctype == "先行意欲":
        front += 15
        attack += 8

    elif ctype == "自力型":
        attack += 12

    elif ctype == "番手型":
        jump += 8

    elif ctype == "単騎":
        jump += 5

    rider["front_probability"] = round(
        min(100, max(0, front)),
        1
    )

    rider["attack_probability"] = round(
        min(100, max(0, attack)),
        1
    )

    rider["jump_probability"] = round(
        min(100, max(0, jump)),
        1
    )


# =========================================================
# AIスコア
# =========================================================

def calculate_ai_score(rider):
    """
    AIスコア。

    競走得点
    + 着順実績
    + 決まり手
    + コメント
    + 展開
    を統合。
    """

    score = to_float(
        rider.get("score", 0)
    )

    finish = rider.get("finish", {})

    first = to_float(finish.get("1", 0))
    second = to_float(finish.get("2", 0))
    third = to_float(finish.get("3", 0))

    kimari = rider.get("kimari", {})

    nige = to_float(kimari.get("nige", 0))
    maki = to_float(kimari.get("maki", 0))
    sashi = to_float(kimari.get("sashi", 0))

    # 得点を中心にする
    ai = score

    # 着順実績
    ai += first * 0.35
    ai += second * 0.18
    ai += third * 0.08

    # 決まり手
    ai += nige * 0.08
    ai += maki * 0.12
    ai += sashi * 0.06

    # 展開
    ai += rider.get(
        "attack_probability",
        0
    ) * 0.025

    ai += rider.get(
        "front_probability",
        0
    ) * 0.015

    # コメント
    ctype = rider.get(
        "comment_type",
        ""
    )

    if ctype == "先行意欲":
        ai += 0.8

    elif ctype == "自力型":
        ai += 0.7

    elif ctype == "番手型":
        ai += 0.9

    elif ctype == "自在型":
        ai += 0.4

    rider["ai_score"] = round(
        ai,
        2
    )

    return rider["ai_score"]


# =========================================================
# AI順位
# =========================================================

def rank_riders(riders):
    sorted_riders = sorted(
        riders,
        key=lambda x: x.get(
            "ai_score",
            0
        ),
        reverse=True
    )

    for i, rider in enumerate(
        sorted_riders,
        start=1
    ):
        rider["ai_rank"] = i

        if i == 1:
            rider["ai_label"] = "本命"

        elif i == 2:
            rider["ai_label"] = "対抗"

        elif i == 3:
            rider["ai_label"] = "単穴"

        elif i <= 5:
            rider["ai_label"] = "連下"

        else:
            rider["ai_label"] = "穴候補"


# =========================================================
# レースAI
# =========================================================

def calculate_race_ai(race):
    riders = race.get("riders", [])

    if not riders:
        return

    for rider in riders:

        calculate_probabilities(
            rider
        )

        calculate_ai_score(
            rider
        )

    rank_riders(riders)

    # -----------------------------------------------------
    # レース全体の本命
    # -----------------------------------------------------

    top = sorted(
        riders,
        key=lambda x: x.get(
            "ai_score",
            0
        ),
        reverse=True
    )

    race["ai_prediction"] = {
        "main": top[0]["car_no"]
        if top else None,

        "opponent": top[1]["car_no"]
        if len(top) >= 2
        else None,

        "third": top[2]["car_no"]
        if len(top) >= 3
        else None,

        "hole": top[4]["car_no"]
        if len(top) >= 5
        else (
            top[-1]["car_no"]
            if top
            else None
        ),
    }


# =========================================================
# メイン
# =========================================================

def main():

    start_time = time.time()

    print()
    print("==============================")
    print(" KEIRIN AI DATA UPDATE v4.3")
    print("==============================")
    print(f"対象日: {TODAY}")
    print("==============================")

    # -----------------------------------------------------
    # 1. 開催場
    # -----------------------------------------------------

    venues = get_venues()

    if not venues:

        print("開催場を取得できませんでした。")
        return

    print(f"開催場数: {len(venues)}")

    for v in venues:
        print(
            f"  {v['venue_code']} "
            f"{v['venue_name']}"
        )

    # -----------------------------------------------------
    # 2. レース番号
    # -----------------------------------------------------

    all_races = []

    for venue in venues:

        races = get_race_numbers(
            venue["venue_code"]
        )

        venue["race_numbers"] = races

        print(
            f"{venue['venue_name']}: "
            f"{len(races)}レース"
        )

        for race_no in races:

            all_races.append({
                "venue_code": venue["venue_code"],
                "venue_name": venue["venue_name"],
                "race_no": race_no,
            })

    print()
    print(
        f"詳細取得対象レース: "
        f"{len(all_races)}"
    )

    # -----------------------------------------------------
    # 3. RaceList.do
    # -----------------------------------------------------

    print()
    print("選手データ取得中...")

    race_results = []

    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for item in all_races:

            venue = {
                "venue_code":
                    item["venue_code"],
                "venue_name":
                    item["venue_name"],
            }

            future = executor.submit(
                get_race_data,
                venue,
                item["race_no"]
            )

            futures[future] = item

        for future in as_completed(futures):

            item = futures[future]

            try:
                result = future.result()

            except Exception as e:

                print(
                    f"[RACE ERROR] "
                    f"{item['venue_name']} "
                    f"{item['race_no']}R "
                    f"{e}"
                )

                result = {
                    **item,
                    "riders": [],
                    "success": False,
                }

            race_results.append(result)

            if result.get("success"):
                success_count += 1
            else:
                fail_count += 1

    # -----------------------------------------------------
    # 取得順を元に戻す
    # -----------------------------------------------------

    race_results.sort(
        key=lambda x: (
            x["venue_code"],
            x["race_no"],
        )
    )

    print()
    print(
        f"選手データ取得結果: "
        f"{success_count}/{len(all_races)}"
    )

    # -----------------------------------------------------
    # 4. 予想ページ
    # -----------------------------------------------------

    print()
    print(
        "コメント・並び・展開情報取得中..."
    )

    prediction_by_venue = {}

    prediction_ok = 0

    for venue in venues:

        venue_name = venue["venue_name"]

        try:

            data = get_prediction_data(
                venue_name
            )

            prediction_by_venue[
                venue_name
            ] = data

            if data:
                prediction_ok += 1

            print(
                f"  {venue_name}: "
                f"{len(data)}レース予想"
            )

        except Exception as e:

            print(
                f"  {venue_name}: "
                f"予想取得失敗 "
                f"{type(e).__name__}: {e}"
            )

            prediction_by_venue[
                venue_name
            ] = {}

    # -----------------------------------------------------
    # 5. 予想情報をレースへ結合
    # -----------------------------------------------------

    comment_count = 0
    line_count = 0
    development_count = 0
    rider_comment_count = 0

    for race in race_results:

        venue_name = race["venue_name"]
        race_no = race["race_no"]

        prediction = (
            prediction_by_venue
            .get(venue_name, {})
            .get(race_no, {})
        )

        # 予想情報
        pred_riders = prediction.get(
            "riders",
            {}
        )

        # -------------------------------------------------
        # 選手コメント
        # -------------------------------------------------

        for rider in race["riders"]:

            car_no = rider["car_no"]

            p = pred_riders.get(
                car_no,
                {}
            )

            comment = clean_text(
                p.get("comment", "")
            )

            if comment:

                rider["comment"] = comment

                rider_comment_count += 1

            mark = clean_text(
                p.get("mark", "")
            )

            if mark:
                rider[
                    "prediction_mark"
                ] = mark

            rider[
                "comment_type"
            ] = classify_comment(
                rider.get(
                    "comment",
                    ""
                )
            )

        # -------------------------------------------------
        # ライン
        # -------------------------------------------------

        line = prediction.get(
            "line",
            []
        )

        if line:

            line = build_line_groups(
                line
            )

            for rider in race["riders"]:

                if rider["car_no"] in line:
                    rider["line"] = line

            line_count += 1

        # -------------------------------------------------
        # 展開コメント
        # -------------------------------------------------

        development_comment = clean_text(
            prediction.get(
                "comment",
                ""
            )
        )

        race["development_comment"] = (
            development_comment
        )

        if development_comment:
            development_count += 1

        # -------------------------------------------------
        # 役割
        # -------------------------------------------------

        assign_roles(
            race["riders"],
            prediction
        )

        # -------------------------------------------------
        # 展開確率
        # -------------------------------------------------

        for rider in race["riders"]:
            calculate_probabilities(
                rider
            )

        if development_comment:
            comment_count += 1

    # -----------------------------------------------------
    # 6. AI
    # -----------------------------------------------------

    print()
    print("AI評価計算中...")

    ai_races = 0
    ai_riders = 0

    for race in race_results:

        if not race["riders"]:
            continue

        calculate_race_ai(
            race
        )

        ai_races += 1
        ai_riders += len(
            race["riders"]
        )

    # -----------------------------------------------------
    # 7. 出力JSON
    # -----------------------------------------------------

    output = {
        "updated_at": datetime.now().isoformat(
            timespec="seconds"
        ),

        "target_date": TODAY,

        "version": "4.3",

        "venues": venues,

        "races": race_results,

        "stats": {
            "venue_count": len(venues),
            "race_count": len(race_results),
            "successful_races": success_count,
            "failed_races": fail_count,

            "rider_count": sum(
                len(r.get("riders", []))
                for r in race_results
            ),

            "prediction_venues": prediction_ok,

            "prediction_races": sum(
                len(x)
                for x in prediction_by_venue.values()
            ),

            "development_comments":
                development_count,

            "rider_comments":
                rider_comment_count,

            "line_races":
                line_count,

            "ai_races":
                ai_races,

            "ai_riders":
                ai_riders,
        },
    }

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    # 一時ファイルに保存してから置換
    temp_file = OUTPUT_FILE + ".tmp"

    with open(
        temp_file,
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
        temp_file,
        OUTPUT_FILE
    )

    elapsed = time.time() - start_time

    # -----------------------------------------------------
    # 8. 結果
    # -----------------------------------------------------

    print()
    print("==============================")
    print(" 更新完了 v4.3")
    print("==============================")

    print(
        f"開催場: {len(venues)}"
    )

    print(
        f"レース: {len(race_results)}"
    )

    rider_total = sum(
        len(r.get("riders", []))
        for r in race_results
    )

    valid_name_count = sum(
        1
        for race in race_results
        for rider in race.get(
            "riders",
            []
        )
        if rider.get("name")
    )

    print(
        f"選手: {rider_total}"
    )

    print(
        f"正しい選手名: "
        f"{valid_name_count}"
    )

    print()
    print("【取得状況】")

    print(
        f"選手データ成功: "
        f"{success_count}/"
        f"{len(all_races)}"
    )

    print(
        f"選手データ失敗: "
        f"{fail_count}"
    )

    print(
        f"予想情報開催場: "
        f"{prediction_ok}/"
        f"{len(venues)}"
    )

    print(
        f"予想情報レース: "
        f"{sum(len(x) for x in prediction_by_venue.values())}"
    )

    print(
        f"選手コメント: "
        f"{rider_comment_count}"
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
        f"{ai_races}"
    )

    print(
        f"AI評価選手: "
        f"{ai_riders}"
    )

    print(
        f"処理時間: "
        f"{elapsed:.1f}秒"
    )

    print(
        f"保存先: {OUTPUT_FILE}"
    )

    print()


if __name__ == "__main__":
    main()
