import json, os, re, time, warnings
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

VERSION='6.17'
BASE='https://www.oddspark.com'; SP='https://sp.oddspark.com'
JST=ZoneInfo('Asia/Tokyo')
def now_jst(): return datetime.now(JST)
TODAY=now_jst().strftime('%Y%m%d'); TODAY_DISPLAY=now_jst().strftime('%Y-%m-%d')
TIMEOUT=15; RETRIES=1; MAX_WORKERS=8; BET_UNIT=100; MAX_BETS=10
# 利益重視の暫定運用設定。実際の凍結予想は変更せず、推奨購入だけを別管理します。
DAILY_MAX_RACES=3; DAILY_BET_COUNT=5; DAILY_BUDGET=DAILY_MAX_RACES*DAILY_BET_COUNT*BET_UNIT
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36','Accept-Language':'ja-JP,ja;q=0.9,en;q=0.8'}
# 全国の競輪場。開催中の場は毎回オッズパークから自動検出する。
VENUE_MASTER = {
    '11': ('函館', 'hakodate'),
    '12': ('青森', 'aomori'),
    '13': ('いわき平', 'iwakitaira'),
    '21': ('弥彦', 'yahiko'),
    '22': ('前橋', 'maebashi'),
    '23': ('取手', 'toride'),
    '24': ('宇都宮', 'utsunomiya'),
    '25': ('大宮', 'omiya'),
    '26': ('西武園', 'seibuen'),
    '27': ('京王閣', 'keiokaku'),
    '28': ('立川', 'tachikawa'),
    '31': ('松戸', 'matsudo'),
    '34': ('川崎', 'kawasaki'),
    '35': ('平塚', 'hiratsuka'),
    '36': ('小田原', 'odawara'),
    '37': ('伊東', 'ito'),
    '38': ('静岡', 'shizuoka'),
    '42': ('名古屋', 'nagoya'),
    '43': ('岐阜', 'gifu'),
    '44': ('大垣', 'ogaki'),
    '45': ('豊橋', 'toyohashi'),
    '46': ('富山', 'toyama'),
    '47': ('松阪', 'matsusaka'),
    '48': ('四日市', 'yokkaichi'),
    '51': ('福井', 'fukui'),
    '53': ('奈良', 'nara'),
    '54': ('向日町', 'mukomachi'),
    '55': ('和歌山', 'wakayama'),
    '56': ('岸和田', 'kishiwada'),
    '61': ('玉野', 'tamano'),
    '62': ('広島', 'hiroshima'),
    '63': ('防府', 'hofu'),
    '71': ('高松', 'takamatsu'),
    '73': ('小松島', 'komatsushima'),
    '74': ('高知', 'kochi'),
    '75': ('松山', 'matsuyama'),
    '81': ('小倉', 'kokura'),
    '83': ('久留米', 'kurume'),
    '84': ('武雄', 'takeo'),
    '85': ('佐世保', 'sasebo'),
    '86': ('別府', 'beppu'),
    '87': ('熊本', 'kumamoto'),
}

VENUES = {
    code: (name, slug)
    for code, (name, slug) in VENUE_MASTER.items()
}
MARK_SCORE={'◎':18,'○':12,'▲':8,'△':5,'×':1,'注':3}
# v6.15: explainable rider/race components, odds break-even metrics, persistent multi-day settled-race history. Frozen picks remain immutable.
VENUE_BIAS={
    '前橋':{'nige':1.00,'makuri':1.08,'sashi':1.02},'大宮':{'nige':1.03,'makuri':1.05,'sashi':1.02},
    '平塚':{'nige':1.02,'makuri':1.08,'sashi':1.04},'静岡':{'nige':1.03,'makuri':1.04,'sashi':1.07},
    '大垣':{'nige':1.06,'makuri':1.04,'sashi':1.03},'四日市':{'nige':1.04,'makuri':1.06,'sashi':1.04},
    '奈良':{'nige':1.05,'makuri':1.06,'sashi':1.02},'高知':{'nige':1.04,'makuri':1.07,'sashi':1.03},
}
COMMENT_BOOST_WORDS=('先行','積極的','主導権','自力で','自力勝負','カマシ','逃げ')
COMMENT_CAUTION_WORDS=('自力自在','前で','自在に','自在','何でもやる','飛びつき','位置取り','好位','切り替え')
COMMENT_WEAK_WORDS=('追走','流れ込み','追走に専念','任せる','番手')
STYLE_WORDS={'逃捲','逃げ','先捲','捲り','捲','追込','追捲','自在','単騎','差脚','地差','先行'}
PREFECTURES=set('北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄'.split())

def clean(x): return re.sub(r'\s+',' ',str(x or '').replace('\xa0',' ').replace('　',' ')).strip()
def get_html(url):
    if not url:return None
    for a in range(RETRIES+1):
        try:
            r=requests.get(url,headers=HEADERS,timeout=TIMEOUT)
            if r.status_code==200 and len(r.text)>300:
                r.encoding=r.apparent_encoding or r.encoding; return r.text
        except requests.RequestException: pass
        if a<RETRIES: time.sleep(.4)
    return None
def soup(h):
    if not h:return None
    try:return BeautifulSoup(h,'lxml')
    except:return BeautifulSoup(h,'html.parser')
def absolute(h):
    if not h:return ''
    if h.startswith(('http://','https://')):return h
    if h.startswith('//'):return 'https:'+h
    return BASE+h if h.startswith('/') else BASE+'/'+h
def numbers4(t):
    m=re.search(r'(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)\s*[-−]\s*(\d+)',t or '')
    return [int(x) for x in m.groups()] if m else [None]*4

def race_url(c,n):return f'{BASE}/keirin/RaceList.do?joCode={c}&kaisaiBi={TODAY}&raceNo={n}'
def prediction_url(slug,n):return f'{SP}/keirin/yosou/{slug}/{TODAY[:4]}/{TODAY[4:]}'+('.html' if n==1 else f'_{n}.html')
def odds_url(c,n):return f'{SP}/keirin/SpOddsInfo.do?betType=9&dispMode=1&joCd={c}&joCode={c}&kaisaiBi={TODAY}&raceNo={n}'
def result_url(c, n):
    return (
        f'{BASE}/keirin/RaceKekka.do'
        f'?joCode={c}&kaisaiBi={TODAY}&raceNo={n}'
    )

def parse_rider_row(tr, car_idx=None):
    cells = [
        clean(c.get_text(' ', strip=True))
        for c in tr.find_all(['th', 'td'])
    ]
    text = clean(tr.get_text(' ', strip=True))

    # 表の見出しから特定できた車番列を最優先する。
    car = None

    if car_idx is not None and car_idx < len(cells):
        m = re.fullmatch(r'([1-9])', cells[car_idx])
        if m:
            car = int(m.group(1))

    # 車番列が特定できない場合のみ、従来の方法で補完する。
    if car is None:
        for cell in cells:
            if re.fullmatch(r'[1-9]', cell):
                car = int(cell)
                break

    if car is None:
        return None

    # 選手名は選手詳細リンクを優先する。
    name = ''

    for a in tr.find_all('a'):
        href = a.get('href', '')
        label = clean(a.get_text(' ', strip=True))

        if (
            label
            and (
                'PlayerDetail.do' in href
                or 'player' in href.lower()
            )
            and label not in MARK_SCORE
        ):
            name = label
            break

    if not name:
        for i, value in enumerate(cells):
            if value != str(car):
                continue

            for candidate in cells[i + 1:i + 7]:
                if (
                    candidate
                    and candidate not in MARK_SCORE
                    and not re.fullmatch(r'\d+', candidate)
                    and re.search(r'[一-龯ぁ-んァ-ヶ]', candidate)
                    and not any(
                        q in candidate
                        for q in (
                            '競走得点',
                            '着順',
                            '決まり手',
                            '今場所',
                            '前場所',
                        )
                    )
                ):
                    name = candidate
                    break

            if name:
                break

    if not name:
        return None

    m = re.search(r'(\d{1,2})歳\s*[／/]\s*(\d{2,3})期', text)
    age = int(m.group(1)) if m else None
    period = int(m.group(2)) if m else None

    pref = next(
        (
            p for p in sorted(PREFECTURES, key=len, reverse=True)
            if p in text
        ),
        ''
    )

    mg = re.search(r'\b([SAL]\d)\b', text)
    grade = mg.group(1) if mg else ''

    style = next(
        (
            s for s in ('逃', '捲', '追', '両')
            if re.search(rf'\s{s}\s*[|｜ ]', text)
        ),
        ''
    )

    ms = re.search(r'競走得点\s*[:：]?\s*(\d+(?:\.\d+)?)', text)
    score = float(ms.group(1)) if ms else None

    mf = re.search(r'着\s*順\s*[:：]?\s*([^|｜]+)', text)
    f = numbers4(mf.group(1) if mf else '')

    mk = re.search(r'決まり手\s*[:：]?\s*([^|｜]+)', text)
    k = numbers4(mk.group(1) if mk else '')

    return {
        'car_no': car,
        'name': name,
        'age': age,
        'period': period,
        'prefecture': pref,
        'grade': grade,
        'style': style,
        'score': score,
        'finish_1': f[0],
        'finish_2': f[1],
        'finish_3': f[2],
        'finish_out': f[3],
        'kimari_nige': k[0],
        'kimari_makuri': k[1],
        'kimari_sashi': k[2],
        'kimari_mark': k[3],
        'comment': '',
        'prediction_mark': '',
        'prediction_score': 0,
        'ai_score': 0,
    }


