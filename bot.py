import os, math, json, time, logging, re, unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

VERSION = "1.04"
import catalog as sources
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
        h,a=names(m); lines.append(f'{i}. {h} vs {a} · {kickoff_text(m)} · {league_text(m)}')
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



def render(m):
    hn,an=names(m); st=status_short(m)
    lines=[f'🏐 BOTS VÓLEY — V{VERSION}',f'{hn} vs {an}',f'🏆 {league_text(m)}',f'🕒 {kickoff_text(m)}']
    if st in {'FT','CANC','PST','SUSP'}:
        label={'FT':'FINALIZADO','CANC':'CANCELADO','PST':'APLAZADO','SUSP':'SUSPENDIDO'}[st]
        lines.append(label)
        scores=m.get('scores') or {}
        if st=='FT' and scores.get('home') is not None and scores.get('away') is not None:
            lines.append(f"Sets: {scores['home']}-{scores['away']}")
        lines.append('Sin propuestas activas.'); return '\n'.join(lines)
    if st=='UNKNOWN':
        lines += ['Estado del partido sin confirmar.', 'Sin propuesta hasta confirmar el estado.']
        return '\n'.join(lines)
    if is_live(m):
        sc=m.get('scores') or {}
        lines += ['⏱ LIVE', f"Sets: {sc.get('home') if sc.get('home') is not None else '?'}-{sc.get('away') if sc.get('away') is not None else '?'}"]
        pts=live_points(m)
        if pts: lines.append(f'Puntos del último set informado: {pts[0]}-{pts[1]}')
        lines += ['Probabilidades LIVE: todavía sin modelo validado.', 'SIN PROPUESTA CONFIABLE.']
        return '\n'.join(lines)
    lines.append('⏱ PRE')
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


def choose(chat_id,m):
    state=STATE.setdefault(chat_id,{})
    state['_active']=m
    state.pop('_options',None)
    send(chat_id,render(m))


def handle(chat_id,text):
    t=(text or '').strip()
    if not t: return
    if t.lower() in {'/start','start','inicio'}:
        send(chat_id, '🏐 BOTS VÓLEY V1.04\nEscribe los equipos, PARTIDOS DE HOY o AHORA.\nBúsqueda multifuente con horario de Perú.'); return
    state=STATE.setdefault(chat_id, {})
    if t.upper() == 'AHORA':
        selected=state.get('_active')
        if not selected:
            send(chat_id,'No hay partido seleccionado. Escribe un equipo.'); return
        fresh=sources.refresh(selected)
        if fresh is None:
            send(chat_id,'⚠️ No pude actualizar el partido seleccionado. No hay una lectura LIVE nueva.'); return
        choose(chat_id,fresh); return
    if t.isdigit() and state.get('_options'):
        i=int(t)-1; opts=state['_options']
        if 0<=i<len(opts): choose(chat_id,opts[i]); return
        send(chat_id,'Ese número no corresponde a la lista actual.'); return
    d,rows=today_catalog()
    if d.get('errors'):
        send(chat_id,'⚠️ No pude consultar las fuentes de partidos. Esto no significa que el encuentro no exista.'); return
    if is_catalog_command(t): list_catalog(chat_id, rows); return
    matches=find_matches(t,rows)
    if not matches: matches=sources.search_extra(t)
    if not matches:
        send(chat_id,'No pude confirmar ese encuentro de HOY en las fuentes disponibles. Puede faltar cobertura o estar registrado en otra categoría. El partido no se da por inexistente.'); return
    if len(matches)==1: choose(chat_id,matches[0]); return
    state['_options']=matches
    lines=['🏐 Coincidencias de HOY']
    for i,m in enumerate(matches,1):
        h,a=names(m); lines.append(f'{i}. {h} vs {a} · {kickoff_text(m)} · {league_text(m)}')
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
