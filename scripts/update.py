import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import unescape
from html.parser import HTMLParser
from itertools import permutations


JST = timezone(timedelta(hours=9))

OUTPUT_FILE = Path("data/today.json")

BASE_URL = "https://www.oddspark.com/keirin/"


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


def fetch(url, timeout=30, retries=3):
    for attempt in range(1, retries + 1):
        try:
            print(f"GET {attempt}/{retries}: {url}")

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/140.0 Safari/537.36"
                    ),
                    "Accept-Language": "ja-JP,ja;q=0.9",
                    "Accept": (
                        "text/html,"
                        "application/xhtml+xml,"
                        "application/xml;q=0.9,"
                        "*/*;q=0.8"
                    ),
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:

                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

                data = response.read()

                return data.decode(
                    charset,
                    errors="ignore",
                )

        except Exception as e:
            print("取得失敗:", str(e))

            if attempt < retries:
                time.sleep(3)

    raise RuntimeError(
        f"ページ取得に失敗しました: {url}"
    )


class TableParser(HTMLParser):

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.tables = []

        self.current_table = None
        self.current_row = None
        self.current_cell = None

        self.in_table = False
        self.in_row = False
        self.in_cell = False

    def handle_starttag(self, tag, attrs):

        tag = tag.lower()

        if tag == "table":

            self.in_table = True
            self.current_table = []

        elif tag == "tr" and self.in_table:

            self.in_row = True
            self.current_row = []

        elif (
            tag in ("td", "th")
            and self.in_row
        ):

            self.in_cell = True
            self.current_cell = ""

    def handle_data(self, data):

        if (
            self.in_cell
            and self.current_cell is not None
        ):

            self.current_cell += data

    def handle_endtag(self, tag):

        tag = tag.lower()

        if (
            tag in ("td", "th")
            and self.in_cell
        ):

            text = self.current_cell or ""

            text = unescape(text)

            text = re.sub(
                r"\s+",
                " ",
                text,
            ).strip()

            self.current_row.append(text)

            self.current_cell = None
            self.in_cell = False

        elif (
            tag == "tr"
            and self.in_row
        ):

            if self.current_row:
                self.current_table.append(
                    self.current_row
                )

            self.current_row = None
            self.in_row = False

        elif (
            tag == "table"
            and self.in_table
        ):

            if self.current_table:
                self.tables.append(
                    self.current_table
                )

            self.current_table = None
            self.in_table = False


def clean_text(html):

    text = unescape(html)

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.S | re.I,
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.S | re.I,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def find_today_venues(html):

    text = clean_text(html)

    venues = []

    for name, code in VENUE_CODES.items():

        if name in text:

            venues.append({
                "name": name,
                "code": code,
            })

    return venues


def find_race_info(html):

    text = clean_text(html)

    matches = list(
        re.finditer(
            r"第\s*(1[0-2]|[1-9])R",
            text
        )
    )

    races = []

    seen = set()

    for i, match in enumerate(matches):

        number = int(
            match.group(1)
        )

        if number in seen:
            continue

        seen.add(number)

        start = match.start()

        if i + 1 < len(matches):

            end = matches[i + 1].start()

        else:

            end = len(text)

        section = text[start:end]

        start_time = ""

        time_match = re.search(
            r"発走時間\s*([0-9]{1,2}:[0-9]{2})",
            section
        )

        if time_match:

            start_time = time_match.group(1)

        race_class = ""

        class_match = re.search(
            r"\(([^)]+)\)",
            section
        )

        if class_match:

            value = class_match.group(1)

            if (
                "級" in value
                or "Ｌ級" in value
            ):

                race_class = value

        races.append({
            "number": number,
            "start_time": start_time,
            "class": race_class,
        })

    races.sort(
        key=lambda x: x["number"]
    )

    return races


def parse_number(value):

    if not value:
        return None

    value = value.replace(
        ",",
        ""
    )

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        value
    )

    if not match:
        return None

    try:

        number = float(
            match.group(0)
        )

        if number.is_integer():
            return int(number)

        return number

    except Exception:

        return None


def parse_dash_numbers(value):

    if not value:
        return []

    value = (
        value
        .replace(" ", "")
        .replace("　", "")
    )

    numbers = re.findall(
        r"\d+",
        value
    )

    return [
        int(x)
        for x in numbers
    ]