def parse_riders(sp):
    if not sp:
        return []

    best = {}

    for table in sp.find_all('table'):
        rows = table.find_all('tr')
        car_idx = None
        header_idx = None

        # 表の見出しから車番の列を特定する。
        for ri, tr in enumerate(rows[:8]):
            cells = [
                clean(c.get_text(' ', strip=True)).replace(' ', '')
                for c in tr.find_all(['th', 'td'])
            ]

            for ci, value in enumerate(cells):
                if '車番' in value:
                    car_idx = ci
                    header_idx = ri
                    break

            if car_idx is not None:
                break

        if car_idx is not None:
            for tr in rows[header_idx + 1:]:
                rider = parse_rider_row(tr, car_idx)

                if rider:
                    best[rider['car_no']] = rider

    # 見出しを持たないページ形式への互換処理。
    if not best:
        for tr in sp.find_all('tr'):
            rider = parse_rider_row(tr)

            if rider:
                best[rider['car_no']] = rider

    if not 5 <= len(best) <= 9:
        return []

    return [best[k] for k in sorted(best)]
def discover_related(sp):
    out={'odds_url':'','result_url':''}
    if not sp:return out
    for a in sp.find_all('a'):
        txt=clean(a.get_text(' ',strip=True)); h=a.get('href',''); u=absolute(h); low=(txt+' '+h).lower()
        if not out['odds_url'] and ('オッズ' in txt or 'oddsinfo' in low or 'odds' in low):out['odds_url']=u
        if not out['result_url'] and ('結果' in txt or 'raceresult' in low or 'resultinfo' in low):out['result_url']=u
    return out
def parse_start_time(sp):
    """開催ページから発走時刻を保守的に抽出。取れない場合は推測しない。"""
    if not sp:return ''
    text=clean(sp.get_text(' ',strip=True))
    patterns=(r'(?:発走予定|発走時刻|発走)\s*[:：]?\s*(\d{1,2}:\d{2})',
              r'(\d{1,2}:\d{2})\s*(?:発走予定|発走時刻|発走)')
    for pat in patterns:
        m=re.search(pat,text)
        if m:
            hh,mm=map(int,m.group(1).split(':'))
            if 0<=hh<=23 and 0<=mm<=59:return f'{hh:02d}:{mm:02d}'
    return ''

def fetch_race(job):
    u = race_url(job['venue_code'], job['race_no'])
    sp = soup(get_html(u))
    rel = discover_related(sp)
    riders = parse_riders(sp)
    return {
        **job,
        'url': u,
        'riders': riders,
        'start_time': parse_start_time(sp),
        **rel,
        'success': 5 <= len(riders) <= 9
    }
def discover_today_venues():
    """
    オッズパークの当日開催一覧を確認し、
    実際に開催されている競輪場を抽出する。
    """
    urls = [
        f'{SP}/keirin/SpSalePlaceList.do?kaisaiBi={TODAY}',
        f'{BASE}/keirin/KeirinTop.do',
    ]
    found = {}
    for url in urls:
        html = get_html(url)
        sp = soup(html)
        if not sp:
            continue
        for a in sp.find_all('a', href=True):
            href = a.get('href', '')
            match = re.search(
                r'(?:joCd|joCode)=(\d{2})',
                href,
                re.I,
            )
            if not match:
                continue
            code = match.group(1)
            if code not in VENUE_MASTER:
                continue
            name, slug = VENUE_MASTER[code]
            found[code] = {
                'code': code,
                'name': name,
                'slug': slug,
            }
        if found:
            break
    if found:
        print(
            '開催場の自動検出: '
            + ', '.join(
                f'{v["name"]}({v["code"]})'
                for v in found.values()
            )
        )
    else:
        print('開催場の自動検出に失敗しました')
    return list(found.values())
def discover_real_races(v):
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        rs=[f.result() for f in as_completed([ex.submit(fetch_race,{'venue_code':v['code'],'venue_name':v['name'],'venue_slug':v['slug'],'race_no':n}) for n in range(1,13)])]
    return sorted([r for r in rs if r['success']],key=lambda x:x['race_no'])

def parse_prediction_table(sp):
    if not sp:
        return {}

    result = {}

    for tr in sp.find_all('tr'):
        cells = [
            clean(c.get_text(' ', strip=True))
            for c in tr.find_all(['th', 'td'])
        ]

        cars = [
            int(v) for v in cells
            if re.fullmatch(r'[1-9]', v)
        ]

        if not cars:
            continue

        car = cars[0]
        mark = next(
            (v for v in cells if v in MARK_SCORE),
            ''
        )

        comments = [
            v for v in cells
            if v
            and v not in (str(car), mark)
            and v not in MARK_SCORE
            and re.search(r'[ぁ-んァ-ヶ]', v)
            and not any(
                q in v
                for q in ('選手名', 'コメント', '予想', '並び')
            )
        ]

        name = next(
            (
                v for v in comments
                if re.search(r'[一-龯]', v)
                and len(v) <= 12
            ),
            ''
        )

        comment = ' '.join(
            v for v in comments if v != name
        )

        if name or comment or mark:
            result[car] = {
                'name': name,
                'mark': mark,
                'comment': comment,
                'prediction_score': MARK_SCORE.get(mark, 0)
            }

    return result if 5 <= len(result) <= 9 else {}
def valid_cars(c):
    cars = set(c)
    return (
        5 <= len(cars) <= 9
        and cars <= set(range(1, 10))
    )
def parse_line(sp):
    if not sp:return [],[]
    for table in sp.find_all('table'):
        for tr in table.find_all('tr'):
            cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'])]; cars=list(dict.fromkeys(int(x) for x in cells if re.fullmatch(r'[1-9]',x)))
            if valid_cars(cars) and any(w in ' '.join(cells) for w in STYLE_WORDS):return cars,[cars]
    lines=[clean(x) for x in sp.get_text('\n',strip=True).splitlines() if clean(x)]
    for i,line in enumerate(lines):
        nums=list(dict.fromkeys(int(x) for x in re.findall(r'(?<!\d)([1-9])(?!\d)',line))); nearby=' '.join(lines[max(0,i-2):i+3])
        if valid_cars(nums) and any(w in nearby for w in STYLE_WORDS):return nums,[nums]
    return [],[]
def is_dev(t):
    t=clean(t); bad=('予想の並び','並び内の脚質','免責','Copyright','ページ先頭','投票メニュー','ログイン','会員登録','オッズ','出走表','レース一覧','発売','払戻')
    return 12<=len(t)<=350 and not any(x in t for x in bad) and re.search(r'[ぁ-んァ-ヶ一-龯]',t) and re.search(r'[。！？，、]',t)
def parse_development(sp):
    if not sp:return ''
    lines=[clean(x) for x in sp.get_text('\n',strip=True).splitlines() if clean(x)]
    for i,x in enumerate(lines):
        if '展開' in x:
            for y in lines[i+1:i+8]:
                if is_dev(y):return y
    cand=[x for x in lines if is_dev(x) and len(x)>=20]
    return max(cand,key=lambda x:len(x)) if cand else ''
def fetch_prediction(r):
    u=prediction_url(r['venue_slug'],r['race_no']); sp=soup(get_html(u)); line,groups=parse_line(sp)
    return {'comments':parse_prediction_table(sp),'line':line,'line_groups':groups,'development':parse_development(sp),'url':u}

def _comment_text(r):
    return clean(r.get('comment',''))

def _comment_score(r):
    """コメントは単純な文字数ではなく、戦法の意思を評価する。"""
    t=_comment_text(r)
    if not t:return 0.0
    score=0.0
    if any(w in t for w in COMMENT_BOOST_WORDS): score+=3.0
    if any(w in t for w in COMMENT_CAUTION_WORDS): score+=1.0
    if any(w in t for w in COMMENT_WEAK_WORDS): score-=0.8
    # 自力自在/自在は先行固定として扱わず、位置取り変化のリスクを少し織り込む。
    if '自力自在' in t or '自在' in t: score-=0.8
    return score

def _style_key(r):
    st=clean(r.get('style',''))
    if '逃' in st:return 'nige'
    if '捲' in st:return 'makuri'
    if '追' in st:return 'sashi'
    return 'sashi'

def _recent_form_score(r):
    """取得できる直近集計着順から、単純な強さだけでなく安定性を評価。"""
    f1,f2,f3,fo=[r.get(k) for k in ('finish_1','finish_2','finish_3','finish_out')]
    nums=[x for x in (f1,f2,f3,fo) if isinstance(x,(int,float)) and x is not None]
    if not nums:return 0.0
    return (f1 or 0)*1.8+(f2 or 0)*0.9+(f3 or 0)*0.45+(fo or 0)*0.05

def _line_context_score(r,race):
    riders=race.get('riders',[]) or []; c=r.get('car_no'); line=race.get('line',[]) or []
    if c not in line:return 0.0
    idx=line.index(c); score=0.0
    # 番手は基本的に展開恩恵。先頭も自力型なら残り目を評価。
    if idx==1: score+=4.5
    elif idx==0: score+=2.5
    else: score+=1.5
    # 同県ラインは結束を少し加点。ただし同県だけで順位を逆転させない。
    if idx>0:
        prev=line[idx-1]
        pr=next((x for x in riders if x.get('car_no')==prev),None)
        if pr and pr.get('prefecture') and pr.get('prefecture')==r.get('prefecture'):score+=1.8
    # ライン後方は上位候補としての優先度を少し下げる。
    if idx>=2:score-=0.5*(idx-1)
    return score

def _development_score(r,race):
    text=clean(race.get('development',''))
    if not text:return 0.0
    c=r.get('car_no'); line=race.get('line',[]) or []
    if c not in line:return 0.0
    idx=line.index(c); score=0.0
    # 展開文に番号が登場する場合は、位置に応じて弱く反映。
    if re.search(rf'(?<!\d){c}(?!\d)',text):
        score+=1.2 if idx==1 else 0.7
    if any(w in text for w in ('番手','差し','追込')) and idx==1:score+=1.2
    if any(w in text for w in ('先行','主導権','逃げ')) and idx==0:score+=1.0
    return score

