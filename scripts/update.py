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
# ============================================================

JST = timezone(timedelta(hours=9))

OUTPUT_FILE = Path("data/today.json")

BASE_URL = "https://www.oddspark.com/keirin/"


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
# HTTP取得
# ============================================================

def fetch(url, timeout=30, retries=3):

    for attempt in range(1, retries + 1):

        try:

            print(
                f"GET {attempt}/{retries}: {url}"
            )

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
                    "Accept-Language": (
                        "ja-JP,ja;q=0.9"
                    ),
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

            print(
                "取得失敗:",
                str(e)
            )

            if attempt < retries:

                time.sleep(3)

    raise RuntimeError(
        f"ページ取得に失敗しました: {url}"
    )


# ============================================================
# HTML Table Parser
# ============================================================

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

    def handle_starttag(
        self,
        tag,
        attrs
    ):

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
                text
            ).strip()

            self.current_row.append(
                text
            )

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


# ============================================================
# テキスト整形
# ============================================================

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


# ============================================================
# 今日の開催場
# ============================================================

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


# ============================================================
# レース情報
# ============================================================

def find_race_info(html):

    text = clean_text(html)

    matches = list(
        re.finditer(
            r"第\s*(1[0-2]|[1-9])R",
            text
        )
    )

    races = []

    for i, match in enumerate(matches):

        number = int(
            match.group(1)
        )

        start = match.start()

        if i + 1 < len(matches):

            end = matches[
                i + 1
            ].start()

        else:

            end = len(text)

        section = text[
            start:end
        ]

        start_time = ""

        time_match = re.search(
            r"発走時間\s*([0-9]{1,2}:[0-9]{2})",
            section
        )

        if time_match:

            start_time = (
                time_match.group(1)
            )

        races.append({
            "number": number,
            "start_time": start_time,
        })

    return races


# ============================================================
# 基本選手データ
#
# ここは「500人取得できていた正常版」と同じ方式。
# 絶対に壊さない。
# ============================================================

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
                and "府県" in joined
            ):

                header_index = i

                break

        if header_index < 0:
            continue

        race_riders = []

        for row in table[
            header_index + 1:
        ]:

            if not row:
                continue

            car_no = None
            car_index = None

            for i, value in enumerate(row):

                value = value.strip()

                if re.fullmatch(
                    r"[1-9]",
                    value
                ):

                    car_no = int(value)

                    car_index = i

                    break

            if car_no is None:
                continue

            period_index = None
            period = ""

            for i in range(
                car_index + 1,
                len(row)
            ):

                value = row[i].strip()

                if re.fullmatch(
                    r"\d{2,3}",
                    value
                ):

                    period = value

                    period_index = i

                    break

            if period_index is None:
                continue

            name_parts = []

            for i in range(
                car_index + 1,
                period_index
            ):

                value = row[i].strip()

                if value:

                    name_parts.append(
                        value
                    )

            name = " ".join(
                name_parts
            ).strip()

            if not name:
                continue

            prefecture = ""

            if (
                period_index + 1
                < len(row)
            ):

                prefecture = (
                    row[
                        period_index + 1
                    ].strip()
                )

            if name in (
                "誘導",
                "誘導員"
            ):

                continue

            duplicate = False

            for existing in race_riders:

                if (
                    existing["car"]
                    == car_no
                ):

                    duplicate = True

                    break

            if duplicate:
                continue

            race_riders.append({
                "car": car_no,
                "name": name,
                "period": period,
                "prefecture": prefecture,
            })

        race_riders.sort(
            key=lambda x: x["car"]
        )

        if race_riders:

            riders_by_race.append(
                race_riders
            )

    return riders_by_race


# ============================================================
# 個別レースURL
# ============================================================

def get_race_url(
    code,
    target_date,
    race_no
):

    return (
        f"{BASE_URL}"
        f"RaceList.do"
        f"?joCode={code}"
        f"&kaisaiBi={target_date}"
        f"&raceNo={race_no}"
    )


