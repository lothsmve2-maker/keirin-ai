import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUTPUT_FILE = Path("data/today.json")

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


def fetch(url, timeout=10, retries=3):
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
                    "Accept": "text/html,application/xhtml+xml",
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

                return response.read().decode(
                    charset,
                    errors="ignore",
                )

        except Exception as e:
            print("取得失敗:", str(e))

            if attempt < retries:
                time.sleep(2)

    raise RuntimeError(
        f"ページ取得に失敗しました: {url}"
    )


def clean_html(html):
    html = re.sub(
        r"<script.*?</script>",
        " ",
        html,
        flags=re.S | re.I,
    )

    html = re.sub(
        r"<style.*?</style>",
        " ",
        html,
        flags=re.S | re.I,
    )

    html = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    html = re.sub(
        r"\s+",
        " ",
        html,
    )

    return html.strip()


def find_today_venues(html):
    text = clean_html(html)

    venues = []

    for name, code in VENUE_CODES.items():

        if name in text:
            venues.append(
                {
                    "name": name,
                    "code": code,
                }
            )

    return venues


def find_races(html):
    text = clean_html(html)

    numbers = set()

    patterns = [
        r"([1-9]|1[0-2])R",
        r"([1-9]|1[0-2]) レース",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
        ):

            number = int(
                match.group(1)
            )

            if 1 <= number <= 12:
                numbers.add(number)

    return sorted(numbers)


def get_venue_program(
    venue,
    date_string,
):
    name = venue["name"]
    code = venue["code"]

    url = (
        "https://www.oddspark.com/keirin/"
        "RaceProgram.do"
        f"?joCode={code}"
        f"&shonichi={date_string}"
    )

    try:
        html = fetch(
            url,
            timeout=8,
            retries=2,
        )

    except Exception as e:

        print(
            "プログラム取得失敗:",
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

    race_numbers = find_races(html)

    races = []

    for number in race_numbers:

        races.append(
            {
                "number": number,
                "start_time": "",
                "status": "scheduled",
            }
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

    print(
        "保存完了:",
        OUTPUT_FILE,
    )


def main():

    now = datetime.now(JST)

    date_string = now.strftime(
        "%Y%m%d"
    )

    date_display = now.strftime(
        "%Y-%m-%d"
    )

    print("")
    print("==============================")
    print(" KEIRIN AI DATA UPDATE")
    print("==============================")
    print("対象日:", date_display)
    print("==============================")

    # 日付を指定して開催場一覧を取得
    sale_place_url = (
        "https://www.oddspark.com/keirin/"
        "RaceListInfo.do"
        f"?kaisaiBi={date_string}"
    )

    try:

        html = fetch(
            sale_place_url,
            timeout=10,
            retries=3,
        )

    except Exception as e:

        print("")
        print("開催場取得に失敗しました。")
        print(str(e))
        print("")

        raise

    venues = find_today_venues(
        html
    )

    print("")
    print("検出した開催場:")

    for venue in venues:
        print(
            "-",
            venue["name"],
        )

    if not venues:

        raise RuntimeError(
            "開催場を1つも検出できませんでした。"
        )

    venue_data = []

    for venue in venues:

        print("")
        print(
            "開催場処理:",
            venue["name"],
        )

        data = get_venue_program(
            venue,
            date_string,
        )

        venue_data.append(
            data
        )

    race_count = 0

    for venue in venue_data:

        race_count += len(
            venue["races"]
        )

    result = {
        "updated_at": now.isoformat(),
        "source": "OddsPark",
        "date": date_display,
        "status": "ok",
        "venue_count": len(
            venue_data
        ),
        "race_count": race_count,
        "entry_count": 0,
        "venues": venue_data,
    }

    save(result)

    print("")
    print("==============================")
    print(" UPDATE COMPLETE")
    print("==============================")
    print(
        "開催場:",
        len(venue_data),
    )
    print(
        "レース:",
        race_count,
    )
    print("==============================")


if __name__ == "__main__":
    main()