def _bank_fit_score(r,race):
    venue=race.get('venue_name',''); bias=VENUE_BIAS.get(venue,{})
    st=_style_key(r); return 2.2*(bias.get(st,1.0)-1.0)*10

def calculate_ai_components(r,race):
    """スコアを基礎能力とレース固有要素に分け、説明・検証可能にする。"""
    base=max(0,min(32,((r.get('score') or 70)-70)*1.35))
    form=0.42*_recent_form_score(r)
    kimari=(r.get('kimari_nige') or 0)*0.9+(r.get('kimari_makuri') or 0)*1.15+(r.get('kimari_sashi') or 0)*1.05+(r.get('kimari_mark') or 0)*0.25
    mark=0.65*(r.get('prediction_score',0) or 0)
    comment=_comment_score(r)
    line=_line_context_score(r,race)
    development=_development_score(r,race)
    bank=_bank_fit_score(r,race)
    intent=0.0; t=_comment_text(r)
    if any(w in t for w in ('先行します','先行したい','積極的に行く')):intent+=1.8
    if any(w in t for w in ('自力自在','自在')):intent-=0.6
    race_specific=comment+line+development+bank+intent
    total=max(0,min(100,base+form+kimari+mark+race_specific))
    return {'base_strength':round(base,2),'recent_form':round(form,2),'finishing_methods':round(kimari,2),
            'prediction_mark':round(mark,2),'comment_intent':round(comment+intent,2),'line_fit':round(line,2),
            'development_fit':round(development,2),'venue_fit':round(bank,2),
            'race_specific':round(race_specific,2),'total':round(total,2)}

def calculate_ai_score(r,race):
    parts=calculate_ai_components(r,race)
    r['ai_components']=parts
    return parts['total']

def _prediction_quality(race):
    riders=race.get('riders',[]) or []; line=race.get('line',[]) or []
    q=0
    if len(riders)>=5:q+=1
    if len(race.get('comments',{}))==len(riders):q+=2
    if len(line)==len(riders):q+=2
    if race.get('development'):q+=2
    if race.get('prediction_url'):q+=1
    return q

def calculate_verdict(race):
    rs=sorted(race.get('riders',[]),key=lambda x:(x.get('ai_score',0),x.get('score') or 0),reverse=True)
    if not rs:return {'rank':'C','label':'⚠️ 見送り','score':0,'reason':'AI評価対象データ不足'}
    a,b,c=rs[0].get('ai_score',0),rs[1].get('ai_score',0) if len(rs)>1 else rs[0].get('ai_score',0),rs[2].get('ai_score',0) if len(rs)>2 else rs[-1].get('ai_score',0)
    gap=max(0,a-b); gap2=max(0,b-c)
    quality=_prediction_quality(race)
    # 旧v6.11より「上位との差」「データ品質」「ライン恩恵」を明確に分離。
    v=50+min(18,gap*1.8)+min(8,gap2*0.8)+min(10,quality*1.25)
    if len(race.get('line',[]))==len(rs):v+=3
    if race.get('development'):v+=2
    if any(_comment_text(x) for x in rs[:3]):v+=2
    v=min(100,int(round(v)))
    rank='S' if v>=84 else 'A' if v>=74 else 'B' if v>=63 else 'C'
    label={'S':'🔥 勝負','A':'🎯 買い','B':'⭐ 少額','C':'⚠️ 見送り'}[rank]
    return {'rank':rank,'label':label,'score':v,'reason':'基礎能力＋直近成績＋決まり手＋ライン＋コメント＋展開＋バンク適性を総合評価','model':'v6.15'}
def ticket(a,b,c):return f'{a}-{b}-{c}' if None not in (a,b,c) and len({a,b,c})==3 else ''
def generate_bets(race, limit=MAX_BETS):
    rs=sorted(race.get('riders',[]),key=lambda x:(x.get('ai_score',0),x.get('score') or 0),reverse=True)
    if len(rs)<3:return []
    m,s,t=[x['car_no'] for x in rs[:3]]; q=rs[3]['car_no'] if len(rs)>3 else None; f=rs[4]['car_no'] if len(rs)>4 else None; d=race.get('ai',{}).get('dark_horse') or t
    cs=[(m,s,t,100,'🔥 AI本線'),(m,t,s,97,'🔥 本線入替'),(s,m,t,94,'⭐ 対抗頭'),(m,s,d,92,'🎯 穴絡み'),(m,d,s,90,'🎯 穴入替'),(t,m,s,82,'💥 逆転'),(s,t,m,80,'💥 対抗展開'),(m,q,s,76,'🎯 中穴'),(q,m,t,72,'💥 高配当'),(m,d,f,68,'💣 大穴警戒'),(d,m,s,65,'💣 穴頭')]
    for g in race.get('line_groups',[]):
        if len(g)>=2:cs += [(g[0],g[1],m,88,'🚴 ライン本線'),(g[1],g[0],m,85,'🔄 番手差し'),(g[0],m,g[1],82,'⚡ 先頭残り')]
    line=set(race.get('line',[])); out={}
    for a,b,c,sc,lab in cs:
        tk=ticket(a,b,c)
        if tk:
            tactical=0
            for pos,x in enumerate((a,b,c)):
                if x in line:tactical+=2
                rider=next((rr for rr in race.get('riders',[]) if rr.get('car_no')==x),None)
                if rider and pos==0:tactical+=max(0,_comment_score(rider))*0.7
                if rider and pos==1:tactical+=1.0
            val={'ticket':tk,'score':round(sc+tactical,2),'type':lab,'bet_yen':BET_UNIT}
            out[tk]=max(out.get(tk,{'score':-1}),val,key=lambda x:x['score'])
    return sorted(out.values(),key=lambda x:x['score'],reverse=True)[:limit]
def apply_ai(race):
    for r in race['riders']:r['ai_score']=calculate_ai_score(r,race)
    rank=sorted(race['riders'],key=lambda x:(x.get('ai_score',0),x.get('score') or 0),reverse=True)
    race['ai']={'model':'v6.15','main':rank[0]['car_no'],'opponent':rank[1]['car_no'],'dark_horse':rank[2]['car_no'],'ranking':[{'car_no':r['car_no'],'name':r['name'],'score':r['ai_score'],'mark':r.get('prediction_mark','')} for r in rank]}
    race['verdict']=calculate_verdict(race); race['bets']=generate_bets(race)

def parse_ticket_text(t):
    m = re.search(
        r'([1-9])\s*[→＞>\-−]\s*'
        r'([1-9])\s*[→＞>\-−]\s*'
        r'([1-9])',
        clean(t)
    )

    if not m:
        return None

    cars = [int(x) for x in m.groups()]

    if len(set(cars)) != 3:
        return None

    return '-'.join(map(str, cars))
def parse_yen_text(t):return [int(x.replace(',','')) for x in re.findall(r'([\d,]+)\s*円',clean(t)) if int(x.replace(',',''))>0]
def payout_scan(sp,text):
    rows=sp.find_all('tr') if sp else []
    for i,tr in enumerate(rows):
        row=clean(tr.get_text(' ',strip=True))
        if '3連単' not in row:continue
        near=' '.join(clean(x.get_text(' ',strip=True)) for x in rows[max(0,i-2):min(len(rows),i+5)])
        tk=parse_ticket_text(near); ys=parse_yen_text(near)
        if tk and ys:return tk,ys[0],near
    for key in ('3連単','払戻金','払戻'):
        p=text.find(key)
        while p>=0:
            w=text[max(0,p-100):p+500]; tk=parse_ticket_text(w); ys=parse_yen_text(w)
            if tk and ys:return tk,ys[0],w
            p=text.find(key,p+len(key))
    return None,0,''

def result_cell_text(cell):
    """セルの文字に画像のalt/titleも加える。"""
    parts = [clean(cell.get_text(' ', strip=True))]

    for img in cell.find_all(['img']):
        parts.extend([
            clean(img.get('alt', '')),
            clean(img.get('title', '')),
        ])

    return clean(' '.join(x for x in parts if x))


def parse_ticket_text(t):
    t = clean(t)

    m = re.search(
        r'([1-9])\s*[→＞>\-−]\s*'
        r'([1-9])\s*[→＞>\-−]\s*'
        r'([1-9])',
        t,
    )

    if not m:
        return None

    cars = [int(x) for x in m.groups()]

    if len(set(cars)) != 3:
        return None

    return '-'.join(map(str, cars))


def parse_yen_text(t):
    return [
        int(x.replace(',', ''))
        for x in re.findall(r'([\d,]+)\s*円', clean(t))
        if int(x.replace(',', '')) > 0
    ]


def payout_scan(sp, text):
    """
    払戻表を調べ、3連単の組み合わせと払戻金を取得する。
    読み取れない場合は誤った金額を推測せず、未取得として返す。
    """
    if not sp:
        return None, 0, ''

    for table in sp.find_all('table'):
        rows = table.find_all('tr')

        for i, tr in enumerate(rows):
            cells = [
                result_cell_text(c)
                for c in tr.find_all(['th', 'td'])
            ]

            row_text = clean(' '.join(cells))

            if '3連勝' not in row_text:
                continue

            # 3連勝欄の近くにある「単」表記を優先する。
            for j in range(i, min(len(rows), i + 12)):
                window_rows = rows[max(i, j - 1):min(len(rows), j + 3)]

                window = ' '.join(
                    result_cell_text(c)
                    for row in window_rows
                    for c in row.find_all(['th', 'td'])
                )

                if '単' not in window:
                    continue

                ticket = parse_ticket_text(window)
                yen = parse_yen_text(window)

                if ticket and yen:
                    return ticket, yen[-1], window

    # ページ全体のテキストでも補完する。
    # 組み合わせと金額の両方を特定できた場合だけ採用する。
    page_text = clean(sp.get_text(' ', strip=True))

    for key in ('3連単', '3連勝'):
        start = 0

        while True:
            pos = page_text.find(key, start)

            if pos < 0:
                break

            window = page_text[max(0, pos - 100):pos + 700]
            ticket = parse_ticket_text(window)
            yen = parse_yen_text(window)

            if ticket and yen:
                return ticket, yen[-1], window

            start = pos + len(key)

    return None, 0, ''