# ============================================================
# 数値変換
# ============================================================

def to_float(value):

    if value is None:
        return None

    value = str(value)

    value = value.replace(
        ",",
        ""
    )

    match = re.search(
        r"\d+(?:\.\d+)?",
        value
    )

    if not match:
        return None

    try:

        return float(
            match.group(0)
        )

    except Exception:

        return None


# ============================================================
# 個別レースの選手詳細を取得
# ============================================================

def parse_detailed_riders(html):

    parser = TableParser()

    parser.feed(html)

    for table in parser.tables:

        if not table:
            continue

        header_index = -1

        header = []

        for i, row in enumerate(table):

            joined = " ".join(row)

            if (
                "車番" in joined
                and "選手名" in joined
                and "競走得点" in joined
            ):

                header_index = i
                header = row

                break

        if header_index < 0:
            continue

        # ----------------------------------------------------
        # ヘッダー位置を特定
        # ----------------------------------------------------

        def find_header(*names):

            for index, value in enumerate(
                header
            ):

                for name in names:

                    if name in value:

                        return index

            return None

        car_index = find_header(
            "車番"
        )

        name_index = find_header(
            "選手名"
        )

        age_period_index = find_header(
            "年齢/期別",
            "年齢",
            "期別"
        )

        prefecture_index = find_header(
            "府県"
        )

        class_index = find_header(
            "級班"
        )

        gear_index = find_header(
            "ギア倍数"
        )

        style_index = find_header(
            "脚質"
        )

        score_index = find_header(
            "競走得点"
        )

        recent_index = find_header(
            "直近4ヶ月の成績"
        )

        decision_index = find_header(
            "決まり手"
        )

        current_index = find_header(
            "今場所成績"
        )

        previous_index = find_header(
            "前場所成績"
        )

        previous2_index = find_header(
            "前々場所成績"
        )

        # ----------------------------------------------------
        # 行解析
        # ----------------------------------------------------

        detailed = []

        for row in table[
            header_index + 1:
        ]:

            if not row:
                continue

            # 車番
            car_no = None

            if (
                car_index is not None
                and car_index < len(row)
            ):

                match = re.search(
                    r"[1-9]",
                    row[car_index]
                )

                if match:

                    car_no = int(
                        match.group(0)
                    )

            # 車番がヘッダー位置で取れない場合
            if car_no is None:

                for value in row:

                    if re.fullmatch(
                        r"[1-9]",
                        value.strip()
                    ):

                        car_no = int(
                            value.strip()
                        )

                        break

            if car_no is None:
                continue

            # ------------------------------------------------
            # 基本値
            # ------------------------------------------------

            name = ""

            if (
                name_index is not None
                and name_index < len(row)
            ):

                name = row[
                    name_index
                ].strip()

            age_period = ""

            if (
                age_period_index is not None
                and age_period_index < len(row)
            ):

                age_period = row[
                    age_period_index
                ].strip()

            prefecture = ""

            if (
                prefecture_index is not None
                and prefecture_index < len(row)
            ):

                prefecture = row[
                    prefecture_index
                ].strip()

            rider_class = ""

            if (
                class_index is not None
                and class_index < len(row)
            ):

                rider_class = row[
                    class_index
                ].strip()

            gear = ""

            if (
                gear_index is not None
                and gear_index < len(row)
            ):

                gear = row[
                    gear_index
                ].strip()

            style = ""

            if (
                style_index is not None
                and style_index < len(row)
            ):

                style = row[
                    style_index
                ].strip()

            score_text = ""

            if (
                score_index is not None
                and score_index < len(row)
            ):

                score_text = row[
                    score_index
                ].strip()

            recent = ""

            if (
                recent_index is not None
                and recent_index < len(row)
            ):

                recent = row[
                    recent_index
                ].strip()

            decision = ""

            if (
                decision_index is not None
                and decision_index < len(row)
            ):

                decision = row[
                    decision_index
                ].strip()

            current = ""

            if (
                current_index is not None
                and current_index < len(row)
            ):

                current = row[
                    current_index
                ].strip()

            previous = ""

            if (
                previous_index is not None
                and previous_index < len(row)
            ):

                previous = row[
                    previous_index
                ].strip()

            previous2 = ""

            if (
                previous2_index is not None
                and previous2_index < len(row)
            ):

                previous2 = row[
                    previous2_index
                ].strip()

            # ------------------------------------------------
            # 年齢
            # ------------------------------------------------

            age = None

            age_match = re.search(
                r"(\d{2})\s*歳",
                age_period
            )

            if age_match:

                age = int(
                    age_match.group(1)
                )

            # ------------------------------------------------
            # 期別
            # ------------------------------------------------

            period = ""

            period_match = re.search(
                r"/\s*(\d{2,3})\s*期",
                age_period
            )

            if period_match:

                period = period_match.group(1)

            else:

                period_match = re.search(
                    r"(\d{2,3})\s*期",
                    age_period
                )

                if period_match:

                    period = (
                        period_match.group(1)
                    )

            # ------------------------------------------------
            # 競走得点
            # ------------------------------------------------

            score = to_float(
                score_text
            )

            # ------------------------------------------------
            # ギア
            # ------------------------------------------------

            gear_value = to_float(
                gear
            )

            # ------------------------------------------------
            # 決まり手を数値化
            #
            # 逃-捲-差-マ
            # ------------------------------------------------

            escape = 0
            makuri = 0
            sashi = 0
            mark = 0

            decision_numbers = re.findall(
                r"\d+",
                decision
            )

            if len(decision_numbers) >= 4:

                escape = int(
                    decision_numbers[0]
                )

                makuri = int(
                    decision_numbers[1]
                )

                sashi = int(
                    decision_numbers[2]
                )

                mark = int(
                    decision_numbers[3]
                )

            detailed.append({
                "car": car_no,
                "name_detail": name,
                "age": age,
                "period_detail": period,
                "prefecture_detail": prefecture,
                "class": rider_class,
                "gear": gear,
                "gear_value": gear_value,
                "style": style,
                "score": score,
                "recent_4m": recent,
                "decision": decision,
                "escape": escape,
                "makuri": makuri,
                "sashi": sashi,
                "mark": mark,
                "current_race": current,
                "previous_race": previous,
                "previous2_race": previous2,
            })

        if detailed:

            return detailed

    return []


