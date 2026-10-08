import json, os, re, time, warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

VERSION='6.10'
BASE='https://www.oddspark.com'; SP='https://sp.oddspark.com'
TODAY=datetime.now().strftime('%Y%m%d'); TODAY_DISPLAY=datetime.now().strftime('%Y-%m-%d')
TIMEOUT=15; RETRIES=1; MAX_WORKERS=8; BET_UNIT=100; MAX_BETS=10
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36','Accept-Language':'ja-JP,ja;q=0.9,en;q=0.8'}
VENUES={'22':('前橋','maebashi'),'25':('大宮','omiya'),'35':('平塚','hiratsuka'),'38':('静岡','shizuoka'),'44':('大垣','ogaki'),'48':('四日市','yokkaichi'),'74':('高知','kochi')}
MARK_SCORE={'◎':18,'○':12,'▲':8,'△':5,'×':1,'注':3}
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
def result_url(c,n):return f'{SP}/keirin/SpRaceResultInfo.do?joCd={c}&joCode={c}&kaisaiBi={TODAY}&raceNo={n}'

def parse_rider_row(tr):
    text=clean(tr.get_text(' ',strip=True)); mcar=re.search(r'\b([1-7])\b',text)
    if not mcar:return None
    car=int(mcar.group(1)); name=''
    for a in tr.find_all('a'):
        h=a.get('href',''); lab=clean(a.get_text(' ',strip=True))
        if lab and ('PlayerDetail.do' in h or 'player' in h.lower()) and lab not in MARK_SCORE:name=lab;break
    cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'])]
    if not name:
        for i,v in enumerate(cells):
            if v==str(car):
                for x in cells[i+1:i+6]:
                    if x and x not in MARK_SCORE and not re.fullmatch(r'[1-7]|\d+',x) and re.search(r'[一-龯ぁ-んァ-ヶ]',x) and not any(q in x for q in ('競走得点','着順','決まり手','今場所','前場所')):name=x;break
            if name:break
    if not name:return None
    m=re.search(r'(\d{1,2})歳\s*[／/]\s*(\d{2,3})期',text); age=int(m.group(1)) if m else None; period=int(m.group(2)) if m else None
    pref=next((p for p in sorted(PREFECTURES,key=len,reverse=True) if p in text),'')
    mg=re.search(r'\b([SAL]\d)\b',text); grade=mg.group(1) if mg else ''
    style=next((s for s in ('逃','捲','追','両') if re.search(rf'\s{s}\s*[|｜ ]',text)),'')
    ms=re.search(r'競走得点\s*[:：]?\s*(\d+(?:\.\d+)?)',text); score=float(ms.group(1)) if ms else None
    mf=re.search(r'着\s*順\s*[:：]?\s*([^|｜]+)',text); f=numbers4(mf.group(1) if mf else '')
    mk=re.search(r'決まり手\s*[:：]?\s*([^|｜]+)',text); k=numbers4(mk.group(1) if mk else '')
    return {'car_no':car,'name':name,'age':age,'period':period,'prefecture':pref,'grade':grade,'style':style,'score':score,'finish_1':f[0],'finish_2':f[1],'finish_3':f[2],'finish_out':f[3],'kimari_nige':k[0],'kimari_makuri':k[1],'kimari_sashi':k[2],'kimari_mark':k[3],'comment':'','prediction_mark':'','prediction_score':0,'ai_score':0}
def parse_riders(sp):
    best={}
    if not sp:return []
    for tr in sp.find_all('tr'):
        r=parse_rider_row(tr)
        if r:best[r['car_no']]=r
    return [best[k] for k in sorted(best)] if 5<=len(best)<=7 else []
def discover_related(sp):
    out={'odds_url':'','result_url':''}
    if not sp:return out
    for a in sp.find_all('a'):
        txt=clean(a.get_text(' ',strip=True)); h=a.get('href',''); u=absolute(h); low=(txt+' '+h).lower()
        if not out['odds_url'] and ('オッズ' in txt or 'oddsinfo' in low or 'odds' in low):out['odds_url']=u
        if not out['result_url'] and ('結果' in txt or 'raceresult' in low or 'resultinfo' in low):out['result_url']=u
    return out
