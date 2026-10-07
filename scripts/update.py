import json
import os
import re
import time
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

VERSION = "5.5"
BASE_URL = "https://www.oddspark.com"
SP_BASE_URL = "https://sp.oddspark.com"
TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DISPLAY = datetime.now().strftime("%Y-%m-%d")
TIMEOUT = 15
RETRIES = 1
MAX_WORKERS = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}

VENUES = {
    "22": ("前橋", "maebashi"),
    "25": ("大宮", "omiya"),
    "38": ("静岡", "shizuoka"),
    "44": ("大垣", "ogaki"),
    "48": ("四日市", "yokkaichi"),
    "53": ("奈良", "nara"),
    "74": ("高知", "kochi"),
}

MARK_SCORE = {"◎": 18, "○": 12, "▲": 8, "△": 5, "×": 1, "注": 3}
STYLE_WORDS = {"逃捲", "逃げ", "先捲", "捲り", "捲", "追込", "追捲", "自在", "単騎", "差脚", "地差", "先行"}
PREFECTURES = {
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島", "茨城", "栃木", "群馬", "埼玉", "千葉",
    "東京", "神奈川", "新潟", "富山", "石川", "福井", "山梨", "長野", "岐阜", "静岡", "愛知", "三重",
    "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "山口", "徳島",
    "香川", "愛媛", "高知", "福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄",
    "北海", "福岡", "熊本", "愛知", "静岡", "大阪", "兵庫", "京都", "奈良", "和歌山", "三重",
}
GRADES = {"S1", "S2", "A1", "A2", "A3", "L1", "L2"}


def clean(text):
    if text is None:
        return ""
    text = str(text).replace("\xa0", " ").replace("　", " ")
    return re.sub(r"\s+", " ", text).strip()


def get_html(url):
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and len(r.text) > 300:
                r.encoding = r.apparent_encoding or r.encoding
                return r.text
        except requests.RequestException:
            pass
        if attempt < RETRIES:
            time.sleep(0.4)
    return None


def soup(html):
    if not html:
        return None
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def race_url(code, race_no):
    return f"{BASE_URL}/keirin/RaceList.do?joCode={code}&kaisaiBi={TODAY}&raceNo={race_no}"


def prediction_url(slug, race_no):
    base = f"{SP_BASE_URL}/keirin/yosou/{slug}/{TODAY[:4]}/{TODAY[4:]}"
    return base + (".html" if race_no == 1 else f"_{race_no}.html")


def numbers4(text):
    m = re.search(r"(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)", text)
    return [int(x) for x in m.groups()] if m else [None] * 4


def parse_rider_row(tr):
    text = clean(tr.get_text(" ", strip=True))
    # 選手名はPlayerDetailリンクから取得。これで「◎ ○ ▲...」を名前として誤取得する問題を完全回避。
    name = ""
    for a in tr.find_all("a"):
        href = a.get("href", "")
        label = clean(a.get_text(" ", strip=True))
        if "PlayerDetail.do" in href and label:
            name = label
            break
    if not name:
        return None

    mcar = re.search(r"^\s*(\d+)\s+(\d+)", text)
    car = int(mcar.group(2) if mcar else mcar.group(1)) if mcar else None
    if car is None or not 1 <= car <= 7:
        return None

    age = period = None
    m = re.search(r"(\d{1,2})歳\s*[／/]\s*(\d{2,3})期", text)
    if m:
        age, period = int(m.group(1)), int(m.group(2))

    prefecture = ""
    # 年齢/期別の直後から級班までの短い部分を探す
    if m:
        tail = text[m.end():]
        for p in sorted(PREFECTURES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(p)}\b", tail) or p in tail:
                prefecture = p
                break

    grade = ""
    mg = re.search(r"\b([SAL]\d)\b", text)
    if mg:
        grade = mg.group(1)

    style = ""
    for s in ("逃", "捲", "追", "両"):
        if re.search(rf"\s{s}\s*[|]", text) or re.search(rf"\s{s}\s+競走得点", text):
            style = s
            break
    if not style:
        for s in ("逃", "捲", "追", "両"):
            if re.search(rf"\s{s}\s", text):
                style = s
                break

    score = None
    ms = re.search(r"競走得点\s*[:：]\s*(\d+(?:\.\d+)?)", text)
    if ms:
        score = float(ms.group(1))

    finish = numbers4(re.search(r"着\s*順\s*[:：]\s*([^|]+)", text).group(1) if re.search(r"着\s*順\s*[:：]\s*([^|]+)", text) else "")
    kimari = numbers4(re.search(r"決まり手\s*[:：]\s*([^|]+)", text).group(1) if re.search(r"決まり手\s*[:：]\s*([^|]+)", text) else "")

    return {
        "car_no": car, "name": name, "age": age, "period": period,
        "prefecture": prefecture, "grade": grade, "style": style, "score": score,
        "finish_1": finish[0], "finish_2": finish[1], "finish_3": finish[2], "finish_out": finish[3],
        "kimari_nige": kimari[0], "kimari_makuri": kimari[1], "kimari_sashi": kimari[2], "kimari_mark": kimari[3],
        "comment": "", "prediction_mark": "", "prediction_score": 0, "ai_score": 0,
    }