# ============================================================
# 詳細データを基本選手データに結合
# ============================================================

def merge_rider_details(
    riders,
    detailed
):

    detail_map = {}

    for item in detailed:

        detail_map[
            item["car"]
        ] = item

    result = []

    for rider in riders:

        car = rider["car"]

        item = detail_map.get(
            car
        )

        merged = dict(
            rider
        )

        # 詳細データが存在する場合
        if item:

            if item.get(
                "name_detail"
            ):

                merged[
                    "name"
                ] = rider["name"]

            if item.get(
                "period_detail"
            ):

                merged[
                    "period"
                ] = item[
                    "period_detail"
                ]

            if item.get(
                "prefecture_detail"
            ):

                merged[
                    "prefecture"
                ] = item[
                    "prefecture_detail"
                ]

            merged[
                "age"
            ] = item.get(
                "age"
            )

            merged[
                "class"
            ] = item.get(
                "class",
                ""
            )

            merged[
                "gear"
            ] = item.get(
                "gear",
                ""
            )

            merged[
                "gear_value"
            ] = item.get(
                "gear_value"
            )

            merged[
                "style"
            ] = item.get(
                "style",
                ""
            )

            merged[
                "score"
            ] = item.get(
                "score"
            )

            merged[
                "recent_4m"
            ] = item.get(
                "recent_4m",
                ""
            )

            merged[
                "decision"
            ] = item.get(
                "decision",
                ""
            )

            merged[
                "escape"
            ] = item.get(
                "escape",
                0
            )

            merged[
                "makuri"
            ] = item.get(
                "makuri",
                0
            )

            merged[
                "sashi"
            ] = item.get(
                "sashi",
                0
            )

            merged[
                "mark"
            ] = item.get(
                "mark",
                0
            )

            merged[
                "current_race"
            ] = item.get(
                "current_race",
                ""
            )

            merged[
                "previous_race"
            ] = item.get(
                "previous_race",
                ""
            )

            merged[
                "previous2_race"
            ] = item.get(
                "previous2_race",
                ""
            )

            merged[
                "detail_status"
            ] = "ok"

        else:

            merged[
                "detail_status"
            ] = "unavailable"

        result.append(
            merged
        )

    return result