def parse_result_page(html):
    result = {
        'finished': False,
        'result_finished': False,
        'finish': [],
        'result_status': 'pending',
        'payout_available': False,
        'payout_3tan': None,
        'payout_3tan_yen': 0,
        'payout_source': '',
        'payout_status': 'pending',
    }

    sp = soup(html)

    if not sp:
        result['result_status'] = 'error'
        result['payout_status'] = 'error'
        return result

    finish_map = {}

    for table in sp.find_all('table'):
        rows = table.find_all('tr')
        header_idx = None
        pos_idx = None
        car_idx = None

        for ri, tr in enumerate(rows[:10]):
            cells = [
                clean(c.get_text(' ', strip=True)).replace(' ', '')
                for c in tr.find_all(['th', 'td'])
            ]

            for ci, value in enumerate(cells):
                if value in ('着', '着順') and pos_idx is None:
                    pos_idx = ci

                if '車番' in value and car_idx is None:
                    car_idx = ci

            if pos_idx is not None and car_idx is not None:
                header_idx = ri
                break

        if header_idx is None:
            continue

        for tr in rows[header_idx + 1:]:
            cells = [
                clean(c.get_text(' ', strip=True))
                for c in tr.find_all(['th', 'td'])
            ]

            if max(pos_idx, car_idx) >= len(cells):
                continue

            # 着順と車番のセルだけを読む。
            pm = re.fullmatch(r'\s*([1-9])\s*', cells[pos_idx])
            cm = re.fullmatch(r'\s*([1-9])\s*', cells[car_idx])

            if not pm or not cm:
                continue

            pos = int(pm.group(1))
            car = int(cm.group(1))

            if pos not in finish_map:
                finish_map[pos] = car

    finish = [
        finish_map[p]
        for p in sorted(finish_map)
    ]

    # 3着までの着順が確認できた場合だけ、結果確定とする。
    result_finished = (
        len(finish) >= 3
        and len(set(finish[:3])) == 3
    )

    if result_finished:
        result['result_status'] = 'finished'

    ticket, yen, source = payout_scan(
        sp,
        clean(sp.get_text(' ', strip=True)),
    )

    payout_available = bool(
        ticket
        and yen > 0
        and result_finished
    )

    if payout_available:
        result['payout_status'] = 'available'

    result.update({
        'finished': result_finished and payout_available,
        'result_finished': result_finished,
        'finish': finish,
        'payout_available': payout_available,
        'payout_3tan': ticket if payout_available else None,
        'payout_3tan_yen': yen if payout_available else 0,
        'payout_source': source,
    })

    return result


def fetch_result(race):
    """
    現行の結果ページを優先する。
    古いURLが保存されていても、新しいURLから確認する。
    """
    code = str(race['venue_code'])
    race_no = int(race['race_no'])

    urls = [
        result_url(code, race_no),
        race.get('result_url', ''),
        (
            f'{BASE}/keirin/RaceResultInfo.do'
            f'?joCode={code}&kaisaiBi={TODAY}&raceNo={race_no}'
        ),
    ]

    # URLの重複を除去する。
    urls = list(dict.fromkeys(u for u in urls if u))

    last = None

    for url in urls:
        html = get_html(url)

        if not html:
            continue

        res = parse_result_page(html)
        res['url'] = url
        last = res

        # 着順と払戻が揃ったら確定。
        if res.get('result_finished') and res.get('payout_available'):
            return res

        # 結果だけ取得できた場合も、別URLで払戻を確認する。
        if res.get('result_finished'):
            continue

    if last is not None:
        return last

    return {
        'finished': False,
        'result_finished': False,
        'finish': [],
        'result_status': 'error',
        'payout_available': False,
        'payout_3tan': None,
        'payout_3tan_yen': 0,
        'payout_source': '',
        'payout_status': 'error',
    }

def parse_odds_page(html):
    odds = {}
    if not html:
        return {'available': False, 'odds': odds}

    sp = BeautifulSoup(html, 'html.parser')
    text = clean(sp.get_text(' ', strip=True))

    pattern = (
        r'([1-9])\s*[→＞>\-−]\s*'
        r'([1-9])\s*[→＞>\-−]\s*'
        r'([1-9])\s+(\d+(?:\.\d+)?)'
    )

    for m in re.finditer(pattern, text):
        first = int(m.group(1))
        second = int(m.group(2))
        third = int(m.group(3))
        value = float(m.group(4))

        # 同じ車番を重複した買い目として登録しない
        if len({first, second, third}) != 3:
            continue

        odds[f'{first}-{second}-{third}'] = value

    return {
        'available': bool(odds),
        'odds': odds
    }
def fetch_odds(race):
    for u in [race.get('odds_url',''),odds_url(race['venue_code'],race['race_no'])]:
        if u:
            p=parse_odds_page(get_html(u))
            if p['available']:p['url']=u;return p
    return {'available':False,'odds':{},'url':odds_url(race['venue_code'],race['race_no'])}
def attach_bet_odds(race):
    om=race.get('odds',{}).get('odds',{});bets=race.get('frozen_prediction',{}).get('bets',[]);rank={k:i for i,(k,v) in enumerate(sorted(om.items(),key=lambda x:x[1]),1)}
    for b in bets:
        v=om.get(b.get('ticket'));stake=int(b.get('bet_yen',BET_UNIT) or BET_UNIT)
        b['odds']=v;b['market_rank']=rank.get(b.get('ticket'))
        b['break_even_hit_rate_pct']=round(100.0/v,2) if isinstance(v,(int,float)) and v>0 else None
        b['gross_return_if_hit_yen']=round(stake*v) if isinstance(v,(int,float)) and v>0 else None
        b['net_profit_if_hit_yen']=round(stake*v-stake) if isinstance(v,(int,float)) and v>0 else None
        # オッズだけでは独立した的中確率がないため、期待値を捏造しない。
        b['expected_value_yen']=None
        b['expected_value_status']='的中確率モデル未校正' if v is not None else 'オッズ未取得'
        b['value_flag']='オッズ未取得' if v is None else ('💎 高配当候補（期待値未確定）' if v>=30 else '🎯 中穴' if v>=15 else '⭐ 適正' if v>=8 else '⚠️ 人気')
def settle_bets(race):
    bets=race.get('frozen_prediction',{}).get('bets',[]);res=race.get('result',{});inv=sum(int(b.get('bet_yen',BET_UNIT)) for b in bets);tk=res.get('payout_3tan');y=int(res.get('payout_3tan_yen',0) or 0);ready=bool(res.get('result_finished') and res.get('payout_available') and tk and y>0)
    if not ready:return {'status':'pending','bet_count':len(bets),'investment':0,'hit':False,'hit_ticket':'','payout':0,'profit':0,'roi':0}
    hit=any(b.get('ticket')==tk for b in bets);pay=y if hit else 0
    return {'status':'settled','bet_count':len(bets),'investment':inv,'hit':hit,'hit_ticket':tk if hit else '','payout':pay,'profit':pay-inv,'roi':round(pay/inv*100,1) if inv else 0}
def _race_rank(race):
    v=race.get('verdict',{}) or {}
    rank=v.get('rank') or v.get('rating') or ''
    return str(rank).upper().strip()

def _favorite_odds(race):
    om=(race.get('odds',{}) or {}).get('odds',{}) or {}
    vals=[]
    for v in om.values():
        try:
            x=float(v)
            if x>0: vals.append(x)
        except: pass
    return min(vals) if vals else None

def backtest_strategy(races, limit=10, favorite_min=None, rank_filter=None):
    out={
        'races':0,'settled_races':0,'investment':0,'payout':0,'profit':0,
        'hits':0,'hit_rate':0,'roi':0,'skipped_no_odds':0,
        'positive_races':0,'negative_races':0,'max_losing_streak':0,
        'max_drawdown':0,'peak_profit':0
    }
    running=0; peak=0; losing_streak=0
    for race in sorted(races,key=lambda x:int(x.get('race_no',0) or 0)):
        res=race.get('result',{}) or {}
        if not (res.get('result_finished') and res.get('payout_available') and
                res.get('payout_3tan') and int(res.get('payout_3tan_yen',0) or 0)>0):
            continue
        if rank_filter:
            if _race_rank(race) not in rank_filter:
                continue
        if favorite_min is not None:
            fav=_favorite_odds(race)
            if fav is None:
                out['skipped_no_odds']+=1
                continue
            if fav < favorite_min:
                continue
        bets=generate_bets(race,limit=limit)
        if not bets:
            continue
        out['races']+=1
        out['settled_races']+=1
        inv=len(bets)*BET_UNIT
        payout=0
        tk=res.get('payout_3tan')
        if any(b.get('ticket')==tk for b in bets):
            out['hits']+=1
            payout=int(res.get('payout_3tan_yen',0) or 0)
        out['investment']+=inv
        out['payout']+=payout
        race_profit=payout-inv
        if race_profit>0:
            out['positive_races']+=1
            losing_streak=0
        else:
            out['negative_races']+=1
            losing_streak+=1
            out['max_losing_streak']=max(out['max_losing_streak'],losing_streak)
        running+=race_profit
        peak=max(peak,running)
        out['max_drawdown']=max(out['max_drawdown'],peak-running)
    out['profit']=out['payout']-out['investment']
    out['peak_profit']=peak
    out['hit_rate']=round(out['hits']/out['settled_races']*100,1) if out['settled_races'] else 0
    out['roi']=round(out['payout']/out['investment']*100,1) if out['investment'] else 0
    out['positive_rate']=round(out['positive_races']/out['settled_races']*100,1) if out['settled_races'] else 0
    return out

