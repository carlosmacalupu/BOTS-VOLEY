# BOTS VÓLEY V1.07 — aplicación autónoma.
# Las fuentes van incluidas en este archivo, sin imports de archivos locales.
# Se preservan espacios de nombres independientes para evitar colisiones.
import sys as _sys
import types as _types

_embedded_official = r'''
"""Calendarios oficiales verificados, no noticias ni partidos inyectados.
Registro por competición: URL, año y huso de sede comprobados.
"""
from html.parser import HTMLParser
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
import time
import logging

URL = ('https://norceca.net/2026%20Competition%20&%20Activities/Pan%20American%20Cups/'
       'Women%20Pan%20American%20Cup/Calendar/Calendar-Senior%20Women%E2%80%99s%20Pan%20American%20Cup.htm')
TOURNAMENTS = [{'key':'panam-women-2026','url':URL,'year':2026,
                'name':'NORCECA Pan American Cup Women 2026', 'offset':-6}]
CACHE={}
MONTHS={m:i for i,m in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'],1)}

class Table(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.cells=[];self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.cells=[]
        if tag in ('td','th'):self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.cells.append(' '.join(' '.join(self.cell).split()));self.cell=None
        if tag=='tr' and self.cells:self.rows.append(self.cells)


def parse_norceca(html, config, now=None):
    import catalog as c
    import re
    now = time.time() if now is None else now
    table=Table();table.feed(html);matches=[]
    for row in table.rows:
        if len(row)<14 or not row[0].isdigit():continue
        try:
            d,mon=row[1].split('-');hour,minute=map(int,row[2].split(':'))
            ts=int(datetime(config['year'],MONTHS[mon.lower()[:3]],int(d),hour,minute,
                            tzinfo=timezone(timedelta(hours=config['offset']))).timestamp())
        except (ValueError,KeyError):continue
        home,away=row[4],row[6]
        if not home or not away or any(re.search(r'\b(winner|loser|tbd|ganador|perdedor)\b',x.lower()) for x in [home,away]):continue
        # The 'LIVE' column is only a hyperlink present even for completed games.
        score=re.fullmatch(r'([0-3])-([0-3])',row[8])
        hs,aws=map(int,score.groups()) if score else (None,None)
        final=hs is not None and max(hs,aws)==3 and min(hs,aws)<3
        state='FT' if final else 'NS' if ts>now else 'UNKNOWN'
        m=c.make_match('norceca',config['key']+'-'+row[0],ts,home,away,config['name'],state,
                       {'home':hs,'away':aws} if final else {})
        if not m:continue
        m['_official_url']=config['url'];m['_tournament_key']=config['key']
        m['_best_of']=5
        # Reject contradictory summaries; do not use these rows to fit a model.
        sets=[]
        for cell in row[9:14]:
            v=re.fullmatch(r'(\d+)-(\d+)',cell)
            if v and max(map(int,v.groups()))>0:sets.append(tuple(map(int,v.groups())))
        wins=[0,0];valid=True
        for n,(a,b) in enumerate(sets):
            target=15 if n==4 else 25
            if max(a,b)<target or abs(a-b)<2 or (max(a,b)>target and abs(a-b)!=2):valid=False
            wins[int(b>a)]+=1
        if final and (not valid or wins!=[hs,aws]):
            m['scores']={};m['_data_issue']='Marcador oficial contradictorio; pendiente de comprobar.'
        matches.append(m)
    return matches


def calendar(force=False):
    out=[];reasons=[]
    for cfg in TOURNAMENTS:
        key=cfg['key'];cached=CACHE.get(key)
        if not force and cached and time.time()-cached[0]<cached[3]:
            out.extend(cached[1]);reasons.append(cached[2]);continue
        try:
            req=Request(cfg['url'],headers={'User-Agent':'BOTS-VOLEY/1.07'})
            with urlopen(req,timeout=8) as response:
                raw=response.read(2_000_000)
            try:html=raw.decode('utf-8')
            except UnicodeDecodeError:html=raw.decode('cp1252')
            rows=parse_norceca(html,cfg)
            if not rows:raise ValueError('calendario no reconocido')
            reason='ok';ttl=120
        except Exception as exc:
            rows=[];reason=type(exc).__name__;ttl=30
            logging.getLogger('voley.official').warning('NORCECA: %s',reason)
        CACHE[key]=(time.time(),rows,reason,ttl)
        out.extend(rows);reasons.append(reason)
    return out,reasons

# Calendario oficial NCAA: esquema JSON-LD publicado por San Diego State.
SDSU_URL='https://goaztecs.com/sports/volleyball/schedule'
class JsonScripts(HTMLParser):
    def __init__(self):
        super().__init__();self.capture=False;self.buff=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('type')=='application/ld+json':
            self.capture=True;self.buff=[]
    def handle_data(self,data):
        if self.capture:self.buff.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.capture:
            self.scripts.append(''.join(self.buff));self.capture=False


def parse_sdsu(html,now=None):
    import json,re,hashlib
    import catalog as c
    now=time.time() if now is None else now
    parser=JsonScripts();parser.feed(html);out=[]
    def walk(obj):
        if isinstance(obj,list):
            for x in obj:walk(x)
        elif isinstance(obj,dict):
            if obj.get('@type') in ('Event','SportsEvent'):
                name=obj.get('name','')
                pair=re.fullmatch(r'SDSU\s+(vs\.?|at)\s+(.+)',name)
                raw=obj.get('startDate','')
                # Require an explicit timezone; never infer time from a date-only value.
                ts=c.timestamp(raw) if re.search(r'(Z|[+-]\d\d:\d\d)$',raw) else None
                if pair and ts:
                    rival=pair[2].strip()
                    if re.search(r'\b(tbd|championship|tournament|invitational)\b',rival,re.I):return
                    home,away=('San Diego State Aztecs',rival) if pair[1].startswith('vs') else (rival,'San Diego State Aztecs')
                    raw_state=obj.get('eventStatus','').split('/')[-1]
                    state={'EventCancelled':'CANC','EventPostponed':'PST'}.get(raw_state,'NS' if ts>now else 'UNKNOWN')
                    ident=hashlib.sha256(f'{home}|{away}|{ts}'.encode()).hexdigest()[:20]
                    m=c.make_match('ncaa_sdsu',ident,ts,home,away,'NCAA Women · San Diego State schedule',state)
                    m['_official_url']=SDSU_URL;m['_best_of']=5
                    out.append(m)
            for val in obj.values():
                if isinstance(val,(dict,list)):walk(val)
    for script in parser.scripts:
        try:walk(json.loads(script))
        except (ValueError,TypeError):continue
    return out


def sdsu_calendar(force=False):
    key='ncaa_sdsu';cached=CACHE.get(key)
    if not force and cached and time.time()-cached[0]<cached[3]:return cached[1],[cached[2]]
    try:
        with urlopen(Request(SDSU_URL,headers={'User-Agent':'BOTS-VOLEY/1.07'}),timeout=8) as r:
            text=r.read(3_000_000).decode('utf-8')
        rows=parse_sdsu(text)
        if not rows:raise ValueError('calendario no reconocido')
        reason='ok';ttl=300
    except Exception as exc:
        rows=[];reason=type(exc).__name__;ttl=30
    CACHE[key]=(time.time(),rows,reason,ttl)
    return rows,[reason]


norceca_calendar=calendar

def calendar(force=False):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(norceca_calendar,force);b=pool.submit(sdsu_calendar,force)
        ar,am=a.result();br,bm=b.result()
    return ar+br,am+bm


def history_summary(match):
    """Descriptive, pre-kickoff observations. No uncalibrated win probabilities."""
    import catalog as c
    if match.get('_source')!='norceca':return []
    rows,_=norceca_calendar()
    eligible=[r for r in rows if r['timestamp']<match['timestamp'] and r['id']!=match['id']
              and r['league']['name']==match['league']['name']
              and r['status']['short']=='FT' and not r.get('_data_issue')]
    result=[]
    for side in ['home','away']:
        name=match['teams'][side]['name'];values=[]
        for row in eligible:
            hn=c.canonical(row['teams']['home']['name']);an=c.canonical(row['teams']['away']['name'])
            if c.canonical(name) not in (hn,an):continue
            h=row['scores'].get('home');a=row['scores'].get('away')
            if h is None or a is None:continue
            values.append((h,a) if c.canonical(name)==hn else (a,h))
        n=len(values);wins=sum(a>b for a,b in values)
        result.append({'team':name,'n':n,'wins':wins,'losses':n-wins,
                       'sets_for':sum(a for a,b in values),'sets_against':sum(b for a,b in values)})
    return result

'''