def fetch_race(job):
    u=race_url(job['venue_code'],job['race_no']); sp=soup(get_html(u)); rel=discover_related(sp); riders=parse_riders(sp)
    return {**job,'url':u,'riders':riders,**rel,'success':5<=len(riders)<=7}
def discover_real_races(v):
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        rs=[f.result() for f in as_completed([ex.submit(fetch_race,{'venue_code':v['code'],'venue_name':v['name'],'venue_slug':v['slug'],'race_no':n}) for n in range(1,13)])]
    return sorted([r for r in rs if r['success']],key=lambda x:x['race_no'])

def parse_prediction_table(sp):
    if not sp:return {}
    result={}
    for tr in sp.find_all('tr'):
        cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'])]; cars=[int(v) for v in cells if re.fullmatch(r'[1-7]',v)]
        if not cars:continue
        car=cars[0]; mark=next((v for v in cells if v in MARK_SCORE),''); comments=[v for v in cells if v and v not in (str(car),mark) and v not in MARK_SCORE and re.search(r'[ぁ-んァ-ヶ]',v) and not any(q in v for q in ('選手名','コメント','予想','並び'))]
        name=next((v for v in comments if re.search(r'[一-龯]',v) and len(v)<=12),''); comment=' '.join(v for v in comments if v!=name)
        if name or comment or mark:result[car]={'name':name,'mark':mark,'comment':comment,'prediction_score':MARK_SCORE.get(mark,0)}
    return result if 5<=len(result)<=7 else {}
def valid_cars(c):return 5<=len(set(c))<=7 and set(c)<=set(range(1,8))
def parse_line(sp):
    if not sp:return [],[]
    for table in sp.find_all('table'):
        for tr in table.find_all('tr'):
            cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'])]; cars=list(dict.fromkeys(int(x) for x in cells if re.fullmatch(r'[1-7]',x)))
            if valid_cars(cars) and any(w in ' '.join(cells) for w in STYLE_WORDS):return cars,[cars]
    lines=[clean(x) for x in sp.get_text('\n',strip=True).splitlines() if clean(x)]
    for i,line in enumerate(lines):
        nums=list(dict.fromkeys(int(x) for x in re.findall(r'(?<!\d)([1-7])(?!\d)',line))); nearby=' '.join(lines[max(0,i-2):i+3])
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

def calculate_ai_score(r,race):
    s=max(0,min(30,((r.get('score') or 70)-70)*1.2)); s+=(r.get('finish_1') or 0)*1.8+(r.get('finish_2') or 0)*.9+(r.get('finish_3') or 0)*.4+(r.get('kimari_nige') or 0)*.8+(r.get('kimari_makuri') or 0)+(r.get('kimari_sashi') or 0)+r.get('prediction_score',0)
    line=race.get('line',[]); c=r['car_no']
    if c in line:s+=4 if line.index(c)==1 else 2
    return round(s,2)
def calculate_verdict(race):
    rs=sorted(race.get('riders',[]),key=lambda x:(x.get('ai_score',0),x.get('score') or 0),reverse=True)
    if not rs:return {'rank':'C','label':'⚠️ 見送り','score':0,'reason':'AI評価対象データ不足'}
    a,b,c=rs[0].get('ai_score',0),rs[1].get('ai_score',0) if len(rs)>1 else rs[0].get('ai_score',0),rs[2].get('ai_score',0) if len(rs)>2 else rs[-1].get('ai_score',0)
    v=min(100,int(50+min(20,max(0,(a-b)*2))+(8 if b-c>=5 else 0)+(8 if len(race.get('line',[]))==len(rs) else 0)+(5 if race.get('development') else 0)+(4 if race.get('prediction_url') else 0)))
    rank='S' if v>=82 else 'A' if v>=72 else 'B' if v>=61 else 'C'; label={'S':'🔥 勝負','A':'🎯 買い','B':'⭐ 少額','C':'⚠️ 見送り'}[rank]
    return {'rank':rank,'label':label,'score':v,'reason':'本命の優位性・ライン・展開を総合判断'}
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
        if tk:out[tk]=max(out.get(tk,{'score':-1}),{'ticket':tk,'score':sc+sum(2 for x in (a,b,c) if x in line),'type':lab,'bet_yen':BET_UNIT},key=lambda x:x['score'])
    return sorted(out.values(),key=lambda x:x['score'],reverse=True)[:limit]