# ============================================================
# 個別レース詳細取得
#
# 1レース失敗しても全体を止めない。
# ============================================================

def get_race_details(
    code,
    target_date,
    race_no,
    riders
):

    url = get_race_url(
        code,
        target_date,
        race_no
    )

    try:

        html = fetch(
            url,
            timeout=30,
            retries=2,
        )

        detailed = parse_detailed_riders(
            html
        )

        if not detailed:

            print(
                "  ⚠️ 詳細選手データなし"
            )

            for rider in riders:

                rider[
                    "detail_status"
                ] = "unavailable"

            return riders

        merged = merge_rider_details(
            riders,
            detailed
        )

        success_count = sum(
            1
            for rider in merged
            if rider.get(
                "detail_status"
            ) == "ok"
        )

        print(
            "  詳細選手データ:",
            success_count,
            "/",
            len(riders)
        )

        return merged

    except Exception as e:

        print(
            "  ⚠️ 詳細取得失敗:",
            str(e)
        )

        for rider in riders:

            rider[
                "detail_status"
            ] = "error"

        return riders


# ============================================================
# 全レース一覧URL
# ============================================================

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


# ============================================================
# 開催場データ
# ============================================================

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
    print(
        "開催場:",
        name
    )

    print(
        "出走表:",
        url
    )

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

    # --------------------------------------------------------
    # ここは正常版と同じ
    # --------------------------------------------------------

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

        if index < len(
            rider_tables
        ):

            riders = rider_tables[
                index
            ]

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

        print(
            f"  {info['number']}R "
            f"{info['start_time']} "
            f"選手: {len(riders)}"
        )

        # ----------------------------------------------------
        # 個別レース詳細
        # ----------------------------------------------------

        if riders:

            riders = get_race_details(
                code,
                target_date,
                info["number"],
                riders
            )

        races.append({
            "number": info["number"],
            "start_time": info["start_time"],
            "riders": riders,
            "status": "scheduled",
        })

        # サーバーへの負荷を抑える
        time.sleep(0.5)

    return {
        "name": name,
        "code": code,
        "races": races,
        "source": url,
        "status": "ok",
    }


# ============================================================
# 保存
# ============================================================

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


# ============================================================
# メイン
# ============================================================

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

    # --------------------------------------------------------
    # 今日の開催一覧
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 開催場処理
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 集計
    # --------------------------------------------------------

    race_count = 0
    rider_count = 0
    detail_success = 0

    for venue in venue_data:

        for race in venue[
            "races"
        ]:

            race_count += 1

            for rider in race[
                "riders"
            ]:

                rider_count += 1

                if rider.get(
                    "detail_status"
                ) == "ok":

                    detail_success += 1

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

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

        "detail_entry_count": (
            detail_success
        ),

        "venues": venue_data,
    }

    save(result)

    # --------------------------------------------------------
    # 完了表示
    # --------------------------------------------------------

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
        "詳細取得:",
        detail_success
    )

    print(
        "=============================="
    )


if __name__ == "__main__":

    main()