_embedded_catalog = r'''
"""Catálogo multifuente de vóley. Adaptado del orquestador V9.70 de fútbol.
Los IDs nunca se intercambian entre proveedores. Sin cuotas ni fechas inventadas.
"""
import os
import re
import json
import time
import logging
import unicodedata
import threading
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote
import official

LIMA = timezone(timedelta(hours=-5))
LOG = logging.getLogger('voley.catalog')
TIMEOUT = max(2, min(12, float(os.getenv('SOURCE_TIMEOUT', '6'))))
CACHE = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=8)


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', s))


ALIASES = {
    'usa': ['eeuu', 'ee uu', 'estados unidos', 'united states', 'united states of america', 'eua'],
    'dominican republic': ['republica dominicana', 'rep dominicana', 'dominicana', 'republica dominca', 'republicia dominca', 'republcia dominca', 'dom'],
    'brazil': ['brasil'], 'germany': ['alemania'], 'poland': ['polonia'],
    'italy': ['italia'], 'japan': ['japon'], 'netherlands': ['paises bajos', 'holanda'],
    'turkey': ['turquia', 'turkiye'], 'south korea': ['corea del sur', 'korea republic'],
    'france': ['francia'], 'spain': ['espana'], 'belgium': ['belgica'],
    'czech republic': ['chequia', 'czechia'], 'latvia': ['letonia'],
    'puerto rico': ['p rico'], 'argentina': ['arg'], 'canada': ['can'],
}
REPLACEMENTS = sorted([(a, k) for k, arr in ALIASES.items() for a in arr], key=lambda x: -len(x[0]))


def canonical(s):
    s = norm(s)
    for a, k in REPLACEMENTS:
        s = re.sub(r'(?<!\w)' + re.escape(a) + r'(?!\w)', k, s)
    s = re.sub(r'\b(women|womens|femenino|femenina|femenil|damas)\b', 'women', s)
    s = re.sub(r'\b(men|mens|masculino|masculina|varones)\b', 'men', s)
    s = re.sub(r'\bsub\s*(\d{2})\b', r'u\1', s)
    s = re.sub(r'\b(w|f)$', 'women', s)
    return ' '.join(w for w in s.split() if w not in {'volleyball', 'voleyball', 'voley', 'voleibol'})


def category(s):
    s = canonical(s)
    gender = 'women' if 'women' in s.split() else 'men' if 'men' in s.split() else ''
    age = re.search(r'\bu\d{2}\b', s)
    return gender, age.group() if age else '', 'beach' if re.search(r'\b(beach|playa)\b', s) else ''


def name_score(q, name):
    q, name = canonical(q), canonical(name)
    if not q or not name:
        return 0.0
    qc, nc = category(q), category(name)
    if any(a and a != b for a, b in zip(qc, nc)):
        return 0.0
    if q == name or (' ' + q + ' ') in (' ' + name + ' '):
        return 1.0
    qw, nw = q.split(), name.split()
    scores = []
    for w in qw:
        best = max((SequenceMatcher(None, w, v).ratio() if len(w) >= 4 and w[:2] == v[:2] else float(w == v) for v in nw), default=0)
        scores.append(best)
    return min(scores) if scores else 0.0


def query_parts(q):
    q = norm(q)
    q = re.sub(r'\b(vs|versus|contra|frente a|y)\b', ' | ', q)
    q = re.sub(r'\b(hoy|juega|juegan|juego|partido|partidos|analiza|analizar|analisis|el|entre|ahora)\b', ' ', q)
    return [canonical(p) for p in q.split('|') if canonical(p)]


def find_matches(query, rows):
    parts = query_parts(query)
    if not parts or len(parts) > 2:
        return []
    ranked = []
    for m in rows:
        h, a = m['teams']['home']['name'], m['teams']['away']['name']
        # Category can be carried by the tournament rather than team name.
        cat = ' '.join(category((m.get('league') or {}).get('name', '')))
        h, a = h + ' ' + cat, a + ' ' + cat
        if len(parts) == 2:
            score = max(min(name_score(parts[0], h), name_score(parts[1], a)), min(name_score(parts[0], a), name_score(parts[1], h)))
        else:
            score = max(name_score(parts[0], h), name_score(parts[0], a))
        if score >= .76:
            ranked.append((score, m))
    ranked.sort(key=lambda x: (-x[0], x[1]['timestamp']))
    return [m for _, m in ranked[:30]]


def timestamp(raw):
    try:
        if isinstance(raw, (int, float)):
            return int(raw)
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def day_of(m):
    try:
        return datetime.fromtimestamp(m['timestamp'], LIMA).date().isoformat()
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def integer(v):
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def status(value):
    s = str(value or '').upper()
    if s in {'FINISHED', 'FT', 'ENDED', 'AOT'}:
        return 'FT'
    if s in {'INPROGRESS', 'LIVE', 'IN_PLAY', 'INPLAY', '1S', '2S', '3S', '4S', '5S', 'S1', 'S2', 'S3', 'S4', 'S5'}:
        return s if s.endswith('S') else 'LIVE'
    if s in {'CANCELED', 'CANCELLED', 'CANC'}:
        return 'CANC'
    if s in {'POSTPONED', 'PST'}:
        return 'PST'
    if s in {'INT', 'INTERRUPTED', 'SUSP', 'SUSPENDED'}:
        return 'SUSP'
    if s in {'NS', 'NOTSTARTED', 'SCHEDULED'}:
        return 'NS'
    return 'UNKNOWN'


def make_match(source, eid, ts, home, away, league, state, scores=None, team_ids=None, league_id=None, season=None, points=None):
    ts = timestamp(ts)
    if not eid or not ts or not home or not away:
        return None
    tids = team_ids or (None, None)
    return {'id': f'{source}:{eid}', '_source': source, '_source_id': str(eid),
            'timestamp': ts, 'date': datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            'teams': {'home': {'id': tids[0], 'name': home}, 'away': {'id': tids[1], 'name': away}},
            'league': {'id': league_id, 'name': league or 'Competición', 'season': season},
            'status': {'short': status(state)}, 'scores': scores or {}, 'points': points or {},
            '_fetched_at': time.time()}


def parse_api(data):
    out = []
    for e in data.get('response') or []:
        try:
            h, a = e['teams']['home'], e['teams']['away']; lg = e.get('league') or {}
            m = make_match('api', e['id'], e.get('timestamp') or e.get('date'), h['name'], a['name'], lg.get('name'), (e.get('status') or {}).get('short'), e.get('scores'), (h.get('id'), a.get('id')), lg.get('id'), lg.get('season'))
            if m:
                # Points are distinct from match sets; period data must stay explicit.
                m['points'] = e.get('points') or {}; out.append(m)
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def parse_sportsdb(data):
    out = []
    for e in data.get('events') or []:
        if norm(e.get('strSport')) != 'volleyball':
            continue
        raw = e.get('strTimestamp')
        if not raw and e.get('dateEvent') and e.get('strTime'):
            raw = e['dateEvent'] + 'T' + e['strTime']
        m = make_match('sportsdb', e.get('idEvent'), raw, e.get('strHomeTeam'), e.get('strAwayTeam'), e.get('strLeague'), 'PST' if e.get('strPostponed') == 'yes' else e.get('strStatus'), {'home': integer(e.get('intHomeScore')), 'away': integer(e.get('intAwayScore'))}, (e.get('idHomeTeam'), e.get('idAwayTeam')), e.get('idLeague'), e.get('strSeason'))
        if m:
            out.append(m)
    return out


def parse_sofa(data):
    out = []
    for e in data.get('events') or []:
        try:
            tour = e.get('tournament') or {}; sport = ((tour.get('category') or {}).get('sport') or {}).get('slug')
            if sport and sport != 'volleyball':
                continue
            h, a = e['homeTeam'], e['awayTeam']
            hs, aws = e.get('homeScore') or {}, e.get('awayScore') or {}
            # Sofa current/display is sets for volleyball; periodN carries points.
            m = make_match('sofa', e['id'], e.get('startTimestamp'), h['name'], a['name'], tour.get('name'), (e.get('status') or {}).get('type'), {'home': hs.get('current'), 'away': aws.get('current')}, (h.get('id'), a.get('id')), tour.get('id'))
            if m:
                for n in range(5, 0, -1):
                    if hs.get(f'period{n}') is not None and aws.get(f'period{n}') is not None:
                        m['points'] = {'home': hs[f'period{n}'], 'away': aws[f'period{n}']}; break
                out.append(m)
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def fetch(source, url, parser, headers=None, force=False):
    now = time.time()
    with LOCK:
        cached = CACHE.get(url)
        if not force and cached and now - cached[0] < cached[3]:
            return cached[1], cached[2]
    try:
        req = Request(url, headers={'User-Agent': 'BOTS-VOLEY/1.07', 'Accept': 'application/json', **(headers or {})})
        with urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read(8_000_000).decode('utf-8'))
        if not isinstance(data, dict) or data.get('errors'):
            raise ValueError('respuesta inválida o error del proveedor')
        expected = 'response' if source == 'api' else 'events'
        if expected not in data or (data[expected] is not None and not isinstance(data[expected], list)):
            raise ValueError('formato de catálogo no reconocido')
        rows, reason, ttl = parser(data), 'ok', (1800 if source == 'api' else 300)
    except Exception as exc:
        # No URLs/credentials in logs; failures never become a confirmed empty calendar.
        rows, reason, ttl = [], type(exc).__name__, 60
        LOG.warning('Fuente %s no disponible: %s', source, reason)
    with LOCK:
        CACHE[url] = (now, rows, reason, ttl)
    return rows, reason


def dedupe(rows):
    # Conservative cross-source merge: same category AND named competition AND
    # ordered participants/time. Ambiguous competitions remain separate options.
    out, seen = [], {}
    for m in sorted(rows, key=lambda x: (x['_source'] != 'api', x['_source'] != 'sofa')):
        h, a = m['teams']['home']['name'], m['teams']['away']['name']
        k = (canonical(h), canonical(a), canonical(m['league']['name']), m['timestamp'])
        if k in seen:
            base = seen[k]
            base.setdefault('_references', {})[m['_source']] = m['_source_id']
            continue
        m = dict(m); m['_references'] = {m['_source']: m['_source_id']}
        seen[k] = m; out.append(m)
    return sorted(out, key=lambda x: (x['timestamp'], x['id']))


def catalog(day=None):
    day = day or datetime.now(LIMA).date().isoformat()
    # A Lima day intersects two UTC dates. Fetch both, then filter real timestamps.
    dates = [day, (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()]
    jobs = []
    api_key = os.getenv('VOLLEY_API_KEY', '').strip()
    for d in dates:
        if api_key:
            base = os.getenv('VOLLEY_API_BASE', 'https://v1.volleyball.api-sports.io').rstrip('/')
            jobs.append(('api', f'{base}/games?{urlencode({"date": d})}', parse_api, {'x-apisports-key': api_key}))
        key = quote(os.getenv('SPORTSDB_API_KEY', '123'), safe='')
        jobs.append(('sportsdb', f'https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php?{urlencode({"d": d, "s": "Volleyball"})}', parse_sportsdb, None))
        if os.getenv('ENABLE_SOFASCORE', '1') == '1':
            jobs.append(('sofa', f'https://www.sofascore.com/api/v1/sport/volleyball/scheduled-events/{d}', parse_sofa, None))
    official_future = POOL.submit(official.calendar)
    futures = [(j[0], POOL.submit(fetch, *j)) for j in jobs]
    rows, meta = [], {}
    for src, f in futures:
        batch, reason = f.result()
        rows.extend(m for m in batch if day_of(m) == day)
        meta.setdefault(src, []).append(reason)
    official_rows, official_reasons = official_future.result()
    rows.extend(m for m in official_rows if day_of(m) == day)
    meta['official'] = official_reasons
    return dedupe(rows), meta


def refresh(m):
    src, eid = m['_source'], m['_source_id']
    if src in {'norceca','ncaa_sdsu'}:
        rows, reasons = official.calendar(force=True)
        return next((x for x in rows if x['id'] == m['id']), None)
    if src == 'api':
        key = os.getenv('VOLLEY_API_KEY', '').strip()
        base = os.getenv('VOLLEY_API_BASE', 'https://v1.volleyball.api-sports.io').rstrip('/')
        rows, reason = fetch(src, f'{base}/games?{urlencode({"id": eid})}', parse_api, {'x-apisports-key': key}, force=True)
    elif src == 'sportsdb':
        key = quote(os.getenv('SPORTSDB_API_KEY', '123'), safe='')
        rows, reason = fetch(src, f'https://www.thesportsdb.com/api/v1/json/{key}/lookupevent.php?{urlencode({"id": eid})}', parse_sportsdb, force=True)
    elif src == 'sofa':
        # fetch expects events; use the date endpoint with cache bypass, preserving ID.
        utc_day = datetime.fromtimestamp(m['timestamp'], timezone.utc).date().isoformat()
        rows, reason = fetch(src, f'https://www.sofascore.com/api/v1/sport/volleyball/scheduled-events/{utc_day}', parse_sofa, force=True)
    else:
        return None
    return next((x for x in rows if x['id'] == m['id']), None)


def search_extra(query, day=None):
    """Búsqueda dirigida cuando el calendario omite el encuentro; mismo filtro de identidad."""
    day = day or datetime.now(LIMA).date().isoformat()
    parts = query_parts(query)
    if not parts: return []
    dates = [day, (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()]
    futures = []
    key = quote(os.getenv('SPORTSDB_API_KEY', '123'), safe='')
    search_term = '_vs_'.join(parts) if len(parts) == 2 else parts[0]
    for d in dates:
        url = f'https://www.thesportsdb.com/api/v1/json/{key}/searchevents.php?{urlencode({"e": search_term, "d": d})}'
        futures.append(POOL.submit(fetch, 'sportsdb', url, parse_sportsdb))
    api_key = os.getenv('VOLLEY_API_KEY', '').strip()
    if api_key:
        base = os.getenv('VOLLEY_API_BASE', 'https://v1.volleyball.api-sports.io').rstrip('/')
        term = parts[0]
        def parse_teams(d):
            out=[]
            for raw in d.get('response') or []:
                tm=raw.get('team') if isinstance(raw.get('team'),dict) else raw
                if tm.get('id') and name_score(term,tm.get('name')) >= .76: out.append(tm)
            return out
        candidates, reason = fetch('api', f'{base}/teams?{urlencode({"search":term})}', parse_teams, {'x-apisports-key':api_key})
        for tm in candidates[:2]:
            for d in dates:
                futures.append(POOL.submit(fetch,'api',f'{base}/games?{urlencode({"date":d,"team":tm["id"]})}',parse_api,{'x-apisports-key':api_key}))
    rows=[]
    for future in futures:
        batch,_=future.result()
        rows.extend(m for m in batch if day_of(m)==day)
    return find_matches(query, dedupe(rows))

'''