def calculate_backtests(races):
    specs=[
        ('AI3',3,None,None),('AI5',5,None,None),('AI7',7,None,None),
        ('AI10',10,None,None),('AI15',15,None,None),
        ('人気5倍以上+AI5',5,5,None),('人気10倍以上+AI5',5,10,None),
        ('人気15倍以上+AI5',5,15,None),('人気20倍以上+AI5',5,20,None),
        ('人気10倍以上+AI10',10,10,None),
        ('人気15倍以上+AI10',10,15,None),('人気20倍以上+AI10',10,20,None),
        ('Sのみ+AI5',5,None,{'S'}),('Aのみ+AI5',5,None,{'A'}),
        ('S+Aのみ+AI5',5,None,{'S','A'}),
        ('Aのみ+AI10',10,None,{'A'}),('S+Aのみ+AI10',10,None,{'S','A'}),
        ('人気10倍以上+Aのみ+AI5',5,10,{'A'}),
        ('人気10倍以上+S+A+AI5',5,10,{'S','A'}),
    ]
    return {name:backtest_strategy(races,limit,threshold,ranks)
            for name,limit,threshold,ranks in specs}

def select_backtest_recommendation(backtests):
    valid=[(name,b) for name,b in backtests.items() if b.get('settled_races',0)>=5 and b.get('investment',0)>0]
    if not valid:
        return {'status':'insufficient_data','strategy':None,'reason':'確定5R未満のため推奨判定を保留'}
    # Profit first; ROI is the tie-breaker. This is a diagnostic recommendation,
    # not an automatic change to the real frozen bets.
    best=max(valid,key=lambda x:(x[1].get('profit',-10**18),x[1].get('roi',-10**18),x[1].get('positive_rate',-10**18)))
    name,b=best
    return {
        'status':'provisional',
        'strategy':name,
        'profit':b['profit'],
        'roi':b['roi'],
        'hit_rate':b['hit_rate'],
        'races':b['settled_races'],
        'note':'確定レースが少ないため暫定。実際の凍結買い目は変更しません。'
    }


def daily_recommended_races(races, max_races=DAILY_MAX_RACES, bet_count=DAILY_BET_COUNT,
                            rank_filter={'A'}, favorite_min=None):
    """Pre-race information onlyで、その日の購入候補を優先順位付けする。
    結果・払戻情報は選定スコアに使わないため、バックテスト時もルックアヘッドを避ける。
    """
    candidates=[]
    for race in races:
        rank=_race_rank(race)
        if rank_filter and rank not in rank_filter:
            continue
        if favorite_min is not None:
            fav=_favorite_odds(race)
            if fav is None or fav < favorite_min:
                continue
        bets=generate_bets(race,limit=bet_count)
        if len(bets)<bet_count:
            continue
        verdict=race.get('verdict',{}) or {}
        score=float(verdict.get('score',0) or 0)
        # 同点時はAI上位との差を使って順位を安定させる。
        ranking=race.get('ai',{}).get('ranking',[]) or []
        top1=float(ranking[0].get('score',0) or 0) if ranking else 0
        top2=float(ranking[1].get('score',0) or 0) if len(ranking)>1 else 0
        confidence=max(0,top1-top2)
        candidates.append({
            'venue_code':str(race.get('venue_code','')),
            'venue_name':race.get('venue_name',''),
            'race_no':int(race.get('race_no',0) or 0),
            'rank':rank,
            'verdict_score':score,
            'confidence_gap':confidence,
            'selection_score':round(score*10+confidence,2),
            'favorite_odds':_favorite_odds(race),
            'bets':[dict(b) for b in bets],
            'investment':len(bets)*BET_UNIT
        })
    candidates.sort(key=lambda x:(x['selection_score'],x['verdict_score'],x['confidence_gap']),
                    reverse=True)
    selected=candidates[:max_races]
    return {
        'max_races':max_races,
        'bet_count':bet_count,
        'budget_limit':max_races*bet_count*BET_UNIT,
        'candidate_count':len(candidates),
        'selected_count':len(selected),
        'candidates':candidates,
        'selected':selected
    }

def backtest_daily_top_strategy(races, max_races=DAILY_MAX_RACES, bet_count=DAILY_BET_COUNT,
                                rank_filter={'A'}, favorite_min=None):
    """その日の事前情報だけで上位レースを選び、確定結果で損益を検証する。"""
    selected=daily_recommended_races(races,max_races,bet_count,rank_filter,favorite_min)
    settled=[]
    for item in selected['selected']:
        race=next((r for r in races
                   if str(r.get('venue_code',''))==item['venue_code']
                   and int(r.get('race_no',0) or 0)==item['race_no']),None)
        if not race:
            continue
        res=race.get('result',{}) or {}
        if not (res.get('result_finished') and res.get('payout_available')
                and res.get('payout_3tan') and int(res.get('payout_3tan_yen',0) or 0)>0):
            continue
        payout=0
        if any(b.get('ticket')==res.get('payout_3tan') for b in item['bets']):
            payout=int(res.get('payout_3tan_yen',0) or 0)
        investment=len(item['bets'])*BET_UNIT
        settled.append({
            'venue_name':item['venue_name'],'race_no':item['race_no'],
            'rank':item['rank'],'selection_score':item['selection_score'],
            'investment':investment,'payout':payout,'profit':payout-investment,
            'hit':payout>0
        })
    investment=sum(x['investment'] for x in settled)
    payout=sum(x['payout'] for x in settled)
    hits=sum(1 for x in settled if x['hit'])
    profit=payout-investment
    return {
        'strategy':f"Aのみ・上位{max_races}R・AI{bet_count}",
        'max_races':max_races,'bet_count':bet_count,
        'candidate_count':selected['candidate_count'],
        'selected_count':selected['selected_count'],
        'settled_count':len(settled),'hits':hits,
        'hit_rate':round(hits/len(settled)*100,1) if settled else 0,
        'investment':investment,'payout':payout,'profit':profit,
        'roi':round(payout/investment*100,1) if investment else 0,
        'positive_days':1 if profit>0 and settled else 0,
        'negative_days':1 if profit<0 and settled else 0,
        'zero_days':1 if profit==0 and settled else 0,
        'selected':selected['selected'],
        'settled':settled
    }

def build_daily_strategy_report(races):
    tests={}
    for max_races in (1,2,3,4,5):
        tests[f'Aのみ・上位{max_races}R・AI5']=backtest_daily_top_strategy(
            races,max_races,5,{'A'},None)
    # オッズ10倍以上を加えた候補も比較。
    for max_races in (1,2,3,5):
        tests[f'Aのみ・人気10倍以上・上位{max_races}R・AI5']=backtest_daily_top_strategy(
            races,max_races,5,{'A'},10)
    valid=[(n,x) for n,x in tests.items() if x.get('settled_count',0)>0]
    best=max(valid,key=lambda z:(z[1].get('profit',-10**18),
                                 z[1].get('roi',-10**18),
                                 z[1].get('hit_rate',-10**18))) if valid else (None,{})
    return {'tests':tests,'best_name':best[0],'best':best[1],
            'operating':backtest_daily_top_strategy(races,DAILY_MAX_RACES,DAILY_BET_COUNT,{'A'},None)}

def create_daily_strategy_lock(races, date_text, now=None):
    """Lock a prospective A-rank strategy only for races with a known future start time."""
    now=now or now_jst()
    eligible=[]
    for r in races:
        st=clean(r.get('start_time',''))
        if not st: continue
        try:
            hh,mm=map(int,st.split(':'))
            start=now.replace(hour=hh,minute=mm,second=0,microsecond=0)
            if start <= now + timedelta(minutes=2): continue
        except Exception:
            continue
        fp=r.get('frozen_prediction',{}) or {}
        verdict=fp.get('verdict',{}) or {}
        rank=verdict.get('rank','C')
        if rank!='A': continue
        bets=fp.get('bets',[]) or []
        picks=[{'ticket':b.get('ticket'),'bet_yen':int(b.get('bet_yen',BET_UNIT) or BET_UNIT)} for b in bets[:DAILY_BET_COUNT] if b.get('ticket')]
        if len(picks)<DAILY_BET_COUNT: continue
        eligible.append({'venue_code':str(r.get('venue_code')),'venue_name':r.get('venue_name',''),
                         'race_no':int(r.get('race_no',0)),'start_time':st,'rank':rank,
                         'verdict_score':int(verdict.get('score',0) or 0),
                         'model':verdict.get('model',''),'prediction_created_at':fp.get('created_at',''),
                         'bets':picks,'investment_yen':sum(x['bet_yen'] for x in picks),
                         'status':'pending','result_ticket':'','payout_yen':0,'hit':False,'return_yen':0,'profit_yen':0})
    eligible.sort(key=lambda x:(x['verdict_score'],x['start_time']),reverse=True)
    selected=eligible[:DAILY_MAX_RACES]
    return {'date':date_text,'locked_at':now.isoformat(),'status':'locked' if selected else 'no_eligible_races',
            'selection_rule':'Aランクのみ・発走2分以上前に時刻確認できたレース・判定スコア上位',
            'max_races':DAILY_MAX_RACES,'points_per_race':DAILY_BET_COUNT,
            'investment_cap_yen':DAILY_BUDGET,'eligible_count':len(eligible),'selected_count':len(selected),
            'selected':selected,'note':'発走時刻を取得できないレース、発走済みレースは選外。選定後の買い目は変更しない。'}

