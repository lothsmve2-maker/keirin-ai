import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import unescape
from html.parser import HTMLParser


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


# ==========================================
# HTTP取得
# ==========================================

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


# ==========================================
# HTMLテーブル解析
# ==========================================

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
                text
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


# ==========================================
# 今日の開催場
# ==========================================

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


# ==========================================
# レース番号・発走時間
# ==========================================

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


# ==========================================
# 選手表を解析
# ==========================================

def parse_riders(html):

    parser = TableParser()

    parser.feed(html)

    riders_by_race = []

    # ページ内の全テーブルを調査
    for table in parser.tables:

        if not table:
            continue

        # 「車番」「選手名」「期別」「府県」
        # を持つテーブルを探す
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

            if len(row) < 5:
                continue

            # 通常
            # 枠 / 車番 / 選手名 / 期別 / 府県
            try:

                car_no = int(
                    row[1]
                )

            except:

                continue

            name = row[2].strip()

            period = row[3].strip()

            prefecture = row[4].strip()

            if not name:
                continue

            if not re.fullmatch(
                r"\d+",
                str(period)
            ):
                continue

            race_riders.append({
                "car": car_no,
                "name": name,
                "period": period,
                "prefecture": prefecture,
            })

        if race_riders:

            riders_by_race.append(
                race_riders
            )

    return riders_by_race


# ==========================================
# 出走表URL
# ==========================================

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


# ==========================================
# 開催場データ
# ==========================================

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

    # ----------------------------------
    # レース情報
    # ----------------------------------

    race_info = find_race_info(
        html
    )

    # ----------------------------------
    # 選手情報
    # ----------------------------------

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

        races.append({
            "number": info["number"],
            "start_time": info[
                "start_time"
            ],
            "riders": riders,
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


# ==========================================
# 保存
# ==========================================

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


# ==========================================
# メイン
# ==========================================

def main():

    now = datetime.now(JST)

    target_date = now.strftime(
        "%Y%m%d"
    )

    display_date = now.strftime(
        "%Y-%m-%d"
    )

    print("")
    print("==============================")
    print(" KEIRIN AI DATA UPDATE")
    print("==============================")
    print(
        "対象日:",
        display_date
    )
    print("==============================")

    # ----------------------------------
    # 1. 今日の開催一覧
    # ----------------------------------

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

    # ----------------------------------
    # 2. 各開催
    # ----------------------------------

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

    # ----------------------------------
    # 3. 集計
    # ----------------------------------

    race_count = 0
    rider_count = 0

    for venue in venue_data:

        for race in venue["races"]:

            race_count += 1

            rider_count += len(
                race["riders"]
            )

    # ----------------------------------
    # 4. 保存
    # ----------------------------------

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
    print("==============================")
    print(" UPDATE COMPLETE")
    print("==============================")
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
    print("==============================")


if __name__ == "__main__":
    main()