def parse_riders(sp):
    best = {}
    for tr in sp.find_all("tr"):
        rider = parse_rider_row(tr)
        if rider:
            best[rider["car_no"]] = rider
    if 5 <= len(best) <= 7:
        return [best[k] for k in sorted(best)]
    return []


def fetch_race(job):
    url = race_url(job["venue_code"], job["race_no"])
    sp = soup(get_html(url))
    riders = parse_riders(sp) if sp else []
    return {**job, "url": url, "riders": riders, "success": 5 <= len(riders) <= 7}


def discover_real_races(venue):
    jobs = [{"venue_code": venue["code"], "venue_name": venue["name"], "venue_slug": venue["slug"], "race_no": n} for n in range(1, 13)]
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_race, j) for j in jobs]
        for f in as_completed(futures):
            try:
                r = f.result()
                if r["success"]:
                    results.append(r)
            except Exception:
                pass
    return sorted(results, key=lambda x: x["race_no"])


def parse_prediction_table(sp):
    result = {}
    for table in sp.find_all("table"):
        rows = table.find_all("tr")
        header = next((i for i, tr in enumerate(rows) if all(x in clean(tr.get_text(" ", strip=True)) for x in ("車", "印", "選手名", "コメント"))), None)
        if header is None:
            continue
        for tr in rows[header + 1:]:
            vals = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            car = next((int(v) for v in vals if re.fullmatch(r"[1-7]", v)), None)
            if car is None:
                continue
            mark = next((v for v in vals if v in MARK_SCORE), "")
            name = ""
            for v in vals:
                if v and v != str(car) and v != mark and len(v) >= 2:
                    name = v
                    break
            comment = " ".join(v for v in vals if v and v not in {str(car), mark, name})
            result[car] = {"name": name, "mark": mark, "comment": comment, "prediction_score": MARK_SCORE.get(mark, 0)}
        if 5 <= len(result) <= 7:
            return result
    return result


def parse_line(sp):
    best_cars, best_groups = [], []
    for table in sp.find_all("table"):
        rows = table.find_all("tr")
        for i, tr in enumerate(rows):
            vals = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cars = [int(v) for v in vals if re.fullmatch(r"[1-7]", v)]
            if not 5 <= len(set(cars)) <= 7:
                continue
            cars = list(dict.fromkeys(cars))
            nearby = " ".join(clean(x.get_text(" ", strip=True)) for x in rows[i:i + 3])
            if not best_cars or any(w in nearby for w in STYLE_WORDS):
                groups, cur = [], []
                for v in vals:
                    if re.fullmatch(r"[1-7]", v):
                        cur.append(int(v))
                    elif cur:
                        groups.append(cur); cur = []
                if cur:
                    groups.append(cur)
                flat = [n for g in groups for n in g]
                if set(flat) != set(cars):
                    groups = [cars]
                best_cars, best_groups = cars, groups
    return best_cars, best_groups


def is_development_text(text):
    t = clean(text)
    if not (15 <= len(t) <= 300):
        return False
    if any(x in t for x in ("予想の並び", "並び内の脚質", "免責", "Copyright", "ページ先頭", "投票メニュー")):
        return False
    return bool(re.search(r"[。！？，、]", t)) and any("\u3040" <= ch <= "\u30ff" for ch in t)


