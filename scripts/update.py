import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import unescape
from html.parser import HTMLParser


# ============================================================
# KEIRIN AI DATA UPDATE
# AI 1.3
# ============================================================

JST = timezone(timedelta(hours=9))

OUTPUT_FILE = Path("data/today.json")

BASE_URL = "https://www.oddspark.com/keirin/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.7,en;q=0.5",
    "Connection": "keep-alive",
}


# ============================================================
# 競輪場コード
# ============================================================

VENUE_CODES = {
    "函館": "11",
    "青森": "12",
    "いわき平": "13",
    "弥彦": "21",
    "前橋": "22",
    "取手": "23",
    "宇都宮": "24",
    "大宮": "25",
    "西武園": "26",
    "京王閣": "27",
    "立川": "28",
    "松戸": "31",
    "川崎": "34",
    "平塚": "35",
    "小田原": "36",
    "伊東": "37",
    "静岡": "38",
    "名古屋": "42",
    "岐阜": "43",
    "大垣": "44",
    "豊橋": "45",
    "富山": "46",
    "松阪": "47",
    "四日市": "48",
    "福井": "51",
    "奈良": "53",
    "向日町": "54",
    "和歌山": "55",
    "岸和田": "56",
    "玉野": "61",
    "広島": "62",
    "防府": "63",
    "高松": "71",
    "小松島": "73",
    "高知": "74",
    "松山": "75",
    "小倉": "81",
    "久留米": "83",
    "武雄": "84",
    "佐世保": "85",
    "別府": "86",
    "熊本": "87",
}


# ============================================================
# HTTP
# ============================================================

def fetch(url, retries=2, sleep_sec=0.15):
    """
    ページ取得
    """
    last_error = None

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)

            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read()

            # OddsParkはUTF-8が基本
            text = raw.decode("utf-8", errors="ignore")

            if text:
                time.sleep(sleep_sec)
                return text

        except Exception as e:
            last_error = e

            if attempt < retries:
                time.sleep(1.0)

    print(f"  ERROR: {url}")
    print(f"  {last_error}")

    return ""


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.tables = []

        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.in_th = False

        self.current_table = []
        self.current_row = []
        self.current_cell = []

    def handle_starttag(self, tag, attrs):

        tag = tag.lower()

        if tag == "table":
            self.in_table = True
            self.current_table = []

        elif tag == "tr" and self.in_table:
            self.in_tr = True
            self.current_row = []

        elif tag in ("td", "th") and self.in_tr:
            self.in_td = True
            self.in_th = tag == "th"
            self.current_cell = []

    def handle_endtag(self, tag):

        tag = tag.lower()

        if tag in ("td", "th") and self.in_td:

            text = clean_text("".join(self.current_cell))
            self.current_row.append(text)

            self.current_cell = []
            self.in_td = False
            self.in_th = False

        elif tag == "tr" and self.in_tr:

            if self.current_row:
                self.current_table.append(self.current_row)

            self.current_row = []
            self.in_tr = False

        elif tag == "table" and self.in_table:

            if self.current_table:
                self.tables.append(self.current_table)

            self.current_table = []
            self.in_table = False

    def handle_data(self, data):

        if self.in_td:
            self.current_cell.append(data)


# ============================================================
# TEXT CLEAN
# ============================================================

def clean_text(text):
    text = unescape(text or "")

    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")

    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# TODAY
# ============================================================

def today_jst():
    return datetime.now(JST).strftime("%Y%m%d")


# ============================================================
# 開催場取得
# ============================================================

def find_today_venues(html, date_str):

    venues = []

    for venue, code in VENUE_CODES.items():

        # 会場コード＋開催日が存在するか確認
        pattern1 = rf"joCode={code}&kaisaiBi={date_str}"
        pattern2 = rf"joCd={code}.*?kaisaiBi={date_str}"

        if re.search(pattern1, html) or re.search(pattern2, html):
            venues.append({
                "name": venue,
                "code": code,
            })

    return venues


