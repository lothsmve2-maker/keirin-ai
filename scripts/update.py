import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import unescape

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


def clean_text(text):

    text = unescape(text)

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


def find_all_race_urls(html, target_date):

    html = unescape(html)

    pattern = re.compile(
        r'href=["\']([^"\']*AllRaceList\.do\?[^"\']*)["\']',
        re.I
    )

    urls = []

    for match in pattern.finditer(html):

        url = match.group(1)

        url = url.replace("&amp;", "&")

        if f"kaisaiBi={target_date}" not in url:
            continue

        if url.startswith("/"):
            url = "https://www.oddspark.com" + url

        elif url.startswith("http") is False:
            url = BASE_URL + url

        if url not in urls:
            urls.append(url)

    return urls


def parse_races(html):

    text = clean_text(html)

    races = []

    # 「第1R」「第2R」などを検出
    race_matches = list(
        re.finditer(
            r"第\s*(1[0-2]|[1-9])R",
            text
        )
    )

    for i, match in enumerate(race_matches):

        number = int(match.group(1))

        start = match.start()

        if i + 1 < len(race_matches):
            end = race_matches[i + 1].start()
        else:
            end = len(text)

        section = text[start:end]

        # 発走時間
        start_time = ""

        time_match = re.search(
            r"発走時間\s*([0-9]{1,2}:[0-9]{2})",
            section
        )

        if time_match:
            start_time = time_match.group(1)

        # 車番・選手情報
        riders = []

        rider_pattern = re.compile(
            r"([1-9])\s+([^\s]{2,12})\s+([0-9]{2,3})\s+([^\s]{1,5})"
        )

        for rider_match in rider_pattern.finditer(section):

            car_no = int(
                rider_match.group(1)
            )

            name = rider_match.group(2)

            period = rider_match.group(3)

            prefecture = rider_match.group(4)

            riders.append({
                "car": car_no,
                "name": name,
                "period": period,
                "prefecture": prefecture,
            })

        races.append({
            "number": number,
            "start_time": start_time,
            "riders": riders,
            "status": "scheduled",
        })

    return races


def get_venue_data(
    venue,
    target_date,
    race_list_html,
):

    name = venue["name"]
    code = venue["code"]

    all_race_urls = find_all_race_urls(
        race_list_html,
        target_date,
    )

    # 開催場コードで絞る
    venue_urls = []

    for url in all_race_urls:

        if (
            f"joCode={code}" in url
            or f"joCode%3D{code}" in url
        ):
            venue_urls.append(url)

    # URLが見つからなかった場合は直接生成
    if not venue_urls:

        venue_urls = [
            (
                f"{BASE_URL}"
                f"AllRaceList.do"
                f"?joCode={code}"
                f"&kaisaiBi={target_date}"
            )
        ]

    url = venue_urls[0]

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
            str(e),
        )

        return {
            "name": name,
            "code": code,
            "races": [],
            "source": url,
            "status": "error",
        }

    races = parse_races(html)

    print(
        "取得レース数:",
        len(races)
    )

    for race in races:

        print(
            f"  {race['number']}R",
            race["start_time"],
            "選手:",
            len(race["riders"])
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
    print("対象日:", display_date)
    print("==============================")

    # --------------------------------
    # 1. 今日の開催一覧
    # --------------------------------

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
    print("検出した開催場:")

    for venue in venues:
        print(
            "-",
            venue["name"]
        )

    if not venues:

        raise RuntimeError(
            "今日の開催場を検出できませんでした。"
        )

    # --------------------------------
    # 2. 各開催の全レース出走表
    # --------------------------------

    venue_data = []

    for venue in venues:

        data = get_venue_data(
            venue,
            target_date,
            html,
        )

        venue_data.append(
            data
        )

        # サーバーへの連続アクセスを少し避ける
        time.sleep(1)

    # --------------------------------
    # 3. 集計
    # --------------------------------

    race_count = 0
    rider_count = 0

    for venue in venue_data:

        for race in venue["races"]:

            race_count += 1

            rider_count += len(
                race["riders"]
            )

    # --------------------------------
    # 4. 保存
    # --------------------------------

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