def parse_development(sp):
    # オッズパークSPページは「車番行 → 脚質行 → 展開コメント」という構造。
    # まず「←」を含む行/要素を見つけ、その後方だけを探す。
    all_elements = sp.find_all(["tr", "p", "div", "td", "li"])
    for i, el in enumerate(all_elements):
        txt = clean(el.get_text(" ", strip=True))
        if "←" not in txt:
            continue
        # 同じ親内の後続要素を優先
        for nxt in all_elements[i + 1:i + 12]:
            cand = clean(nxt.get_text(" ", strip=True))
            if not cand or cand == txt or "←" in cand:
                continue
            if any(w in cand for w in ("1R出走表", "2R出走表", "3R出走表", "4R出走表", "5R出走表", "6R出走表", "7R出走表", "8R出走表", "9R出走表", "10R出走表", "11R出走表", "12R出走表")):
                break
            if is_development_text(cand):
                return cand
    # フォールバック：本文の「←」行の直後の文章
    lines = [clean(x) for x in sp.get_text("\n", strip=True).splitlines() if clean(x)]
    for i, line in enumerate(lines):
        if "←" in line:
            for cand in lines[i + 1:i + 6]:
                if "←" not in cand and is_development_text(cand):
                    return cand
    return ""


def fetch_prediction(race):
    url = prediction_url(race["venue_slug"], race["race_no"])
    sp = soup(get_html(url))
    if not sp:
        return {"comments": {}, "line": [], "line_groups": [], "development": "", "url": url}
    return {
        "comments": parse_prediction_table(sp),
        "line": parse_line(sp)[0],
        "line_groups": parse_line(sp)[1],
        "development": parse_development(sp),
        "url": url,
    }


def calculate_ai_score(rider, race):
    score = 0.0
    base = rider.get("score")
    if base is not None:
        score += max(0, min(30, (base - 70) * 1.2))
    score += (rider.get("finish_1") or 0) * 1.8
    score += (rider.get("finish_2") or 0) * 0.9
    score += (rider.get("finish_3") or 0) * 0.4
    score += (rider.get("kimari_nige") or 0) * 0.8
    score += (rider.get("kimari_makuri") or 0) * 1.0
    score += (rider.get("kimari_sashi") or 0) * 1.0
    score += rider.get("prediction_score", 0)
    line = race.get("line", [])
    if rider.get("car_no") in line:
        p = line.index(rider["car_no"])
        score += 2 if p in (0, 2) else 4 if p == 1 else 0
    return round(score, 2)


def apply_ai(race):
    riders = race.get("riders", [])
    for r in riders:
        r["ai_score"] = calculate_ai_score(r, race)
    ranking = sorted(riders, key=lambda x: (x.get("ai_score", 0), x.get("score") or 0), reverse=True)
    race["ai"] = {
        "main": ranking[0]["car_no"] if ranking else None,
        "opponent": ranking[1]["car_no"] if len(ranking) > 1 else None,
        "dark_horse": ranking[2]["car_no"] if len(ranking) > 2 else None,
        "ranking": [{"car_no": r["car_no"], "name": r["name"], "score": r["ai_score"], "mark": r.get("prediction_mark", "")} for r in ranking],
    }