# Ambos módulos se registran antes de inicializarlos (referencia circular diferida).
_official = _types.ModuleType('official')
sources = _types.ModuleType('catalog')
_sys.modules['official'] = _official
_sys.modules['catalog'] = sources
exec(compile(_embedded_official, '<bot:official>', 'exec'), _official.__dict__)
exec(compile(_embedded_catalog, '<bot:catalog>', 'exec'), sources.__dict__)
del _embedded_official, _embedded_catalog

import os, math, json, time, logging, re, unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

VERSION = "1.07"
LIMA = timezone(timedelta(hours=-5))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
VOLLEY_API_KEY = os.getenv("VOLLEY_API_KEY", "").strip()
VOLLEY_BASE = os.getenv("VOLLEY_API_BASE", "https://v1.volleyball.api-sports.io").rstrip("/")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "2"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bots_voley")
STATE, CACHE = {}, {}


def http_json(url, headers=None, timeout=6):
    h={"User-Agent":"BOTS-VOLEY/1.03"}; h.update(headers or {})
    with urlopen(Request(url, headers=h), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api(path, cache_seconds=600, **params):
    if not VOLLEY_API_KEY:
        return {"response":[], "errors":{"key":"VOLLEY_API_KEY no configurada"}, "_ok":False}
    url=f"{VOLLEY_BASE}/{path.lstrip('/')}"
    if params: url += "?" + urlencode(params)
    key=url; now=time.time()
    if key in CACHE and now-CACHE[key][0] < cache_seconds:
        return CACHE[key][1]
    try:
        data=http_json(url, {"x-apisports-key":VOLLEY_API_KEY})
        if not isinstance(data,dict): data={"response":[],"errors":{"format":"respuesta no JSON-object"}}
        data["_ok"] = not bool(data.get("errors"))
        log.info("API %s params=%s results=%s errors=%s", path, params, data.get("results"), data.get("errors") or "none")
        CACHE[key]=(now,data); return data
    except Exception as e:
        log.warning("API-SPORTS %s no disponible (%s)", path, type(e).__name__)
        return {"response":[],"errors":{"network":str(e)},"_ok":False}


def tg(method, **params):
    if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN no configurado")
    return http_json(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}?{urlencode(params)}", timeout=35)


def send(chat_id,text):
    chunks=[]; current=''
    for line in str(text).splitlines():
        for pos in range(0,max(1,len(line)),3500):
            part=line[pos:pos+3500]
            if len(current)+len(part)+1>3800:
                chunks.append(current);current=''
            current += part+'\n'
    if current: chunks.append(current.rstrip())
    for chunk in chunks:
        chunk = f'🏐 V{VERSION} · MULTIFUENTE\n' + chunk
        try: tg('sendMessage',chat_id=chat_id,text=chunk)
        except Exception as e: log.error('Telegram send: %s',type(e).__name__)


def norm(s):
    s=unicodedata.normalize("NFKD",str(s or "")).encode("ascii","ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+",s))