# ============================================================
# レースURL取得
# ============================================================

def get_race_urls(venue_code, date_str):

    url = (
        f"{BASE_URL}AllRaceList.do"
        f"?joCode={venue_code}"
        f"&kaisaiBi={date_str}"
    )

    html = fetch(url)

    if not html:
        return []

    urls = []

    # RaceList.do のリンクを探す
    pattern = (
        r"(?:https?://www\.oddspark\.com)?"
        r"/keirin/RaceList\.do"
        r"\?joCode="
        + re.escape(venue_code)
        + r"&kaisaiBi="
        + re.escape(date_str)
        + r"&raceNo=(\d+)"
    )

    for match in re.finditer(pattern, html):

        race_no = int(match.group(1))

        race_url = (
            f"{BASE_URL}RaceList.do"
            f"?joCode={venue_code}"
            f"&kaisaiBi={date_str}"
            f"&raceNo={race_no}"
        )

        if race_url not in urls:
            urls.append(race_url)

    # リンク形式が違う場合の保険
    if not urls:

        pattern2 = (
            r"RaceList\.do\?[^\"']*?"
            r"joCode="
            + re.escape(venue_code)
            + r"[^\"']*?"
            r"kaisaiBi="
            + re.escape(date_str)
            + r"[^\"']*?"
            r"raceNo=(\d+)"
        )

        race_numbers = set()

        for match in re.finditer(pattern2, html):
            race_numbers.add(int(match.group(1)))

        for race_no in sorted(race_numbers):

            urls.append(
                f"{BASE_URL}RaceList.do"
                f"?joCode={venue_code}"
                f"&kaisaiBi={date_str}"
                f"&raceNo={race_no}"
            )

    return sorted(
        urls,
        key=lambda x: int(
            re.search(r"raceNo=(\d+)", x).group(1)
        )
    )


# ============================================================
# 数値
# ============================================================

def to_float(value):

    if value is None:
        return None

    m = re.search(r"(\d+(?:\.\d+)?)", value)

    if not m:
        return None

    try:
        return float(m.group(1))
    except:
        return None


def to_int(value):

    if value is None:
        return None

    m = re.search(r"(\d+)", value)

    if not m:
        return None

    try:
        return int(m.group(1))
    except:
        return None


# ============================================================
# 競走得点
# ============================================================