def main():
    started = time.time()
    print("==============================")
    print(f" KEIRIN AI DATA UPDATE v{VERSION}")
    print("==============================")
    print(f"対象日: {TODAY_DISPLAY}")
    print("==============================")

    venues = [{"code": c, "name": n, "slug": s} for c, (n, s) in VENUES.items()]
    print("開催場を確認中...")
    print(f"開催場数: {len(venues)}")
    print("実在レースを実ページで確認中...")

    all_races = []
    for venue in venues:
        races = discover_real_races(venue)
        venue["race_numbers"] = [r["race_no"] for r in races]
        print(f"  {venue['code']} {venue['name']}: {len(races)}レース")
        all_races.extend(races)
    all_races.sort(key=lambda x: (int(x["venue_code"]), x["race_no"]))
    print(f"詳細取得対象レース: {len(all_races)}")

    rider_success = sum(5 <= len(r["riders"]) <= 7 for r in all_races)
    print(f"選手データ取得結果: {rider_success}/{len(all_races)}")
    print("コメント・並び・展開情報取得中...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_prediction, r): r for r in all_races}
        for f in as_completed(futures):
            race = futures[f]
            try:
                p = f.result()
            except Exception:
                p = {"comments": {}, "line": [], "line_groups": [], "development": "", "url": ""}
            race.update({"comments": p["comments"], "line": p["line"], "line_groups": p["line_groups"], "development": p["development"], "prediction_url": p["url"]})

    for race in all_races:
        comments = race.get("comments", {})
        for rider in race["riders"]:
            info = comments.get(rider["car_no"], {})
            rider["prediction_mark"] = info.get("mark", "")
            rider["prediction_score"] = info.get("prediction_score", 0)
            rider["comment"] = info.get("comment", "")
        apply_ai(race)

    prediction_races = sum(len(r.get("comments", {})) == len(r["riders"]) and len(r["riders"]) >= 5 for r in all_races)
    comment_count = sum(len(r.get("comments", {})) for r in all_races)
    line_races = sum(len(r.get("line", [])) == len(r["riders"]) and len(r["riders"]) >= 5 for r in all_races)
    development_races = sum(bool(r.get("development")) for r in all_races)
    rider_count = sum(len(r["riders"]) for r in all_races)
    correct_names = sum(bool(rider.get("name")) for r in all_races for rider in r["riders"])
    ai_race_count = sum(bool(r.get("ai")) for r in all_races)
    ai_rider_count = sum(r.get("ai_score") is not None for race in all_races for r in race["riders"])

    failed_riders, failed_predictions, failed_lines, failed_development = [], [], [], []
    for r in all_races:
        label = f"{r['venue_name']} {r['race_no']}R"
        if not 5 <= len(r["riders"]) <= 7: failed_riders.append(label)
        if len(r.get("comments", {})) != len(r["riders"]): failed_predictions.append(label)
        if len(r.get("line", [])) != len(r["riders"]): failed_lines.append(label)
        if not r.get("development"): failed_development.append(label)

    data_complete = (
        len(all_races) == 72 and rider_success == 72 and rider_count == 500 and correct_names == 500
        and prediction_races == 72 and comment_count == 500 and line_races == 72 and development_races == 72
        and not failed_riders and not failed_predictions and not failed_lines
    )

    output = {
        "version": VERSION, "updated_at": datetime.now().isoformat(), "target_date": TODAY_DISPLAY,
        "data_complete": data_complete, "venue_count": len(venues), "race_count": len(all_races),
        "rider_count": rider_count, "correct_name_count": correct_names, "prediction_race_count": prediction_races,
        "comment_count": comment_count, "line_race_count": line_races, "development_count": development_races,
        "ai_race_count": ai_race_count, "ai_rider_count": ai_rider_count, "failed_race_count": len(failed_riders),
        "venues": [{"venue_code": v["code"], "venue_name": v["name"], "race_count": len(v["race_numbers"]), "race_numbers": v["race_numbers"]} for v in venues],
        "races": all_races,
    }

    os.makedirs("data", exist_ok=True)
    path = "data/today.json"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    elapsed = time.time() - started
    print("==============================")
    print(f" v{VERSION} 取得結果")
    print("==============================")
    print(f"開催場: {len(venues)}")
    print(f"レース: {len(all_races)}")
    print(f"選手: {rider_count}")
    print(f"正しい選手名: {correct_names}")
    print("【取得状況】")
    print(f"選手データ成功: {rider_success}/{len(all_races)}")
    print(f"予想情報レース: {prediction_races}/{len(all_races)}")
    print(f"選手コメント: {comment_count}")
    print(f"並び取得レース: {line_races}")
    print(f"展開コメント: {development_races}")
    print(f"AI評価レース: {ai_race_count}")
    print(f"AI評価選手: {ai_rider_count}")
    print(f"取得失敗レース: {len(failed_riders)}")
    print(f"処理時間: {elapsed:.1f}秒")
    print(f"データ完全性: {'OK' if data_complete else '要確認'}")
    print("==============================")
    print(" 詳細診断")
    print("==============================")
    print("選手データ不足: " + (", ".join(failed_riders) if failed_riders else "なし"))
    print("予想情報不足: " + (", ".join(failed_predictions) if failed_predictions else "なし"))
    print("並び不足: " + (", ".join(failed_lines) if failed_lines else "なし"))
    print("展開コメント不足: " + (", ".join(failed_development) if failed_development else "なし"))
    print(f"保存先: {path}")


if __name__ == "__main__":
    main()