def today_catalog():
    rows, meta = sources.catalog()
    ok = any('ok' in reasons for reasons in meta.values())
    return {'response': rows, 'errors': {} if ok else {'sources': 'sin fuentes disponibles'}, '_meta': meta}, rows



def today_matches():
    return today_catalog()[1]


def teams(m): return (m.get("teams") or {}).get("home",{}),(m.get("teams") or {}).get("away",{})
def names(m):
    h,a=teams(m); return h.get("name","Local"),a.get("name","Visita")


def kickoff_text(m):
    try: return datetime.fromtimestamp(m['timestamp'], LIMA).strftime('%H:%M') + ' Perú'
    except (KeyError, TypeError, ValueError): return 'hora sin confirmar'



def league_text(m):
    lg=m.get("league") or {}
    return lg.get("name") or lg.get("country") or "Competición"


def find_matches(q, catalog=None):
    return sources.find_matches(q, catalog if catalog is not None else today_matches())



def is_catalog_command(t):
    n=norm(t)
    return n in {"partidos de hoy","partidos hoy","juegos de hoy","juegos hoy","encuentros de hoy","encuentros hoy","hoy","partidos"}


def list_catalog(chat_id, catalog):
    if not catalog:
        send(chat_id,'🏐 Las fuentes consultadas no devolvieron encuentros confirmados para HOY en Perú.'); return
    shown=catalog[:30]
    STATE.setdefault(chat_id,{})['_options']=shown
    lines=[f'🏐 VÓLEY DE HOY · {len(catalog)} encuentros disponibles']
    for i,m in enumerate(shown,1):
        h,a=names(m); lines.append(f'{i}. {h} vs {a} · {datetime.fromtimestamp(m["timestamp"],LIMA).strftime("%d/%m")} · {kickoff_text(m)} · {league_text(m)}')
    if len(catalog)>30: lines.append('Escribe un equipo para filtrar el resto del catálogo.')
    lines.append('Responde con el número o escribe un equipo.')
    send(chat_id,'\n'.join(lines))