def parse_recent_record(value):

    result = {
        "first": 0,
        "second": 0,
        "third": 0,
        "other": 0,
    }

    if not value:
        return result

    match = re.search(
        r"着\s*順\s*[:：]\s*"
        r"(\d+)\s*-\s*"
        r"(\d+)\s*-\s*"
        r"(\d+)\s*-\s*"
        r"(\d+)",
        value
    )

    if match:

        result["first"] = int(
            match.group(1)
        )

        result["second"] = int(
            match.group(2)
        )

        result["third"] = int(
            match.group(3)
        )

        result["other"] = int(
            match.group(4)
        )

    return result


def parse_tactics(value):

    result = {
        "escape": 0,
        "maki": 0,
        "difference": 0,
        "mark": 0,
    }

    if not value:
        return result

    match = re.search(
        r"決まり手\s*[:：]\s*"
        r"(\d+)\s*-\s*"
        r"(\d+)\s*-\s*"
        r"(\d+)\s*-\s*"
        r"(\d+)",
        value
    )

    if match:

        result["escape"] = int(
            match.group(1)
        )

        result["maki"] = int(
            match.group(2)
        )

        result["difference"] = int(
            match.group(3)
        )

        result["mark"] = int(
            match.group(4)
        )

    return result


def extract_recent_races(row):

    recent = []

    for value in row:

        if not value:
            continue

        # 日付＋着順を探す
        pattern = (
            r"(\d{1,2}/\s*\d{1,2})"
            r"\s+"
            r"([^0-9]+?)"
            r"\s+"
            r"([1-9]|落|失|欠|棄)"
            r"(?:着)?"
            r"(?:\s+([0-9]+\.[0-9]+))?"
        )

        matches = re.findall(
            pattern,
            value
        )

        for match in matches:

            date = (
                match[0]
                .replace(" ", "")
                .replace("　", "")
            )

            race_name = (
                match[1]
                .strip()
            )

            result = match[2]

            time_value = match[3]

            recent.append({
                "date": date,
                "race": race_name,
                "result": result,
                "time": time_value or "",
            })

    return recent