def apply_ai(race):
    for r in race['riders']:r['ai_score']=calculate_ai_score(r,race)
    rank=sorted(race['riders'],key=lambda x:(x.get('ai_score',0),x.get('score') or 0),reverse=True)
    race['ai']={'main':rank[0]['car_no'],'opponent':rank[1]['car_no'],'dark_horse':rank[2]['car_no'],'ranking':[{'car_no':r['car_no'],'name':r['name'],'score':r['ai_score'],'mark':r.get('prediction_mark','')} for r in rank]}
    race['verdict']=calculate_verdict(race); race['bets']=generate_bets(race)

def parse_ticket_text(t):
    m=re.search(r'([1-7])\s*[→＞>\-−]\s*([1-7])\s*[→＞>\-−]\s*([1-7])',clean(t)); return f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else None
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

def parse_result_page(html):
    empty={'finished':False,'result_finished':False,'finish':[],'result_status':'pending','payout_available':False,'payout_3tan':None,'payout_3tan_yen':0,'payout_source':'','payout_status':'pending'}
    sp=soup(html)
    if not sp:
        empty['result_status']='error';empty['payout_status']='error';return empty
    finish_map={}
    for table in sp.find_all('table'):
        rows=table.find_all('tr'); header_idx=None;pos_idx=None;car_idx=None
        for ri,tr in enumerate(rows[:8]):
            cells=[clean(c.get_text(' ',strip=True)).replace(' ','') for c in tr.find_all(['th','td'])]
            for i,v in enumerate(cells):
                if '着順' in v and pos_idx is None:pos_idx=i
                if '車番' in v and car_idx is None:car_idx=i
            if pos_idx is not None and car_idx is not None:header_idx=ri;break
        if header_idx is None:continue
        for tr in rows[header_idx+1:]:
            cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'])]
            if not cells or max(pos_idx,car_idx)>=len(cells):continue
            pm=re.search(r'(?<!\d)([1-9])(?!\d)',cells[pos_idx]);cm=re.search(r'(?<!\d)([1-9])(?!\d)',cells[car_idx])
            if pm and cm:
                pos=int(pm.group(1));car=int(cm.group(1))
                if 1<=pos<=9 and 1<=car<=7:finish_map[pos]=car
    finish=[finish_map[p] for p in sorted(finish_map) if 1<=p<=9]
    text=clean(sp.get_text(' ',strip=True));tk,y,src=payout_scan(sp,text)
    if not tk and y and len(finish)>=3:tk='-'.join(map(str,finish[:3]))
    rf=len(finish)>=3;pa=bool(tk and y>0)
    if rf:empty['result_status']='finished'
    if pa:empty['payout_status']='available'
    empty.update({'finished':rf and pa,'result_finished':rf,'finish':finish,'payout_available':pa,'payout_3tan':tk,'payout_3tan_yen':y,'payout_source':src})
    return empty