def recursive_team_rows(obj, team_id, found=None):
    if found is None: found=[]
    if isinstance(obj, dict):
        t=obj.get('team')
        if isinstance(t, dict) and str(t.get('id')) == str(team_id): found.append(obj)
        for v in obj.values(): recursive_team_rows(v, team_id, found)
    elif isinstance(obj, list):
        for v in obj: recursive_team_rows(v, team_id, found)
    return found



def standing_strength(m, team_id):
    if m.get("_source") != "api": return None
    lg=m.get("league") or {}; league=lg.get("id"); season=lg.get("season")
    if not league or season is None or not team_id: return None
    d=api("standings",cache_seconds=3600,league=league,season=season)
    rows=recursive_team_rows(d.get("response") or [],team_id)
    if not rows: return None
    r=rows[0]; g=r.get("games") or r.get("all") or {}
    played=g.get("played") or r.get("played") or 0
    win=g.get("win") if isinstance(g,dict) else None
    lose=g.get("lose") if isinstance(g,dict) else None
    if win is None: win=r.get("win") or r.get("wins")
    if lose is None: lose=r.get("lose") or r.get("losses")
    if isinstance(win,dict): win=win.get("total")
    if isinstance(lose,dict): lose=lose.get("total")
    try: played=int(played); win=int(win); lose=int(lose or 0)
    except Exception: return None
    if played<=0: return None
    return {"n":played,"win_rate":win/played,"wins":win,"losses":lose,"position":r.get("position") or r.get("rank")}


