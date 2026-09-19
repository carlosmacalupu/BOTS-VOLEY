import os, math, json, time, logging, re, unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

VERSION = "1.01"
LIMA = timezone(timedelta(hours=-5))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
VOLLEY_API_KEY = os.getenv("VOLLEY_API_KEY", "").strip()
VOLLEY_BASE = os.getenv("VOLLEY_API_BASE", "https://v1.volleyball.api-sports.io").rstrip("/")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "2"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bots_voley")
STATE, CACHE = {}, {}


def http_json(url, headers=None, timeout=20):
    h={"User-Agent":"BOTS-VOLEY/1.01"}; h.update(headers or {})
    with urlopen(Request(url, headers=h), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api(path, cache_seconds=600, **params):
    if not VOLLEY_API_KEY:
        return {"response":[], "errors":{"key":"VOLLEY_API_KEY no configurada"}}
    url=f"{VOLLEY_BASE}/{path.lstrip('/')}"
    if params: url += "?" + urlencode(params)
    key=url; now=time.time()
    if key in CACHE and now-CACHE[key][0] < cache_seconds: return CACHE[key][1]
    try:
        data=http_json(url, {"x-apisports-key":VOLLEY_API_KEY})
        CACHE[key]=(now,data); return data
    except Exception as e:
        log.error("API-SPORTS %s: %s", path, e)
        return {"response":[],"errors":{"network":str(e)}}


def tg(method, **params):
    if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN no configurado")
    return http_json(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}?{urlencode(params)}", timeout=35)


def send(chat_id,text):
    try: return tg("sendMessage",chat_id=chat_id,text=text)
    except Exception as e: log.error("Telegram send: %s",e)


def norm(s):
    s=unicodedata.normalize("NFKD",str(s or "")).encode("ascii","ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+",s))


def today_matches():
    # Lima date is the user's operational day. One cached request serves all searches/AHORA.
    date=datetime.now(LIMA).strftime("%Y-%m-%d")
    d=api("games",cache_seconds=300,date=date,timezone="America/Lima")
    return d.get("response") or []


def teams(m): return (m.get("teams") or {}).get("home",{}),(m.get("teams") or {}).get("away",{})
def names(m):
    h,a=teams(m); return h.get("name","Local"),a.get("name","Visita")

def find_matches(q):
    toks=[x for x in norm(q).split() if len(x)>=2]; out=[]
    for m in today_matches():
        h,a=names(m); n=norm(h+" "+a); score=sum(1 for t in toks if t in n)
        if toks and score: out.append((score,m))
    out.sort(key=lambda x:(x[0],x[1].get("timestamp",0)),reverse=True)
    return [m for _,m in out[:10]]


def recursive_team_rows(obj, team_id, found=None):
    found=found or []
    if isinstance(obj,dict):
        t=obj.get("team")
        if isinstance(t,dict) and str(t.get("id"))==str(team_id): found.append(obj)
        for v in obj.values(): recursive_team_rows(v,team_id,found)
    elif isinstance(obj,list):
        for v in obj: recursive_team_rows(v,team_id,found)
    return found


def standing_strength(m, team_id):
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


def render(m):
    hn,an=names(m); model=pre_model(m); ph,pa=model["ph"],model["pa"]
    live=is_live(m); scores=m.get("scores") or {}; winner=hn if ph>=pa else an; wp=max(ph,pa)
    intu=intuition(wp,model["info"])
    lines=[f"🏐 BOTS VÓLEY — V{VERSION}",f"{hn} vs {an}"]
    if live:
        lines += [f"⏱ LIVE · Sets {scores.get('home','?')}-{scores.get('away','?')}","","🧭 PRE → LIVE: el PRE permanece como referencia."]
    else: lines += ["⏱ PRE"]
    lines += ["",f"🏆 GANADOR · 🧠 {intu}",f"{hn}: {ph*100:.1f}% · {an}: {pa*100:.1f}%"]
    if not (model["hs"] and model["as"]):
        lines += ["","📊 SOPORTE","Datos competitivos insuficientes para una probabilidad fuerte."]
    lines += ["","🎯 CONCLUSIÓN"]
    if intu.startswith("🟡"):
        lines += [f"{winner} aparece como favorito estadístico: {wp*100:.1f}% · 🟡","Aún no se habilita 🟢 hasta completar calibración histórica."]
    else:
        lines += ["No hay una selección suficientemente respaldada.","No se fuerza propuesta."]
    return "\n".join(lines)


def choose(chat_id,m): STATE[chat_id]=m; send(chat_id,render(m))

def handle(chat_id,text):
    t=(text or "").strip()
    if not t:return
    if t.lower() in {"/start","start","inicio"}:
        send(chat_id,"🏐 BOTS VÓLEY listo.\nEscribe uno o ambos equipos de un partido de HOY.\nAHORA = reanalizar el partido seleccionado."); return
    if t.upper()=="AHORA":
        m=STATE.get(chat_id)
        if not isinstance(m,dict) or m.get("_options"): send(chat_id,"No hay partido seleccionado. Escribe el nombre de un equipo."); return
        mid=str(m.get("id")); fresh=next((x for x in today_matches() if str(x.get("id"))==mid),m)
        STATE[chat_id]=fresh; send(chat_id,render(fresh)); return
    if t.isdigit() and isinstance(STATE.get(chat_id),dict) and STATE[chat_id].get("_options"):
        opts=STATE[chat_id]["_options"]; i=int(t)-1
        if 0<=i<len(opts): choose(chat_id,opts[i]); return
    ms=find_matches(t)
    if not ms: send(chat_id,"No encontré un partido de vóley de HOY con ese nombre. Prueba con uno de los equipos."); return
    if len(ms)==1: choose(chat_id,ms[0]); return
    STATE[chat_id]={"_options":ms}; lines=["🏐 Encontré estas opciones de HOY:"]
    for i,m in enumerate(ms,1):
        h,a=names(m); lines.append(f"{i}. {h} vs {a}")
    lines.append("Responde solo con el número."); send(chat_id,"\n".join(lines))


def main():
    if not TELEGRAM_TOKEN: raise SystemExit("Falta TELEGRAM_BOT_TOKEN")
    if not VOLLEY_API_KEY: raise SystemExit("Falta VOLLEY_API_KEY")
    log.info("BOTS VÓLEY V%s iniciado con API-SPORTS Volleyball",VERSION)
    offset=0
    while True:
        try:
            r=tg("getUpdates",timeout=30,offset=offset)
            for u in r.get("result",[]):
                offset=max(offset,u.get("update_id",0)+1); msg=u.get("message") or {}; cid=(msg.get("chat") or {}).get("id")
                if cid and msg.get("text") is not None: handle(cid,msg.get("text"))
        except Exception as e:
            log.exception("poll: %s",e); time.sleep(3)
        time.sleep(POLL_SECONDS)

if __name__=="__main__": main()