def fetch_result(race):
    # v6.8: use one canonical result URL first.  The old implementation tried
    # up to three URLs for every pending race, which was the main runtime cost.
    # Fallback is used only when the primary URL cannot be downloaded at all.
    primary=race.get('result_url','') or result_url(race['venue_code'],race['race_no'])
    fallback=f"{BASE}/keirin/RaceResultInfo.do?joCode={race['venue_code']}&kaisaiBi={TODAY}&raceNo={race['race_no']}"
    urls=[]
    for u in (primary, result_url(race['venue_code'],race['race_no']), fallback):
        if u and u not in urls: urls.append(u)
    first_url=urls[0] if urls else result_url(race['venue_code'],race['race_no'])
    html=get_html(first_url)
    if html:
        res=parse_result_page(html);res['url']=first_url
        # A valid result page is authoritative even when the race/payout is not
        # ready yet. Do not waste requests trying alternate URLs.
        if res.get('result_finished') or res.get('payout_available') or res.get('result_status')=='finished' or res.get('payout_status')=='available':
            return res
        return res
    # Only a download failure triggers one fallback request.
    for u in urls[1:]:
        html=get_html(u)
        if not html: continue
        res=parse_result_page(html);res['url']=u
        if res.get('result_finished') or res.get('payout_available'):
            return res
        # Keep the first successfully loaded pending page; there is no need to
        # try another URL when the site is reachable.
        res['result_status']=res.get('result_status') or 'pending'
        res['payout_status']=res.get('payout_status') or 'pending'
        return res
    return {'result_finished':False,'finished':False,'finish':[],'result_status':'error','payout_available':False,'payout_3tan':None,'payout_3tan_yen':0,'payout_source':'','payout_status':'error'}

def parse_odds_page(html):
    sp=soup(html);odds={}
    if not sp:return {'available':False,'odds':{}}
    text=clean(sp.get_text(' ',strip=True))
    for m in re.finditer(r'([1-7])\s*[→＞>\-−]\s*([1-7])\s*[→＞>\-−]\s*([1-7])\s+(\d+(?:\.\d+)?)',text):odds[f'{m.group(1)}-{m.group(2)}-{m.group(3)}']=float(m.group(4))
    return {'available':bool(odds),'odds':odds}
def fetch_odds(race):
    for u in [race.get('odds_url',''),odds_url(race['venue_code'],race['race_no'])]:
        if u:
            p=parse_odds_page(get_html(u))
            if p['available']:p['url']=u;return p
    return {'available':False,'odds':{},'url':odds_url(race['venue_code'],race['race_no'])}
def attach_bet_odds(race):
    om=race.get('odds',{}).get('odds',{});bets=race.get('frozen_prediction',{}).get('bets',[]);rank={k:i for i,(k,v) in enumerate(sorted(om.items(),key=lambda x:x[1]),1)}
    for b in bets:
        v=om.get(b.get('ticket'));b['odds']=v;b['market_rank']=rank.get(b.get('ticket'));b['value_flag']='オッズ未取得' if v is None else ('💎 穴・期待値候補' if v>=30 else '🎯 中穴' if v>=15 else '⭐ 適正' if v>=8 else '⚠️ 人気')
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
    race['frozen_prediction']={'created_at':datetime.now().isoformat(),'main':race['ai']['main'],'opponent':race['ai']['opponent'],'dark_horse':race['ai']['dark_horse'],'verdict':race['verdict'],'ranking':race['ai']['ranking'],'bets':race['bets']}