def clamp(x,a=.05,b=.95): return max(a,min(b,x))
def logistic(x): return 1/(1+math.exp(-x))


def pre_model(m):
    h,a=teams(m); hs=standing_strength(m,h.get("id")); as_=standing_strength(m,a.get("id"))
    if hs and as_:
        # Conservative baseline, deliberately capped until chronological validation exists.
        n=min(hs["n"],as_["n"]); shrink=min(1.0,n/10.0)
        diff=(hs["win_rate"]-as_["win_rate"])*shrink
        ph=clamp(logistic(2.15*diff+0.08),.12,.88)
        info=min(85,50+min(15,n)*2)
    else:
        ph=.50; info=35
    return {"ph":ph,"pa":1-ph,"info":info,"hs":hs,"as":as_}


def intuition(prob,info):
    # V1.01 never emits green: calibration has not yet been completed.
    if info>=60 and prob>=.65: return "🟡 HAY DUDAS"
    return "🔴 NO CONFÍA"


def status_short(m): return str((m.get("status") or {}).get("short","")).upper()
def is_live(m): return status_short(m) in {"LIVE","INPLAY","IN_PLAY","1S","2S","3S","4S","5S"}



def extract_team_candidates(data):
    out=[]; seen=set()
    def walk(x):
        if isinstance(x,dict):
            # API-Sports team responses can be either the team object itself or wrapped in {team:{...}}
            cand=x.get("team") if isinstance(x.get("team"),dict) else x
            if isinstance(cand,dict) and cand.get("id") is not None and cand.get("name"):
                k=str(cand.get("id"))
                if k not in seen:
                    seen.add(k); out.append(cand)
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(data.get("response") or [])
    return out


