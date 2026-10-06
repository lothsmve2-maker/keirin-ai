import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUTPUT_FILE = Path("data/today.json")

SALE_PLACE_URL = (
    "https://sp.oddspark.com/keirin/"
    "SpSalePlaceList.do"
    "?gamenId=S101"
    "&gamenKoumokuId=sideMenuKeirinSale"
)

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


def fetch(url, timeout=8):
    print("GET:", url)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "ja-JP,ja;q=0.9",
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


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


def find_venues(html):
    """
    ページ内に存在する競輪場名を探す。
    同じ競輪場は1回だけ登録する。
    """

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
    """
    プログラムページから1R〜12Rを探す。
    """

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
        )

    except Exception as e:

        print(
            "取得失敗:",
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

    race_numbers = find_races(
        html
    )

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
    print(
        "対象日:",
        date_display,
    )

    # 開催場一覧を取得
    try:

        html = fetch(
            SALE_PLACE_URL,
            timeout=8,
        )

    except Exception as e:

        print(
            "開催場ページ取得失敗:",
            str(e),
        )

        return

    venues = find_venues(
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

        print(
            "開催場が見つかりませんでした。"
        )

        return

    venue_data = []

    for venue in venues:

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
