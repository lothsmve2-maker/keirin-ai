import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path


# ============================================
# KEIRIN AI - 無料データ取得
# ============================================

JST = timezone(timedelta(hours=9))

BASE_URL = "https://www.oddspark.com/keirin/"

OUTPUT_FILE = Path("data/today.json")


# --------------------------------------------
# HTMLからテキストを取り出す
# --------------------------------------------

class TextParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()

        if text:
            self.parts.append(text)

    def get_text(self):
        return "\n".join(self.parts)


# --------------------------------------------
# Webページ取得
# --------------------------------------------

def fetch(url):

    print("取得:", url)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            )
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:

        charset = response.headers.get_content_charset()

        if not charset:
            charset = "utf-8"

        return response.read().decode(
            charset,
            errors="ignore"
        )


# --------------------------------------------
# HTMLをテキスト化
# --------------------------------------------

def html_to_text(html):

    parser = TextParser()

    parser.feed(html)

    return parser.get_text()


# --------------------------------------------
# 開催場候補
# --------------------------------------------

KEIRIN_VENUES = [
    "函館",
    "青森",
    "いわき平",
    "弥彦",
    "前橋",
    "取手",
    "宇都宮",
    "大宮",
    "西武園",
    "京王閣",
    "立川",
    "松戸",
    "川崎",
    "平塚",
    "小田原",
    "伊東",
    "静岡",
    "名古屋",
    "岐阜",
    "大垣",
    "豊橋",
    "富山",
    "松阪",
    "四日市",
    "福井",
    "奈良",
    "向日町",
    "和歌山",
    "岸和田",
    "玉野",
    "広島",
    "防府",
    "高松",
    "小松島",
    "高知",
    "松山",
    "小倉",
    "久留米",
    "武雄",
    "佐世保",
    "別府",
    "熊本"
]


# --------------------------------------------
# 本日の開催場を抽出
# --------------------------------------------

def find_venues(text):

    venues = []

    for venue in KEIRIN_VENUES:

        if venue in text:

            if venue not in venues:

                venues.append(venue)

    return venues


# --------------------------------------------
# レース番号を抽出
# --------------------------------------------

def find_race_numbers(text):

    races = set()

    patterns = [
        r"(\d{1,2})レース",
        r"(\d{1,2})R",
        r"(\d{1,2})Ｒ"
    ]

    for pattern in patterns:

        for match in re.findall(pattern, text):

            number = int(match)

            if 1 <= number <= 12:

                races.add(number)

    return sorted(races)


# --------------------------------------------
# データ保存
# --------------------------------------------

def save_json(data):

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

    print(
        "保存完了:",
        OUTPUT_FILE
    )


# --------------------------------------------
# メイン処理
# --------------------------------------------

def main():

    now = datetime.now(JST)

    print("")
    print("================================")
    print(" KEIRIN AI DATA UPDATE")
    print("================================")
    print(
        "日時:",
        now.strftime("%Y-%m-%d %H:%M:%S")
    )

    try:

        html = fetch(BASE_URL)

    except Exception as e:

        print("データ取得失敗:", e)

        # GitHub Actionsを落とさず終了
        return

    text = html_to_text(html)

    venues = find_venues(text)

    races = find_race_numbers(text)

    data = {

        "updated_at":
            now.isoformat(),

        "source":
            "OddsPark",

        "date":
            now.strftime("%Y-%m-%d"),

        "venues":
            venues,

        "races":
            races,

        "status":
            "partial",

        "message":
            "開催情報取得済み。出走表詳細取得処理を追加予定。"

    }

    save_json(data)

    print("")
    print("開催場:", venues)
    print("レース:", races)
    print("")
    print("更新完了")


if __name__ == "__main__":

    main()