def parse_rider_row(row):

    if not row:
        return None

    values = [
        str(x).strip()
        for x in row
        if x is not None
    ]

    # 空行除外
    values = [
        x for x in values
        if x != ""
    ]

    if not values:
        return None

    # 誘導員除外
    joined = " ".join(values)

    if "誘導" in joined:
        return None

    # -------------------------
    # 車番
    # -------------------------
    car = None
    car_index = None

    for i, value in enumerate(values):

        if re.fullmatch(
            r"[1-9]",
            value
        ):

            car = int(value)
            car_index = i
            break

    if car is None:
        return None

    # -------------------------
    # 期別
    # -------------------------
    period = ""
    period_index = None

    for i in range(
        car_index + 1,
        len(values)
    ):

        value = values[i]

        if re.fullmatch(
            r"\d{2,3}",
            value
        ):

            period = value
            period_index = i
            break

    if period_index is None:
        return None

    # -------------------------
    # 選手名
    # -------------------------
    name_parts = []

    for i in range(
        car_index + 1,
        period_index
    ):

        value = values[i].strip()

        if value:
            name_parts.append(value)

    name = " ".join(
        name_parts
    ).strip()

    if not name:
        return None

    # -------------------------
    # 府県
    # -------------------------
    prefecture = ""

    if period_index + 1 < len(values):

        prefecture = (
            values[
                period_index + 1
            ].strip()
        )

    # -------------------------
    # 年齢
    # -------------------------
    age = None

    age_match = re.search(
        r"(\d{2})歳",
        joined
    )

    if age_match:

        try:
            age = int(
                age_match.group(1)
            )
        except Exception:
            age = None

    # -------------------------
    # 級班
    # -------------------------
    rank = ""

    for candidate in [
        "Ｓ級Ｓ班",
        "Ｓ級１班",
        "Ｓ級２班",
        "Ａ級１班",
        "Ａ級２班",
        "Ａ級３班",
        "Ｌ級１班",
        "Ｓ級",
        "Ａ級",
        "Ｌ級",
    ]:

        if candidate in joined:

            rank = candidate
            break

    # -------------------------
    # 脚質
    # -------------------------
    style = ""

    for candidate in [
        "自在",
        "逃捲",
        "追込",
        "逃",
        "捲",
        "追",
        "両",
    ]:

        if candidate in joined:

            style = candidate
            break

    # -------------------------
    # 競走得点
    # -------------------------
    score = None

    score_match = re.search(
        r"競走得点\s*[:：]?\s*"
        r"([0-9]+\.[0-9]+)",
        joined
    )

    if score_match:

        try:
            score = float(
                score_match.group(1)
            )
        except Exception:
            score = None

    # -------------------------
    # ギア倍数
    # -------------------------
    gear = None

    gear_match = re.search(
        r"(?:ギヤ|ギア|ギヤ倍数|ギア倍数)"
        r"\s*[:：]?\s*"
        r"([0-9]+\.[0-9]+)",
        joined
    )

    if gear_match:

        try:
            gear = float(
                gear_match.group(1)
            )
        except Exception:
            gear = None

    # -------------------------
    # 直近成績
    # -------------------------
    recent_record = {
        "first": 0,
        "second": 0,
        "third": 0,
        "other": 0,
    }

    record_match = re.search(
        r"着順\s*[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        joined
    )

    if record_match:

        recent_record = {
            "first": int(
                record_match.group(1)
            ),
            "second": int(
                record_match.group(2)
            ),
            "third": int(
                record_match.group(3)
            ),
            "other": int(
                record_match.group(4)
            ),
        }

    # -------------------------
    # 決まり手
    # -------------------------
    tactics = {
        "escape": 0,
        "maki": 0,
        "difference": 0,
        "mark": 0,
    }

    tactics_match = re.search(
        r"決まり手\s*[:：]?\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)\s*[-－]\s*"
        r"(\d+)",
        joined
    )

    if tactics_match:

        tactics = {
            "escape": int(
                tactics_match.group(1)
            ),
            "maki": int(
                tactics_match.group(2)
            ),
            "difference": int(
                tactics_match.group(3)
            ),
            "mark": int(
                tactics_match.group(4)
            ),
        }

    # -------------------------
    # AIスコア
    # -------------------------
    ai_score = calculate_rider_score(
        score,
        recent_record,
        tactics,
        style,
    )

    return {
        "car": car,
        "name": name,
        "age": age,
        "period": period,
        "prefecture": prefecture,
        "rank": rank,
        "gear": gear,
        "style": style,
        "score": score,
        "recent_record": recent_record,
        "tactics": tactics,
        "current_place": "",
        "previous_place": "",
        "previous_previous_place": "",
        "recent_races": [],
        "ai_score": ai_score,
    }
def parse_riders(html):

    parser = TableParser()

    parser.feed(html)

    riders_by_race = []

    for table in parser.tables:

        if not table:
            continue

        header_index = -1

        for i, row in enumerate(table):

            joined = " ".join(row)

            if (
                "車番" in joined
                and "選手名" in joined
                and "期別" in joined
            ):

                header_index = i
                break

        if header_index < 0:
            continue

        race_riders = []

        for row in table[
            header_index + 1:
        ]:

            rider = parse_rider_row(
                row
            )

            if rider is None:
                continue

            duplicate = False

            for existing in race_riders:

                if (
                    existing["car"]
                    == rider["car"]
                ):

                    duplicate = True
                    break

            if duplicate:
                continue

            race_riders.append(
                rider
            )

        race_riders.sort(
            key=lambda x: x["car"]
        )

        if race_riders:

            riders_by_race.append(
                race_riders
            )

    return riders_by_race


def generate_ai_prediction(
    riders
):

    valid = [
        r for r in riders
        if r.get("ai_score") is not None
    ]

    if len(valid) < 3:
        return None

    ranked = sorted(
        valid,
        key=lambda x: x["ai_score"],
        reverse=True,
    )

    # 上位候補
    candidates = ranked[:6]

    combinations = []

    for combo in permutations(
        candidates,
        3
    ):

        first = combo[0]
        second = combo[1]
        third = combo[2]

        score = (
            first["ai_score"] * 0.50
            + second["ai_score"] * 0.30
            + third["ai_score"] * 0.20
        )

        combinations.append({
            "cars": [
                first["car"],
                second["car"],
                third["car"],
            ],
            "score": round(
                score,
                3
            ),
        })

    combinations.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    top = combinations[:5]

    # 1位の信頼度
    if len(top) >= 2:

        difference = (
            top[0]["score"]
            - top[1]["score"]
        )

        confidence = min(
            99,
            max(
                50,
                int(
                    70
                    + difference * 10
                )
            )
        )

    else:

        confidence = 60

    return {
        "main": top[0],
        "candidates": top,
        "confidence": confidence,
        "method": (
            "競走得点・直近4ヶ月成績・"
            "決まり手・脚質を使用した"
            "データ分析"
        ),
    }