def extract_race_point(text):

    patterns = [
        r"競走得点[:：]?\s*(\d+(?:\.\d+)?)",
        r"得点[:：]?\s*(\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:

        m = re.search(pattern, text)

        if m:
            try:
                return float(m.group(1))
            except:
                pass

    return None


# ============================================================
# 着順
# ============================================================

def extract_finish_stats(text):

    # 例:
    # 着順：9-3-3-12
    # 着順：9- 3- 3- 12
    m = re.search(
        r"着\s*順\s*[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        text
    )

    if not m:
        return {
            "first": None,
            "second": None,
            "third": None,
            "other": None,
        }

    return {
        "first": int(m.group(1)),
        "second": int(m.group(2)),
        "third": int(m.group(3)),
        "other": int(m.group(4)),
    }


# ============================================================
# 勝率
# ============================================================

def extract_rates(text):

    # 例
    # 16.6% 36.6% 36.7%
    values = re.findall(r"(\d+(?:\.\d+)?)\s*%", text)

    if len(values) >= 3:

        try:
            return {
                "win_rate": float(values[0]),
                "quinella_rate": float(values[1]),
                "trifecta_rate": float(values[2]),
            }
        except:
            pass

    return {
        "win_rate": None,
        "quinella_rate": None,
        "trifecta_rate": None,
    }


# ============================================================
# 決まり手
# ============================================================

def extract_decisive(text):

    # 例
    # 決まり手：5-4-1-2
    m = re.search(
        r"決まり手\s*[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        text
    )

    if not m:
        return {
            "escape": None,
            "sweep": None,
            "difference": None,
            "mark": None,
        }

    return {
        "escape": int(m.group(1)),
        "sweep": int(m.group(2)),
        "difference": int(m.group(3)),
        "mark": int(m.group(4)),
    }


# ============================================================
# S H B
# ============================================================

def extract_shb(text):

    result = {
        "S": None,
        "H": None,
        "B": None,
    }

    for key in ("S", "H", "B"):

        m = re.search(
            rf"\b{key}\s*[:：]?\s*(\d+)",
            text
        )

        if m:
            result[key] = int(m.group(1))

    return result


# ============================================================
# 脚質
# ============================================================

def extract_style(text):

    styles = [
        "逃",
        "追",
        "両",
        "自在",
    ]

    for style in styles:

        if re.search(
            rf"(?:脚質|脚\s*質)\s*[:：]?\s*{style}",
            text
        ):
            return style

    # 出走表では単独の「逃」「追」になっている場合がある
    if re.search(r"\b逃\b", text):
        return "逃"

    if re.search(r"\b追\b", text):
        return "追"

    if re.search(r"\b両\b", text):
        return "両"

    return None


# ============================================================
# 選手行判定
# ============================================================

def is_rider_row(row):

    text = " ".join(row)

    if "誘導" in text:
        return False

    # 競走得点がある行
    if "競走得点" in text:
        return True

    # 車番＋選手名っぽい行
    if len(row) >= 3:

        if re.fullmatch(r"\d+", row[0] or ""):
            return True

        if re.fullmatch(r"\d+", row[1] or ""):
            return True

    return False


# ============================================================
# 選手名抽出
# ============================================================

def extract_name(row):

    # よくあるパターン:
    # [枠番, 車番, 選手名, ...]
    #
    # 先頭の番号を除き、
    # 府県・年齢・期別などが入っている列を探す。

    for cell in row:

        cell = clean_text(cell)

        if not cell:
            continue

        if re.fullmatch(r"\d+", cell):
            continue

        if "競走得点" in cell:
            continue

        if "着順" in cell:
            continue

        if "決まり手" in cell:
            continue

        if "％" in cell or "%" in cell:
            continue

        # 年齢/期別/級班を含むセルは選手情報セル
        if re.search(r"\d+歳", cell):

            # 「佐藤譲士郎 26歳／123期／A級3班」
            name = re.split(r"\d+歳", cell)[0]

            name = clean_text(name)

            if name:
                return name

        # 府県付きの選手名
        if re.search(
            r"[北海道青森岩手宮城秋田山形福島"
            r"茨城栃木群馬埼玉千葉東京神奈川"
            r"新潟富山石川福井山梨長野岐阜静岡愛知三重"
            r"滋賀京都大阪兵庫奈良和歌山"
            r"鳥取島根岡山広島山口徳島香川愛媛高知"
            r"福岡佐賀長崎熊本大分宮崎鹿児島沖縄]",
            cell
        ):

            # 府県の後ろを削る
            parts = re.split(
                r"[（(]",
                cell
            )

            candidate = clean_text(parts[0])

            if candidate and len(candidate) <= 20:
                return candidate

    return None


# ============================================================
# 車番
# ============================================================

def extract_car_number(row):

    for i, cell in enumerate(row[:4]):

        m = re.fullmatch(r"\s*([1-9])\s*", cell or "")

        if m:
            return int(m.group(1))

    return None


# ============================================================
# 選手データ解析
# ============================================================

def parse_riders_from_tables(tables):

    riders = []

    for table in tables:

        if not table:
            continue

        for row in table:

            if not is_rider_row(row):
                continue

            text = " ".join(row)

            car_no = extract_car_number(row)

            if car_no is None:
                continue

            name = extract_name(row)

            if not name:
                continue

            point = extract_race_point(text)

            finish = extract_finish_stats(text)

            rates = extract_rates(text)

            decisive = extract_decisive(text)

            shb = extract_shb(text)

            style = extract_style(text)

            rider = {
                "car_number": car_no,
                "name": name,
                "race_point": point,

                "finish_stats": finish,

                "win_rate": rates["win_rate"],
                "quinella_rate": rates["quinella_rate"],
                "trifecta_rate": rates["trifecta_rate"],

                "decisive": decisive,

                "S": shb["S"],
                "H": shb["H"],
                "B": shb["B"],

                "style": style,

                "raw": row,
            }

            # 同じ車番を二重登録しない
            duplicate = False

            for old in riders:

                if (
                    old["car_number"] == rider["car_number"]
                    and old["name"] == rider["name"]
                ):
                    duplicate = True
                    break

            if not duplicate:
                riders.append(rider)

    riders.sort(key=lambda x: x["car_number"])

    return riders


# ============================================================
# AI スコア
# ============================================================

def calculate_ai_score(rider):

    point = rider.get("race_point")

    if point is None:
        return None

    score = 0.0

    # --------------------------------------------------------
    # 競走得点
    # --------------------------------------------------------

    score += point * 1.00

    # --------------------------------------------------------
    # 勝率
    # --------------------------------------------------------

    win_rate = rider.get("win_rate")

    if win_rate is not None:
        score += win_rate * 0.25

    # --------------------------------------------------------
    # 2連対率
    # --------------------------------------------------------

    quinella = rider.get("quinella_rate")

    if quinella is not None:
        score += quinella * 0.12

    # --------------------------------------------------------
    # 3連対率
    # --------------------------------------------------------

    trifecta = rider.get("trifecta_rate")

    if trifecta is not None:
        score += trifecta * 0.08

    # --------------------------------------------------------
    # 逃げ・捲り・差し
    # --------------------------------------------------------

    decisive = rider.get("decisive") or {}

    escape = decisive.get("escape")
    sweep = decisive.get("sweep")
    difference = decisive.get("difference")

    if escape is not None:
        score += escape * 0.10

    if sweep is not None:
        score += sweep * 0.08

    if difference is not None:
        score += difference * 0.05

    # --------------------------------------------------------
    # B数
    # --------------------------------------------------------

    B = rider.get("B")

    if B is not None:
        score += B * 0.03

    return round(score, 3)


# ============================================================
# AI 評価
# ============================================================

def add_ai_evaluation(riders):

    valid = []

    for rider in riders:

        score = calculate_ai_score(rider)

        rider["ai_score"] = score

        if score is not None:
            valid.append(rider)

    valid.sort(
        key=lambda x: x["ai_score"],
        reverse=True
    )

    # 全選手の順位
    for rank, rider in enumerate(valid, start=1):
        rider["ai_rank"] = rank

    # ラベル
    for rider in riders:

        rider["ai_label"] = "評価データ不足"

    if len(valid) >= 1:
        valid[0]["ai_label"] = "本命"

    if len(valid) >= 2:
        valid[1]["ai_label"] = "対抗"

    if len(valid) >= 3:
        valid[2]["ai_label"] = "単穴"

    # 穴候補
    if len(valid) >= 4:

        # 4～6位から穴候補を選ぶ
        hole_candidates = valid[3:6]

        if hole_candidates:
            hole = max(
                hole_candidates,
                key=lambda x: (
                    x.get("trifecta_rate") or 0
                )
            )

            hole["ai_label"] = "穴"

    # 消し候補
    if len(valid) >= 5:

        # 下位選手
        bottom = valid[-1]

        if bottom["ai_label"] == "評価データ不足":
            bottom["ai_label"] = "消し"

    return len(valid)


# ============================================================
# レース詳細取得
# ============================================================

def get_race_data(
    venue_name,
    venue_code,
    date_str,
    race_no,
    url
):

    print(
        f"    詳細取得: "
        f"{venue_name} {race_no}R"
    )

    html = fetch(url)

    if not html:
        return None

    parser = TableParser()
    parser.feed(html)

    tables = parser.tables

    riders = parse_riders_from_tables(tables)

    # 詳細ページに存在する主要データ
    race_point_count = sum(
        1
        for r in riders
        if r.get("race_point") is not None
    )

    ai_count = add_ai_evaluation(riders)

    race = {
        "race_no": race_no,
        "url": url,
        "riders": riders,

        "race_point_count": race_point_count,
        "ai_rider_count": ai_count,
    }

    return race


# ============================================================
# 開催場データ
# ============================================================

def get_venue_data(
    venue_name,
    venue_code,
    date_str
):

    print(
        f"\n[{venue_name}] "
        f"コード={venue_code}"
    )

    race_urls = get_race_urls(
        venue_code,
        date_str
    )

    print(
        f"  レース数: {len(race_urls)}"
    )

    races = []

    total_points = 0
    total_riders = 0
    total_ai = 0

    for url in race_urls:

        m = re.search(
            r"raceNo=(\d+)",
            url
        )

        if not m:
            continue

        race_no = int(m.group(1))

        race = get_race_data(
            venue_name,
            venue_code,
            date_str,
            race_no,
            url
        )

        if not race:
            continue

        races.append(race)

        total_points += race[
            "race_point_count"
        ]

        total_riders += len(
            race["riders"]
        )

        total_ai += race[
            "ai_rider_count"
        ]

    print(
        f"  選手数: {total_riders}"
    )

    print(
        f"  競走得点取得: "
        f"{total_points}人"
    )

    print(
        f"  AI評価: "
        f"{total_ai}人"
    )

    return {
        "name": venue_name,
        "code": venue_code,
        "races": races,
    }


# ============================================================
# 保存
# ============================================================

def save(data):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# MAIN
# ============================================================

def main():

    date_str = today_jst()

    print()
    print("==============================")
    print(" KEIRIN AI DATA UPDATE")
    print("==============================")
    print(
        f"対象日: {date_str}"
    )
    print("==============================")

    # --------------------------------------------------------
    # TOP
    # --------------------------------------------------------

    top_url = (
        f"{BASE_URL}KeirinTop.do"
    )

    print(
        f"TOP取得: {top_url}"
    )

    top_html = fetch(top_url)

    if not top_html:
        print(
            "TOPページ取得失敗"
        )
        return

    # --------------------------------------------------------
    # 開催場
    # --------------------------------------------------------

    venues = find_today_venues(
        top_html,
        date_str
    )

    print(
        f"開催場数: {len(venues)}"
    )

    if not venues:

        print(
            "本日の開催場を取得できませんでした"
        )

        return

    # --------------------------------------------------------
    # 全開催場
    # --------------------------------------------------------

    venue_data = []

    total_races = 0
    total_riders = 0
    total_points = 0
    total_ai = 0

    for venue in venues:

        data = get_venue_data(
            venue["name"],
            venue["code"],
            date_str
        )

        venue_data.append(data)

        total_races += len(
            data["races"]
        )

        for race in data["races"]:

            total_riders += len(
                race["riders"]
            )

            total_points += race[
                "race_point_count"
            ]

            total_ai += race[
                "ai_rider_count"
            ]

    # --------------------------------------------------------
    # 出力
    # --------------------------------------------------------

    output = {
        "updated_at": datetime.now(
            JST
        ).isoformat(),

        "date": date_str,

        "ai_version": "1.3",

        "source": "OddsPark",

        "venues": venue_data,

        "summary": {
            "venue_count": len(
                venue_data
            ),
            "race_count": total_races,
            "rider_count": total_riders,
            "race_point_count": total_points,
            "ai_rider_count": total_ai,
        },
    }

    save(output)

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    print()
    print("==============================")
    print(" UPDATE COMPLETE")
    print("==============================")

    print(
        f"開催場: {len(venue_data)}"
    )

    print(
        f"レース: {total_races}"
    )

    print(
        f"選手: {total_riders}"
    )

    print(
        f"競走得点取得: "
        f"{total_points}"
    )

    print(
        f"AI評価選手: "
        f"{total_ai}"
    )

    print(
        "AIバージョン: 1.3"
    )

    print(
        f"保存先: {OUTPUT_FILE}"
    )

    print("==============================")


if __name__ == "__main__":
    main()