def old_race_ready_for_reuse(r):
    return bool(r.get('riders')) and 5<=len(r.get('riders',[]))<=7 and bool(r.get('venue_code')) and int(r.get('race_no',0))>0

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
    started=time.time();print(f'==============================\n KEIRIN AI DATA UPDATE v{VERSION}\n==============================\n対象日: {TODAY_DISPLAY}\n==============================')
    existing=load_existing();old={(str(r.get('venue_code')),int(r.get('race_no',0))):r for r in existing.get('races',[])}
    if old:print(f'既存データ: {len(old)}レース\n凍結済みAI予想を保護します')
    all_races=[];venues=[];print('開催場を確認中...')
    # Existing same-day race data is stable, so reuse it instead of downloading every race page again.
    # This avoids dozens of unnecessary requests on every hourly run.
    if old and old.get('races'):
        old_by_venue={}
        for rr in old.get('races',[]):
            if old_race_ready_for_reuse(rr):
                old_by_venue.setdefault(str(rr['venue_code']),[]).append(rr)
        for c,(n,s) in VENUES.items():
            v={'code':c,'name':n,'slug':s}
            reused=sorted(old_by_venue.get(c,[]),key=lambda x:int(x.get('race_no',0)))
            v['race_numbers']=[int(x['race_no']) for x in reused]
            venues.append(v);all_races += [merge_existing_race(x) for x in reused]
            print(f'  {c} {n}: {len(reused)}レース（既存データ再利用）')
    else:
        for c,(n,s) in VENUES.items():
            v={'code':c,'name':n,'slug':s};venues.append(v);rs=discover_real_races(v);v['race_numbers']=[x['race_no'] for x in rs];print(f'  {c} {n}: {len(rs)}レース');all_races+=rs
    all_races.sort(key=lambda x:(int(x['venue_code']),x['race_no']))
    # In v6.8 this means races present in today's dataset, not HTTP requests.
    # Existing race pages are reused; only missing/incomplete records are fetched.
    detail_targets=[r for r in all_races if not (5<=len(r.get('riders',[]))<=7 and r.get('url'))]
    print(f'詳細取得対象レース: {len(detail_targets)}/{len(all_races)}レース')
    if detail_targets:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fs={ex.submit(fetch_race,{'venue_code':r['venue_code'],'venue_name':r['venue_name'],'venue_slug':r['venue_slug'],'race_no':r['race_no']}):r for r in detail_targets}
            for f in as_completed(fs):
                r=fs[f]
                try:r.update(f.result())
                except:pass
    print(f'選手データ取得結果: {sum(5<=len(r.get("riders",[]))<=7 for r in all_races)}/{len(all_races)}')

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
    summary=calculate_summary(all_races);rider_count=sum(len(r['riders']) for r in all_races);correct_names=sum(bool(x.get('name')) for r in all_races for x in r['riders']);frozen=sum(bool(r.get('frozen_prediction')) for r in all_races)
    output={'version':VERSION,'updated_at':datetime.now().isoformat(),'target_date':TODAY_DISPLAY,'data_complete':bool(all_races and correct_names==rider_count and frozen==len(all_races)),'result_complete':result_count==len(all_races),'payout_complete':payout_count==len(all_races),'venue_count':len(venues),'race_count':len(all_races),'rider_count':rider_count,'correct_name_count':correct_names,'prediction_race_count':sum(len(r.get('comments',{}))==len(r['riders']) and len(r['riders'])>=5 for r in all_races),'comment_count':sum(len(r.get('comments',{})) for r in all_races),'line_race_count':sum(len(r.get('line',[]))==len(r['riders']) and len(r['riders'])>=5 for r in all_races),'development_count':sum(bool(r.get('development')) for r in all_races),'ai_race_count':sum(bool(r.get('ai')) for r in all_races),'ai_rider_count':sum(x.get('ai_score') is not None for r in all_races for x in r['riders']),'frozen_prediction_count':frozen,'result_count':result_count,'result_pending_count':len(result_pending),'result_error_count':len(result_failed),'payout_count':payout_count,'payout_pending_count':len(payout_pending),'payout_error_count':len(payout_failed),'odds_count':odds_count,'summary':summary,'backtests':backtests,'backtest_recommendation':backtest_recommendation,'venues':[{'venue_code':v['code'],'venue_name':v['name'],'race_count':len(v['race_numbers']),'race_numbers':v['race_numbers']} for v in venues],'races':all_races}
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
    print('==============================');print(f'結果待ちレース: {len(result_pending)}');print(f'結果取得エラー: {len(result_failed)}');print(f'払戻待ちレース: {len(payout_pending)}');print(f'払戻取得エラー: {len(payout_failed)}');print(f'オッズ未取得レース: {len(odds_failed)}');print(f'処理時間: {time.time()-started:.1f}秒');print('データ完全性: '+('OK' if output['data_complete'] else '要確認'));print(f'結果完全性: {result_count}/{len(all_races)}');print(f'払戻完全性: {payout_count}/{len(all_races)}')
    if result_pending:print('結果待ち: '+', '.join(result_pending))
    if result_failed:print('結果取得エラー: '+', '.join(result_failed))
    if payout_pending:print('払戻待ち: '+', '.join(payout_pending))
    if payout_failed:print('払戻取得エラー: '+', '.join(payout_failed))
    print('==============================\n UPDATE COMPLETE\n==============================')

if __name__=='__main__':main()