def settle_daily_strategy_lock(lock, races):
    """Update only settlement fields; never change the locked races or tickets."""
    if not isinstance(lock,dict): return lock
    lookup={(str(r.get('venue_code')),int(r.get('race_no',0))):r for r in races}
    for pick in lock.get('selected',[]):
        r=lookup.get((str(pick.get('venue_code')),int(pick.get('race_no',0))))
        if not r: continue
        res=r.get('result',{}) or {}
        if not (res.get('result_finished') and res.get('payout_available') and res.get('payout_3tan')): continue
        winning=str(res.get('payout_3tan')); payout=int(res.get('payout_3tan_yen',0) or 0)
        if payout<=0: continue
        hit=any(str(b.get('ticket'))==winning for b in pick.get('bets',[]))
        pick.update({'status':'settled','result_ticket':winning,'payout_yen':payout,
                     'hit':hit,'return_yen':payout if hit else 0,
                     'profit_yen':(payout if hit else 0)-int(pick.get('investment_yen',0) or 0)})
    settled=[x for x in lock.get('selected',[]) if x.get('status')=='settled']
    investment=sum(int(x.get('investment_yen',0) or 0) for x in settled)
    payout=sum(int(x.get('return_yen',0) or 0) for x in settled)
    hits=sum(bool(x.get('hit')) for x in settled)
    lock['settlement_summary']={'settled_races':len(settled),'hits':hits,'investment_yen':investment,
                                'return_yen':payout,'profit_yen':payout-investment,
                                'hit_rate_pct':round(hits/len(settled)*100,1) if settled else 0,
                                'roi_pct':round(payout/investment*100,1) if investment else 0}
    return lock

def summarize_locked_strategy_history(history):
    locks=[d.get('strategy_lock') for d in history if isinstance(d,dict) and isinstance(d.get('strategy_lock'),dict)]
    settled=[x for lock in locks for x in lock.get('selected',[]) if x.get('status')=='settled']
    inv=sum(int(x.get('investment_yen',0) or 0) for x in settled)
    ret=sum(int(x.get('return_yen',0) or 0) for x in settled)
    hits=sum(bool(x.get('hit')) for x in settled)
    return {'days_with_lock':sum(lock.get('status')=='locked' for lock in locks),'selected_races':sum(len(lock.get('selected',[])) for lock in locks),
            'settled_races':len(settled),'hits':hits,'investment_yen':inv,'return_yen':ret,
            'profit_yen':ret-inv,'hit_rate_pct':round(hits/len(settled)*100,1) if settled else 0,
            'roi_pct':round(ret/inv*100,1) if inv else 0}

def load_performance_history():
    """today.json内の履歴を日付切替後も読む。別ファイル不要なので既存Actions設定で保存できる。"""
    try:
        with open('data/today.json',encoding='utf-8') as f:d=json.load(f)
        h=d.get('performance_history',[])
        return h if isinstance(h,list) else []
    except Exception:
        return []

def update_performance_history(history,races,date_text,strategy_lock=None):
    by_date={str(x.get('date')):x for x in history if isinstance(x,dict) and x.get('date')}
    day=by_date.get(date_text,{'date':date_text,'races':[]})
    records={f"{x.get('venue_code')}:{x.get('race_no')}":x for x in day.get('races',[]) if isinstance(x,dict)}
    for r in races:
        res=r.get('result',{}) or {}
        if not (res.get('result_finished') and res.get('payout_available') and res.get('payout_3tan')):
            continue
        fp=r.get('frozen_prediction',{}) or {};bets=fp.get('bets',[]) or []
        try: payout=int(res.get('payout_3tan_yen',0) or 0)
        except Exception: payout=0
        if payout<=0: continue
        investment=sum(int(b.get('bet_yen',BET_UNIT) or BET_UNIT) for b in bets)
        winning=str(res.get('payout_3tan'))
        hit=any(str(b.get('ticket'))==winning for b in bets)
        records[f"{r.get('venue_code')}:{r.get('race_no')}"]={
            'venue_code':str(r.get('venue_code')),'venue_name':r.get('venue_name',''),'race_no':int(r.get('race_no',0)),
            'prediction_created_at':fp.get('created_at',''),'model':(fp.get('verdict',{}) or {}).get('model','v6.14 legacy'),
            'rank':(fp.get('verdict',{}) or {}).get('rank','C'),'bets':[{'ticket':b.get('ticket'),'bet_yen':int(b.get('bet_yen',BET_UNIT) or BET_UNIT),'odds':b.get('odds')} for b in bets],
            'result_ticket':winning,'payout_yen':payout,'investment_yen':investment,'hit':hit,
            'return_yen':payout if hit else 0,'profit_yen':(payout if hit else 0)-investment,
            'odds_available':bool((r.get('odds',{}) or {}).get('available'))}
    day['races']=sorted(records.values(),key=lambda x:(str(x.get('venue_code')),int(x.get('race_no',0))))
    if strategy_lock is not None:
        # Keep the original locked tickets; only settlement fields may advance.
        previous=day.get('strategy_lock')
        if previous and previous.get('selected'):
            original={(str(x.get('venue_code')),int(x.get('race_no',0))):x for x in previous.get('selected',[])}
            for item in strategy_lock.get('selected',[]):
                key=(str(item.get('venue_code')),int(item.get('race_no',0)))
                if key in original:
                    original[key].update({k:item.get(k) for k in ('status','result_ticket','payout_yen','hit','return_yen','profit_yen')})
            previous['selected']=list(original.values())
            previous['settlement_summary']=strategy_lock.get('settlement_summary',{})
            day['strategy_lock']=previous
        else:
            day['strategy_lock']=strategy_lock
    by_date[date_text]=day
    # 180日分に制限してtoday.jsonが無制限に肥大化しないようにする。
    return [by_date[k] for k in sorted(by_date)[-180:]]

def summarize_performance_history(history):
    rows=[r for d in history for r in d.get('races',[]) if isinstance(r,dict)]
    inv=sum(int(r.get('investment_yen',0) or 0) for r in rows)
    pay=sum(int(r.get('return_yen',0) or 0) for r in rows)
    hits=sum(bool(r.get('hit')) for r in rows)
    return {'days':len(history),'settled_races':len(rows),'hits':hits,
            'hit_rate_pct':round(hits/len(rows)*100,1) if rows else 0,
            'investment_yen':inv,'return_yen':pay,'profit_yen':pay-inv,
            'roi_pct':round(pay/inv*100,1) if inv else 0}

def load_existing():
    try:
        with open('data/today.json',encoding='utf-8') as f:d=json.load(f)
        return d if d.get('target_date')==TODAY_DISPLAY else {}
    except:return {}
def freeze_prediction(race,old):
    if old and old.get('frozen_prediction'):
        fp=old['frozen_prediction']
        race['frozen_prediction']=fp
        # UI/summary compatibility: keep the frozen AI visible in the normal fields too.
        race['ai']={'main':fp.get('main'),'opponent':fp.get('opponent'),'dark_horse':fp.get('dark_horse'),'ranking':fp.get('ranking',[])}
        race['verdict']=fp.get('verdict',{})
        race['bets']=fp.get('bets',[])
        return
    race['frozen_prediction']={'created_at':now_jst().isoformat(),'main':race['ai']['main'],'opponent':race['ai']['opponent'],'dark_horse':race['ai']['dark_horse'],'verdict':race['verdict'],'ranking':race['ai']['ranking'],'bets':race['bets']}

def old_race_ready_for_reuse(r):
    riders = r.get('riders', [])

    return (
        isinstance(riders, list)
        and 5 <= len(riders) <= 9
        and bool(r.get('venue_code'))
        and int(r.get('race_no', 0)) > 0
    )

def merge_existing_race(oldrace):
    # Reuse the already downloaded race/prediction data. This is the main v6.7 speed-up.
    r=json.loads(json.dumps(oldrace,ensure_ascii=False))
    r.pop('settlement',None)
    return r
def calculate_summary(races):
    by_rank={k:{'races':0,'investment':0,'payout':0,'profit':0,'hits':0} for k in 'SABC'};by_venue={};inv=pay=hits=settled=pending=0
    for r in races:
        s=r.get('settlement',{});rank=r.get('frozen_prediction',{}).get('verdict',{}).get('rank','C');rank=rank if rank in by_rank else 'C'
        if s.get('status')!='settled':pending+=1;continue
        settled+=1;i=int(s.get('investment',0));p=int(s.get('payout',0));h=bool(s.get('hit'));inv+=i;pay+=p;hits+=h
        x=by_rank[rank];x['races']+=1;x['investment']+=i;x['payout']+=p;x['profit']+=p-i;x['hits']+=h
        v=r.get('venue_name','');x=by_venue.setdefault(v,{'races':0,'investment':0,'payout':0,'profit':0,'hits':0});x['races']+=1;x['investment']+=i;x['payout']+=p;x['profit']+=p-i;x['hits']+=h
    for x in list(by_rank.values())+list(by_venue.values()):x['hit_rate']=round(x['hits']/x['races']*100,1) if x['races'] else 0;x['roi']=round(x['payout']/x['investment']*100,1) if x['investment'] else 0
    return {'races':len(races),'settled_races':settled,'pending_races':pending,'investment':inv,'payout':pay,'profit':pay-inv,'hits':hits,'hit_rate':round(hits/settled*100,1) if settled else 0,'roi':round(pay/inv*100,1) if inv else 0,'by_rank':by_rank,'by_venue':by_venue}