def match_lima_day(m, day):
    raw=m.get("date") or m.get("datetime")
    if raw:
        try:
            z=str(raw).replace("Z","+00:00")
            dt=datetime.fromisoformat(z)
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(LIMA).strftime("%Y-%m-%d")==day
        except Exception: pass
    ts=m.get("timestamp")
    try: return datetime.fromtimestamp(int(ts),LIMA).strftime("%Y-%m-%d")==day
    except Exception: return True





def live_points(m):
    x=m.get('points') or {}
    h,a=x.get('home'),x.get('away')
    if isinstance(h,(int,float)) and isinstance(a,(int,float)): return int(h),int(a)
    return None



def set_distribution(ph):
    # Invert best-of-five match probability to a coherent iid set parameter.
    low,high=0.,1.
    for _ in range(60):
        ps=(low+high)/2; q=1-ps
        win=ps**3*(1+3*q+6*q*q)
        if win < ph: low=ps
        else: high=ps
    ps=(low+high)/2; q=1-ps
    return {'3-0':ps**3,'3-1':3*ps**3*q,'3-2':6*ps**3*q*q,
            '2-3':6*q**3*ps*ps,'1-3':3*q**3*ps,'0-3':q**3}



def historical_lines(m):
    summary=sources.official.history_summary(m)
    if not summary or not any(x['n'] for x in summary): return []
    lines=['', '📊 HISTORIAL ANTERIOR AL PARTIDO']
    for x in summary:
        lines += [x['team'],f"Muestra válida: {x['n']} partidos",f"Victorias/derrotas: {x['wins']}/{x['losses']}",f"Sets ganados/perdidos: {x['sets_for']}/{x['sets_against']}"]
    lines += ['Lectura descriptiva del torneo, ante rivales diferentes.', 'Se excluyen resultados contradictorios; no es una probabilidad de victoria.', '']
    return lines