def get_all_race_url(
    code,
    target_date
):

    return (
        f"{BASE_URL}"
        f"AllRaceList.do"
        f"?joCode={code}"
        f"&kaisaiBi={target_date}"
    )


def get_venue_data(
    venue,
    target_date
):

    name = venue["name"]
    code = venue["code"]

    url = get_all_race_url(
        code,
        target_date
    )

    print("")
    print("開催場:", name)
    print("出走表:", url)

    try:

        html = fetch(
            url,
            timeout=30,
            retries=2,
        )

    except Exception as e:

        print(
            "出走表取得失敗:",
            name,
            str(e)
        )

        return {
            "name": name,
            "code": code,
            "races": [],
            "source": url,
            "status": "error",
        }

    race_info = find_race_info(
        html
    )

    rider_tables = parse_riders(
        html
    )

    print(
        "検出レース:",
        len(race_info)
    )

    print(
        "選手テーブル:",
        len(rider_tables)
    )

    races = []

    for index, info in enumerate(
        race_info
    ):

        riders = []

        if index < len(rider_tables):

            riders = rider_tables[
                index
            ]

        prediction = (
            generate_ai_prediction(
                riders
            )
        )

        if len(riders) < 5:

            print(
                "  ⚠️ 選手データ不足"
            )

        elif len(riders) < 7:

            print(
                "  ⚠️ 7車未満:",
                len(riders),
                "人"
            )

        races.append({
            "number": info["number"],
            "start_time": info[
                "start_time"
            ],
            "class": info.get(
                "class",
                ""
            ),
            "riders": riders,
            "prediction": prediction,
            "status": "scheduled",
        })

        print(
            f"  {info['number']}R "
            f"{info['start_time']} "
            f"選手: {len(riders)}"
        )

    return {
        "name": name,
        "code": code,
        "races": races,
        "source": url,
        "status": "ok",
    }


def save(data):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("")
    print(
        "保存完了:",
        OUTPUT_FILE
    )


def main():

    now = datetime.now(
        JST
    )

    target_date = now.strftime(
        "%Y%m%d"
    )

    display_date = now.strftime(
        "%Y-%m-%d"
    )

    print("")
    print(
        "=============================="
    )
    print(
        " KEIRIN AI DATA UPDATE"
    )
    print(
        "=============================="
    )
    print(
        "対象日:",
        display_date
    )
    print(
        "=============================="
    )

    race_list_url = (
        f"{BASE_URL}"
        f"RaceListInfo.do"
        f"?kaisaiBi={target_date}"
    )

    html = fetch(
        race_list_url,
        timeout=30,
        retries=3,
    )

    venues = find_today_venues(
        html
    )

    print("")
    print(
        "検出した開催場:"
    )

    for venue in venues:

        print(
            "-",
            venue["name"]
        )

    if not venues:

        raise RuntimeError(
            "今日の開催場を検出できませんでした。"
        )

    venue_data = []

    for venue in venues:

        data = get_venue_data(
            venue,
            target_date
        )

        venue_data.append(
            data
        )

        time.sleep(1)

    race_count = 0
    rider_count = 0

    for venue in venue_data:

        for race in venue["races"]:

            race_count += 1

            rider_count += len(
                race["riders"]
            )

    result = {
        "updated_at": now.isoformat(),
        "source": "OddsPark",
        "date": display_date,
        "status": "ok",
        "venue_count": len(
            venue_data
        ),
        "race_count": race_count,
        "entry_count": rider_count,
        "venues": venue_data,
    }

    save(result)

    print("")
    print(
        "=============================="
    )
    print(
        " UPDATE COMPLETE"
    )
    print(
        "=============================="
    )
    print(
        "開催場:",
        len(venue_data)
    )
    print(
        "レース:",
        race_count
    )
    print(
        "選手:",
        rider_count
    )
    print(
        "=============================="
    )


if __name__ == "__main__":
    main()