def main():
    started = time.time()
    print(
        f'==============================\n'
        f' KEIRIN AI DATA UPDATE v{VERSION}\n'
        f'==============================\n'
        f'対象日: {TODAY_DISPLAY}\n'
        f'=============================='
    )

    history = load_performance_history()
    existing = load_existing()
    old = {
        (str(r.get('venue_code')), int(r.get('race_no', 0))): r
        for r in existing.get('races', [])
    }

    if old:
        print(
            f'既存データ: {len(old)}レース\n'
            '凍結済みAI予想を保護します'
        )

    all_races = []
    venues = []

    print('開催場を確認中...')
    discovered = discover_today_venues()

    if not discovered:
        print('警告: 開催場を検出できません。既存設定を使用します')
        discovered = [
            {'code': c, 'name': n, 'slug': s}
            for c, (n, s) in VENUES.items()
        ]

    old_by_venue = {}
    for rr in old.values():
        if old_race_ready_for_reuse(rr):
            old_by_venue.setdefault(
                str(rr['venue_code']), []
            ).append(rr)

    for v in discovered:
        code = str(v['code'])
        name = v['name']
        slug = v['slug']

        detected = discover_real_races(v)

        detected_numbers = {
            int(r['race_no']) for r in detected
        }

        existing_races = {
            int(r['race_no']): r
            for r in old_by_venue.get(code, [])
        }

        # 新規開催なのにレースが1つも検出されなければ除外。
        # 既存データがある場合は、誤って消さないように保持する。
        if not detected and not existing_races:
            print(f'  {code} {name}: レース未検出のため除外')
            continue


        merged = []

        for fresh in detected:
            race_no = int(fresh['race_no'])

            if race_no in existing_races:
                previous = merge_existing_race(
                    existing_races[race_no]
                )
                previous.update({
                    'venue_code': code,
                    'venue_name': name,
                    'venue_slug': slug,
                    'race_no': race_no,
                    'url': fresh.get('url') or previous.get('url', ''),
                    'start_time': (
                        fresh.get('start_time')
                        or previous.get('start_time', '')
                    ),
                })
                merged.append(previous)
            else:
                merged.append(fresh)

        for race_no, previous in existing_races.items():
            if race_no not in detected_numbers:
                merged.append(merge_existing_race(previous))

        merged.sort(key=lambda r: int(r.get('race_no', 0)))

        v['race_numbers'] = [
            int(r['race_no']) for r in merged
        ]
        venues.append(v)
        all_races.extend(merged)

        print(f'  {code} {name}: {len(merged)}レース')

    unique = {}
    for race in all_races:
        key = (
            str(race.get('venue_code', '')),
            int(race.get('race_no', 0))
        )
        if key[0] and key[1] > 0:
            unique[key] = race

    all_races = sorted(
        unique.values(),
        key=lambda r: (
            int(r.get('venue_code', 0)),
            int(r.get('race_no', 0))
        )
    )

    print(f'開催場数: {len(venues)}')
    print(f'検出レース数: {len(all_races)}')
    # In v6.8 this means races present in today's dataset, not HTTP requests.
    # Existing race pages are reused; only missing/incomplete records are     
    detail_targets = [
        r for r in all_races
        if (
            not (5 <= len(r.get('riders', [])) <= 9)
            or not r.get('url')
            or str(r.get('venue_code')) == '21'
            or sorted(
                x.get('car_no')
                for x in r.get('riders', [])
                if isinstance(x.get('car_no'), int)
            ) != list(range(1, len(r.get('riders', [])) + 1))
        )
    ]
    print(f'詳細取得対象レース: {len(detail_targets)}/{len(all_races)}レース')
    if detail_targets:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fs = {
                ex.submit(
                    fetch_race,
                    {
                        'venue_code': r['venue_code'],
                        'venue_name': r['venue_name'],
                        'venue_slug': r['venue_slug'],
                        'race_no': r['race_no'],
                    }
                ): r
                for r in detail_targets
            }

            for f in as_completed(fs):
                r = fs[f]

                try:
                    fresh = f.result()
                except Exception as e:
                    print(
                        f'選手データ取得エラー: '
                        f'{r.get("venue_name", "")} '
                        f'{r.get("race_no", "")}R: {e}'
                    )
                    continue

                new_riders = fresh.get('riders', [])
                old_riders = r.get('riders', [])

                new_cars = sorted(
                    x.get('car_no')
                    for x in new_riders
                    if isinstance(x.get('car_no'), int)
                )

                old_cars = sorted(
                    x.get('car_no')
                    for x in old_riders
                    if isinstance(x.get('car_no'), int)
                )

                new_valid = (
                    5 <= len(new_riders) <= 9
                    and len(new_cars) == len(set(new_cars))
                    and new_cars == list(range(1, len(new_riders) + 1))
                    and all(x.get('name') for x in new_riders)
                )

                old_valid = (
                    5 <= len(old_riders) <= 9
                    and len(old_cars) == len(set(old_cars))
                    and old_cars == list(range(1, len(old_riders) + 1))
                    and all(x.get('name') for x in old_riders)
                )

                if new_valid and (
                    not old_valid
                    or len(new_riders) >= len(old_riders)
                ):
                    # 取得した選手データだけ更新する。
                    # 既存の凍結済み予想や確定済み結果は変更しない。
                    r.update({
                        'riders': new_riders,
                        'url': fresh.get('url') or r.get('url', ''),
                        'start_time': (
                            fresh.get('start_time')
                            or r.get('start_time', '')
                        ),
                        'odds_url': (
                            fresh.get('odds_url')
                            or r.get('odds_url', '')
                        ),
                        'result_url': (
                            fresh.get('result_url')
                            or r.get('result_url', '')
                        ),
                    })

                    print(
                        f'選手データ更新: '
                        f'{r.get("venue_name", "")} '
                        f'{r.get("race_no", "")}R '
                        f'{len(new_riders)}人 '
                        f'車番={new_cars}'
                    )

                else:
                    print(
                        f'選手データ維持: '
                        f'{r.get("venue_name", "")} '
                        f'{r.get("race_no", "")}R '
                        f'既存{len(old_riders)}人 / '
                        f'新規取得{len(new_riders)}人 '
                        f'新規車番={new_cars}'
                    )

    print(
        f'選手データ取得結果: '
        f'{sum(5 <= len(r.get("riders", [])) <= 9 for r in all_races)}'
        f'/{len(all_races)}'
    )

    # Only fetch prediction pages when frozen prediction data is missing/incomplete.
    prediction_targets=[]
    for r in all_races:
        oldrace=old.get((str(r['venue_code']),int(r['race_no'])),{})
        fp=oldrace.get('frozen_prediction')
        has_prediction=bool(r.get('prediction_url') and (r.get('comments') or r.get('line') or r.get('development')))
        if not fp or not has_prediction:
            prediction_targets.append(r)
    print(f'コメント・並び・展開情報: {len(prediction_targets)}/{len(all_races)}レースを取得（既存データ再利用あり）')
    if prediction_targets:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fs={ex.submit(fetch_prediction,r):r for r in prediction_targets}
            for f in as_completed(fs):
                r=fs[f]
                try:p=f.result()
                except:p={'comments':{},'line':[],'line_groups':[],'development':'','url':''}
                r.update({'comments':p['comments'],'line':p['line'],'line_groups':p['line_groups'],'development':p['development'],'prediction_url':p['url']})
    for r in all_races:
        oldrace=old.get((str(r['venue_code']),int(r['race_no'])),{})
        # If a legacy record lacks comments, preserve whatever we already have rather than wiping it.
        for x in r.get('riders',[]):
            i=r.get('comments',{}).get(x['car_no'],{})
            if i:
                x['prediction_mark']=i.get('mark','');x['prediction_score']=i.get('prediction_score',0);x['comment']=i.get('comment','')
        if oldrace.get('frozen_prediction'):
            freeze_prediction(r,oldrace)
        else:
            apply_ai(r);freeze_prediction(r,oldrace)

    # v6.16: lock the daily strategy before fetching today's results. Never create it
    # retroactively for a same-day dataset that already existed without a lock.
    daily_strategy_lock=existing.get('daily_strategy_lock') if isinstance(existing,dict) else None
    if not daily_strategy_lock:
        if not old:
            daily_strategy_lock=create_daily_strategy_lock(all_races,TODAY_DISPLAY,now_jst())
            print(f"発走前戦略を固定: {daily_strategy_lock.get('selected_count',0)}R / 時刻確認済みAランク候補{daily_strategy_lock.get('eligible_count',0)}R")
        else:
            daily_strategy_lock={'date':TODAY_DISPLAY,'status':'legacy_no_lock','locked_at':'',
                                 'selected':[],'selected_count':0,
                                 'note':'同日データが既に存在したため、結果確認後の後付け選定を防止。翌開催日から発走前固定を開始。'}
            print('発走前戦略: 既存日の後付け選定を防止（翌開催日から記録）')

    print('レース結果を確認中...');result_count=payout_count=0;result_pending=[];result_failed=[];payout_pending=[];payout_failed=[]
    result_targets=[]
    for r in all_races:
        oldres=old.get((str(r['venue_code']),int(r['race_no'])),{}).get('result',{})
        # A race with both result and payout already confirmed never needs to be fetched again.
        if not (oldres.get('result_finished') and oldres.get('payout_available')):
            result_targets.append(r)
        else:
            r['result']=oldres
    print(f'結果更新対象: {len(result_targets)}/{len(all_races)}レース（確定済みは再取得しません）')
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fs={ex.submit(fetch_result,r):r for r in result_targets}
        for f in as_completed(fs):
            r=fs[f];key=(str(r['venue_code']),r['race_no']);oldres=old.get(key,{}).get('result',{})
            try:res=f.result()
            except:res={'result_status':'error','payout_status':'error','result_finished':False,'payout_available':False,'finish':[],'payout_3tan':None,'payout_3tan_yen':0,'payout_source':''}
            if oldres.get('result_finished'):
                res['result_finished']=True;res['finish']=oldres.get('finish',res.get('finish',[]));res['result_status']='finished'
            if oldres.get('payout_available'):
                res['payout_available']=True;res['payout_3tan']=oldres.get('payout_3tan');res['payout_3tan_yen']=oldres.get('payout_3tan_yen',0);res['payout_source']=oldres.get('payout_source','');res['payout_status']='available'
            res['finished']=bool(res.get('result_finished') and res.get('payout_available'));r['result']=res
    # Recalculate status counts from the final race state, including reused confirmed results.
    for r in all_races:
        res=r.get('result',{})
        if res.get('result_finished'):result_count+=1
        elif res.get('result_status')=='error':result_failed.append(f'{r["venue_name"]} {r["race_no"]}R')
        else:result_pending.append(f'{r["venue_name"]} {r["race_no"]}R')
        if res.get('payout_available'):payout_count+=1
        elif res.get('payout_status')=='error':payout_failed.append(f'{r["venue_name"]} {r["race_no"]}R')
        else:payout_pending.append(f'{r["venue_name"]} {r["race_no"]}R')
    print(f'結果確定: {result_count}/{len(all_races)}');print(f'  結果待ち: {len(result_pending)}');print(f'  結果取得エラー: {len(result_failed)}');print(f'払戻確定: {payout_count}/{len(all_races)}');print(f'  払戻待ち: {len(payout_pending)}');print(f'  払戻取得エラー: {len(payout_failed)}')

    print('オッズ情報を確認中...');odds_count=0;odds_failed=[]
    odds_targets=[]
    for r in all_races:
        oldod=old.get((str(r['venue_code']),int(r['race_no'])),{}).get('odds',{})
        if oldod.get('available') and oldod.get('odds'):
            r['odds']=oldod
        else:
            odds_targets.append(r)
    print(f'オッズ更新対象: {len(odds_targets)}/{len(all_races)}レース（取得済みは再取得しません）')
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fs={ex.submit(fetch_odds,r):r for r in odds_targets}
        for f in as_completed(fs):
            r=fs[f]
            try:o=f.result()
            except:o={'available':False,'odds':{}}
            r['odds']=o
    for r in all_races:
        if r.get('odds',{}).get('available'):odds_count+=1
        else:odds_failed.append(f'{r["venue_name"]} {r["race_no"]}R')
    print(f'オッズ取得: {odds_count}/{len(all_races)}')
    for r in all_races:attach_bet_odds(r);r['settlement']=settle_bets(r)
    backtests=calculate_backtests(all_races)
    backtest_recommendation=select_backtest_recommendation(backtests)
    daily_strategy=build_daily_strategy_report(all_races)
    daily_strategy['is_hindsight_backtest']=True
    daily_strategy['note']='確定結果を使って選び直す参考バックテストです。発走前の固定予想・将来成績として扱いません。'
    daily_strategy_lock=settle_daily_strategy_lock(daily_strategy_lock,all_races)
    history=update_performance_history(history,all_races,TODAY_DISPLAY,daily_strategy_lock)
    history_summary=summarize_performance_history(history)
    locked_strategy_summary=summarize_locked_strategy_history(history)
    summary=calculate_summary(all_races);rider_count=sum(len(r['riders']) for r in all_races);correct_names=sum(bool(x.get('name')) for r in all_races for x in r['riders']);frozen=sum(bool(r.get('frozen_prediction')) for r in all_races)
    output={'version':VERSION,'performance_history':history,'performance_history_summary':history_summary,'locked_strategy_summary':locked_strategy_summary,'daily_strategy_lock':daily_strategy_lock,'updated_at':now_jst().isoformat(),'target_date':TODAY_DISPLAY,'data_complete':bool(all_races and correct_names==rider_count and frozen==len(all_races)),'result_complete':result_count==len(all_races),'payout_complete':payout_count==len(all_races),'venue_count':len(venues),'race_count':len(all_races),'rider_count':rider_count,'correct_name_count':correct_names,'prediction_race_count':sum(len(r.get('comments',{}))==len(r['riders']) and len(r['riders'])>=5 for r in all_races),'comment_count':sum(len(r.get('comments',{})) for r in all_races),'line_race_count':sum(len(r.get('line',[]))==len(r['riders']) and len(r['riders'])>=5 for r in all_races),'development_count':sum(bool(r.get('development')) for r in all_races),'ai_race_count':sum(bool(r.get('ai')) for r in all_races),'ai_rider_count':sum(x.get('ai_score') is not None for r in all_races for x in r['riders']),'frozen_prediction_count':frozen,'result_count':result_count,'result_pending_count':len(result_pending),'result_error_count':len(result_failed),'payout_count':payout_count,'payout_pending_count':len(payout_pending),'payout_error_count':len(payout_failed),'odds_count':odds_count,'summary':summary,'backtests':backtests,'backtest_recommendation':backtest_recommendation,'daily_strategy':daily_strategy,'venues':[{'venue_code':v['code'],'venue_name':v['name'],'race_count':len(v['race_numbers']),'race_numbers':v['race_numbers']} for v in venues],'races':all_races}
    os.makedirs('data',exist_ok=True);tmp='data/today.json.tmp'
    with open(tmp,'w',encoding='utf-8') as f:json.dump(output,f,ensure_ascii=False,indent=2)
    os.replace(tmp,'data/today.json')
    print('==============================\n 【AI収支】\n==============================');print(f"確定レース: {summary['settled_races']}");print(f"未確定レース: {summary['pending_races']}");print(f"投資額: ¥{summary['investment']:,}");print(f"払戻: ¥{summary['payout']:,}");print(f"収支: {summary['profit']:+,}円");print(f"的中レース: {summary['hits']}");print(f"的中率: {summary['hit_rate']:.1f}%");print(f"回収率: {summary['roi']:.1f}%")
    print('==============================\n 【S/A/B/C別収支】\n==============================')
    for k in 'SABC':
        s=summary['by_rank'][k];print(f"{k}: {s['races']}R / 的中{s['hits']}R / 的中率{s['hit_rate']:.1f}% / 投資¥{s['investment']:,} / 払戻¥{s['payout']:,} / 収支{s['profit']:+,}円 / 回収率{s['roi']:.1f}%")
    print('==============================\n 【買い方バックテスト】\n==============================')
    for name,b in backtests.items():
        print(f"{name}: {b['races']}R / 的中{b['hits']}R / 的中率{b['hit_rate']:.1f}% / 投資¥{b['investment']:,} / 払戻¥{b['payout']:,} / 収支{b['profit']:+,}円 / 回収率{b['roi']:.1f}% / プラスR率{b.get('positive_rate',0):.1f}%")
    print('--- 暫定おすすめ ---')
    br=backtest_recommendation
    if br.get('strategy'):
        print(f"推奨買い方: {br['strategy']} / 収支{br['profit']:+,}円 / 回収率{br['roi']:.1f}% / 的中率{br['hit_rate']:.1f}% / 対象{br['races']}R")
        print(br.get('note',''))
    else:
        print(br.get('reason','データ不足'))
    print('==============================\n 【過去結果による参考バックテスト（実運用推奨ではない）】\n==============================')
    op=daily_strategy.get('operating',{})
    print(f"運用設定: Aランク上位{DAILY_MAX_RACES}R × AI{DAILY_BET_COUNT}点 / 予算上限¥{DAILY_BUDGET:,}")
    print(f"候補{op.get('candidate_count',0)}R → 推奨{op.get('selected_count',0)}R / 確定{op.get('settled_count',0)}R")
    print(f"投資¥{op.get('investment',0):,} / 払戻¥{op.get('payout',0):,} / 収支{op.get('profit',0):+,}円 / 回収率{op.get('roi',0):.1f}% / 的中率{op.get('hit_rate',0):.1f}%")
    print('--- 結果判明後に選ばれた参考候補（発走前予想ではない）---')
    for x in op.get('selected',[]):
        print(f"  {x['venue_name']} {x['race_no']}R / {x['rank']} / AIスコア{x['verdict_score']:.0f} / 選定スコア{x['selection_score']:.1f} / 5点¥{x['investment']:,}")
    print('--- 1日上限比較 ---')
    for name,x in daily_strategy.get('tests',{}).items():
        print(f"{name}: {x.get('settled_count',0)}R / 的中{x.get('hits',0)}R / 投資¥{x.get('investment',0):,} / 払戻¥{x.get('payout',0):,} / 収支{x.get('profit',0):+,}円 / 回収率{x.get('roi',0):.1f}%")
    print(f"参考バックテスト上の最良（後付け選定）: {daily_strategy.get('best_name') or 'データ不足'}")
    print('==============================');print(' 【発走前固定戦略・結果検証】');print(f"固定状態: {daily_strategy_lock.get('status','unknown')} / 選定{daily_strategy_lock.get('selected_count',0)}R");print(f"今回までの固定戦略: {locked_strategy_summary['settled_races']}確定R / 的中{locked_strategy_summary['hits']}R / 投資¥{locked_strategy_summary['investment_yen']:,} / 払戻¥{locked_strategy_summary['return_yen']:,} / 収支{locked_strategy_summary['profit_yen']:+,}円 / 回収率{locked_strategy_summary['roi_pct']:.1f}%");print(f"累積履歴: {history_summary['days']}日 / {history_summary['settled_races']}確定R / 累積収支 {history_summary['profit_yen']:+,}円 / 回収率 {history_summary['roi_pct']:.1f}%");print(f'結果待ちレース: {len(result_pending)}');print(f'結果取得エラー: {len(result_failed)}');print(f'払戻待ちレース: {len(payout_pending)}');print(f'払戻取得エラー: {len(payout_failed)}');print(f'オッズ未取得レース: {len(odds_failed)}');print(f'処理時間: {time.time()-started:.1f}秒');print('データ完全性: '+('OK' if output['data_complete'] else '要確認'));print(f'結果完全性: {result_count}/{len(all_races)}');print(f'払戻完全性: {payout_count}/{len(all_races)}')
    if result_pending:print('結果待ち: '+', '.join(result_pending))
    if result_failed:print('結果取得エラー: '+', '.join(result_failed))
    if payout_pending:print('払戻待ち: '+', '.join(payout_pending))
    if payout_failed:print('払戻取得エラー: '+', '.join(payout_failed))
    print('==============================\n UPDATE COMPLETE\n==============================')

if __name__=='__main__':main()