def render(m):
    hn,an=names(m); st=status_short(m)
    lines=[f'🏐 BOTS VÓLEY — V{VERSION}',f'{hn} vs {an}',f'🏆 {league_text(m)}',f'📅 {datetime.fromtimestamp(m["timestamp"],LIMA).strftime("%d/%m/%Y")} · {kickoff_text(m)}']
    if st in {'FT','CANC','PST','SUSP'}:
        label={'FT':'FINALIZADO','CANC':'CANCELADO','PST':'APLAZADO','SUSP':'SUSPENDIDO'}[st]
        lines.append(label)
        scores=m.get('scores') or {}
        if st=='FT' and scores.get('home') is not None and scores.get('away') is not None:
            lines.append(f"Sets: {scores['home']}-{scores['away']}")
        if m.get('_data_issue'): lines.append(m['_data_issue'])
        lines.append('Sin propuestas activas.'); return '\n'.join(lines)
    if st=='UNKNOWN':
        lines += ['Estado y marcador actuales sin confirmar.']
        lines += historical_lines(m)
        lines += ['Sin propuesta LIVE sustentada hasta confirmar el estado.']
        return '\n'.join(lines)
    if is_live(m):
        sc=m.get('scores') or {}
        lines += ['⏱ LIVE', f"Sets: {sc.get('home') if sc.get('home') is not None else '?'}-{sc.get('away') if sc.get('away') is not None else '?'}"]
        pts=live_points(m)
        if pts: lines.append(f'Puntos del último set informado: {pts[0]}-{pts[1]}')
        lines += historical_lines(m)
        lines += ['Probabilidades LIVE: todavía sin modelo validado.', 'SIN PROPUESTA CONFIABLE.']
        return '\n'.join(lines)
    lines.append('⏱ PRE')
    lines += historical_lines(m)
    model=pre_model(m)
    if not model['hs'] or not model['as']:
        lines += ['Respaldo estadístico: insuficiente.', 'Ganador: sin porcentaje sustentado.', 'Sets, puntos y hándicap: sin estimación sustentada.', 'SIN PROPUESTA CONFIABLE.']
        return '\n'.join(lines)
    ph=model['ph']; dist=set_distribution(ph)
    lines += ['', '🏆 GANADOR · MODELO EXPLORATORIO', f'{hn}: {ph*100:.1f}%', f'{an}: {(1-ph)*100:.1f}%', 'Respaldo: clasificación de ambos equipos; sin calibración histórica.']
    if 'beach' not in sources.category(league_text(m)) and 'playa' not in norm(league_text(m)):
        lines += ['', '🎯 SETS · SUPUESTO AL MEJOR DE CINCO']
        lines += [f'{k}: {v*100:.1f}%' for k,v in dist.items()]
    lines += ['', 'Puntos y hándicap: sin estimación sustentada.', '🟡 SOLO PRUEBA / REGISTRO. Sin acierto validado.']
    return '\n'.join(lines)


def choose(chat_id,m, refresh=True):
    if refresh:
        updated=sources.refresh(m)
        if updated is not None: m=updated
        else:
            m=dict(m);m['status']={'short':'UNKNOWN'}
            m['_refresh_failed']=True
    state=STATE.setdefault(chat_id,{})
    state['_active']=m
    state.pop('_options',None)
    send(chat_id,render(m))


def handle(chat_id,text):
    t=(text or '').strip()
    if not t: return
    if t.lower() in {'/start','start','inicio'}:
        send(chat_id, '🏐 BOTS VÓLEY V1.07\nEscribe los equipos, PARTIDOS DE HOY o AHORA.\nBúsqueda multifuente con horario de Perú.'); return
    state=STATE.setdefault(chat_id, {})
    if t.upper() == 'AHORA':
        selected=state.get('_active')
        if not selected:
            send(chat_id,'No hay partido seleccionado. Escribe un equipo.'); return
        fresh=sources.refresh(selected)
        if fresh is None:
            send(chat_id,'⚠️ No pude actualizar el partido seleccionado. No hay una lectura LIVE nueva.'); return
        choose(chat_id,fresh,refresh=False); return
    if t.isdigit() and state.get('_options'):
        i=int(t)-1; opts=state['_options']
        if 0<=i<len(opts): choose(chat_id,opts[i]); return
        send(chat_id,'Ese número no corresponde a la lista actual.'); return
    if t.isdigit():
        send(chat_id,'No hay una lista pendiente. Escribe el equipo para mostrar las opciones.'); return
    state.pop('_options',None)
    d,rows=today_catalog()
    if d.get('errors'):
        send(chat_id,'⚠️ No pude consultar las fuentes de partidos. Esto no significa que el encuentro no exista.'); return
    if is_catalog_command(t): list_catalog(chat_id, rows); return
    matches=find_matches(t,rows)
    if not matches: matches=sources.search_extra(t)
    if not matches:
        send(chat_id,'No pude confirmar ese encuentro de HOY en las fuentes disponibles. Puede faltar cobertura o estar registrado en otra categoría. El partido no se da por inexistente.'); return
    state['_options']=matches
    lines=['🏐 Coincidencias de HOY']
    for i,m in enumerate(matches,1):
        h,a=names(m); lines.append(f'{i}. {h} vs {a} · {datetime.fromtimestamp(m["timestamp"],LIMA).strftime("%d/%m")} · {kickoff_text(m)} · {league_text(m)}')
    lines.append('Responde con el número.')
    send(chat_id,'\n'.join(lines))


def main():
    if not TELEGRAM_TOKEN: raise SystemExit("Falta TELEGRAM_BOT_TOKEN")
    
    log.info("BOTS VÓLEY V%s iniciado: catálogo multifuente", VERSION)
    offset=0
    while True:
        try:
            r=tg("getUpdates",timeout=30,offset=offset)
            for u in r.get("result",[]):
                offset=max(offset,u.get("update_id",0)+1); msg=u.get("message") or {}; cid=(msg.get("chat") or {}).get("id")
                if cid and msg.get("text") is not None: handle(cid,msg.get("text"))
        except Exception as e:
            log.error("poll: %s",type(e).__name__); time.sleep(3)
        time.sleep(POLL_SECONDS)

if __name__=="__main__": main()
