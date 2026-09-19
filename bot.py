import os, math, json, time, logging, re, unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

VERSION = "1.03"
LIMA = timezone(timedelta(hours=-5))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
VOLLEY_API_KEY = os.getenv("VOLLEY_API_KEY", "").strip()
VOLLEY_BASE = os.getenv("VOLLEY_API_BASE", "https://v1.volleyball.api-sports.io").rstrip("/")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "2"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bots_voley")
STATE, CACHE = {}, {}


def http_json(url, headers=None, timeout=20):
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
        log.exception("API-SPORTS %s params=%s", path, params)
        return {"response":[],"errors":{"network":str(e)},"_ok":False}


def tg(method, **params):
    if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN no configurado")
    return http_json(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}?{urlencode(params)}", timeout=35)


def send(chat_id,text):
    try: return tg("sendMessage",chat_id=chat_id,text=text)
    except Exception as e: log.error("Telegram send: %s",e)


def norm(s):
    s=unicodedata.normalize("NFKD",str(s or "")).encode("ascii","ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+",s))


def today_catalog():
    # API-SPORTS Volleyball: one global request for the Lima operational date.
    # No league/country filter: we intentionally retain every competition returned by the provider.
    date=datetime.now(LIMA).strftime("%Y-%m-%d")
    d=api("games",cache_seconds=300,date=date)
    return d, (d.get("response") or [])


def today_matches():
    return today_catalog()[1]


def teams(m): return (m.get("teams") or {}).get("home",{}),(m.get("teams") or {}).get("away",{})
def names(m):
    h,a=teams(m); return h.get("name","Local"),a.get("name","Visita")


def kickoff_text(m):
    raw=m.get("date") or m.get("datetime")
    if raw: return str(raw)[11:16] if len(str(raw))>=16 else str(raw)
    ts=m.get("timestamp")
    try: return datetime.fromtimestamp(int(ts),LIMA).strftime("%H:%M")
    except Exception: return "hora ?"


def league_text(m):
    lg=m.get("league") or {}
    return lg.get("name") or lg.get("country") or "Competición"


def find_matches(q, catalog=None):
    toks=[x for x in norm(q).split() if len(x)>=2]; out=[]
    for m in (catalog if catalog is not None else today_matches()):
        h,a=names(m); n=norm(h+" "+a); score=sum(1 for t in toks if t in n)
        if toks and score: out.append((score,m))
    out.sort(key=lambda x:(x[0],-(x[1].get("timestamp") or 0)),reverse=True)
    return [m for _,m in out[:20]]


def is_catalog_command(t):
    n=norm(t)
    return n in {"partidos de hoy","partidos hoy","juegos de hoy","juegos hoy","encuentros de hoy","encuentros hoy","hoy","partidos"}


def list_catalog(chat_id, catalog):
    if not catalog:
        send(chat_id,"🏐 No hay encuentros devueltos por API-SPORTS para la fecha de hoy en Lima.")
        return
    # Telegram limit: show first 30, but keep all options in state.
    STATE[chat_id]={"_options":catalog}
    lines=[f"🏐 VÓLEY DE HOY · {len(catalog)} encuentros en el catálogo API-SPORTS"]
    for i,m in enumerate(catalog[:30],1):
        h,a=names(m); lines.append(f"{i}. {h} vs {a} · {kickoff_text(m)} · {league_text(m)}")
    if len(catalog)>30: lines.append(f"… y {len(catalog)-30} más. Escribe un equipo para filtrar.")
    lines.append("Responde con el número o escribe un equipo.")
    send(chat_id,"\n".join(lines))

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


def global_team_search(q):
    """Fallback: resolve team IDs globally, then ask API-Sports for that team's games.
    It is only used when the one-call daily catalog misses the user's team, protecting the 100/day plan.
    """
    day=datetime.now(LIMA).strftime("%Y-%m-%d")
    n=norm(q); terms=[]
    if len(n)>=3: terms.append(n)
    # Useful fallback for 'Renata Sesi Bauru': try meaningful words, longest first.
    words=sorted({w for w in n.split() if len(w)>=4 and w not in {"voley","volley","versus"}}, key=len, reverse=True)
    terms.extend(w for w in words if w not in terms)
    teams_found=[]; seen=set()
    for term in terms[:3]:
        d=api("teams",cache_seconds=86400,search=term)
        if d.get("errors"):
            log.warning("TEAM SEARCH error term=%r errors=%s",term,d.get("errors")); continue
        for tm in extract_team_candidates(d):
            tid=str(tm.get("id"))
            if tid not in seen:
                seen.add(tid); teams_found.append(tm)
        if teams_found: break
    if not teams_found: return [], "NO_TEAM"
    games=[]; gids=set()
    # Restrict calls: at most 4 team IDs. First query today's provider date.
    for tm in teams_found[:4]:
        d=api("games",cache_seconds=300,date=day,team=tm.get("id"))
        if d.get("errors"):
            log.warning("TEAM GAMES error team=%s errors=%s",tm.get("id"),d.get("errors")); continue
        for g in d.get("response") or []:
            gid=str(g.get("id"))
            if gid not in gids and match_lima_day(g,day): gids.add(gid); games.append(g)
    # Keep only games whose names overlap the original query; if one team was resolved, its games are valid candidates.
    toks=[x for x in n.split() if len(x)>=3]
    ranked=[]
    for g in games:
        h,a=names(g); hay=norm(h+" "+a); score=sum(1 for t in toks if t in hay)
        ranked.append((score,g))
    ranked.sort(key=lambda x:(x[0], x[1].get("timestamp") or 0), reverse=True)
    return [g for _,g in ranked[:20]], "OK" if games else "NO_GAME"


def live_points(m):
    # Different competitions expose point data differently. Read only explicit values; never invent.
    for key in ("points","scores"):
        x=m.get(key)
        if isinstance(x,dict):
            h=x.get("home"); a=x.get("away")
            if isinstance(h,(int,float)) and isinstance(a,(int,float)): return int(h),int(a)
            cur=x.get("current")
            if isinstance(cur,dict) and isinstance(cur.get("home"),(int,float)) and isinstance(cur.get("away"),(int,float)):
                return int(cur["home"]),int(cur["away"])
    return None


def set_distribution(ph):
    # Coherent best-of-five approximation from a conservative set-strength transform.
    # Used as exploratory test output, not calibrated betting advice.
    ps=clamp(.5+(ph-.5)*.72,.18,.82); q=1-ps
    probs={"3-0":ps**3,"3-1":3*ps**3*q,"3-2":6*ps**3*q*q,
           "2-3":6*q**3*ps*ps,"1-3":3*q**3*ps,"0-3":q**3}
    z=sum(probs.values()) or 1
    return {k:v/z for k,v in probs.items()}


def render(m):
    hn,an=names(m); model=pre_model(m); ph,pa=model["ph"],model["pa"]
    live=is_live(m); scores=m.get("scores") or {}; winner=hn if ph>=pa else an; wp=max(ph,pa)
    intu=intuition(wp,model["info"]); dist=set_distribution(ph)
    lg=m.get("league") or {}; country=lg.get("country") or ""; comp=lg.get("name") or "Competición"
    lines=[f"🏐 BOTS VÓLEY — V{VERSION}",f"{hn} vs {an}",f"🏆 {comp}" + (f" · {country}" if country else "")]
    if live:
        lines += [f"⏱ LIVE · Sets {scores.get('home','?')}-{scores.get('away','?')}"]
        pts=live_points(m)
        if pts: lines += [f"Set actual: {pts[0]}-{pts[1]}"]
        lines += ["🧭 PRE → LIVE: el PRE sigue como base; el estado real solo ajusta cuando hay datos explícitos."]
    else: lines += ["⏱ PRE"]
    lines += ["", "🏆 GANADOR DEL PARTIDO", f"{hn}: {ph*100:.1f}%", f"{an}: {pa*100:.1f}%", f"Intuición: {intu}"]
    lines += ["", "🎯 RESULTADO DE SETS (exploratorio)"]
    for k in ("3-0","3-1","3-2","2-3","1-3","0-3"): lines.append(f"{k}: {dist[k]*100:.1f}%")
    lines += ["", "🏐 GANADOR DEL SET", "NO DISPONIBLE con respaldo suficiente en esta versión."]
    lines += ["", "📊 TOTAL DE PUNTOS", "NO DISPONIBLE si el proveedor no entrega datos suficientes; no se inventa una línea."]
    lines += ["", "⚖️ HÁNDICAP DE PUNTOS/SETS", "NO DISPONIBLE con respaldo suficiente en esta versión."]
    lines += ["", "📊 RESPALDO", f"Información: {'MEDIA' if model['info']>=60 else 'BAJA'} ({model['info']}%)"]
    if model["hs"] and model["as"]:
        lines += [f"Muestra de tabla: {min(model['hs']['n'],model['as']['n'])} partidos por lado como máximo comparable."]
    else: lines += ["Historial/tabla insuficiente para una lectura fuerte."]
    lines += ["", "🔥 APUESTAS DE PRUEBA"]
    if model["info"]>=60 and wp>=.65:
        lines += [f"1. Ganador del partido: {winner} — modelo {wp*100:.1f}%", "🟡 SOLO PRUEBA / REGISTRO. Aún no validado históricamente fuera de muestra."]
    else:
        lines += ["SIN PROPUESTA CONFIABLE.", "Registrar el partido sirve para calibrar; no se fuerza una apuesta."]
    return "\n".join(lines)

def choose(chat_id,m): STATE[chat_id]=m; send(chat_id,render(m))

def handle(chat_id,text):
    t=(text or "").strip()
    if not t:return
    log.info("MSG chat=%s text=%r",chat_id,t[:120])
    if t.lower() in {"/start","start","inicio"}:
        send(chat_id,"🏐 BOTS VÓLEY listo.\n\n• PARTIDOS DE HOY = catálogo mundial disponible en API-SPORTS\n• Escribe un equipo = buscar primero en el catálogo y luego por equipo en la cobertura mundial\n• 1, 2, 3... = seleccionar\n• AHORA = reanalizar seleccionado"); return
    if is_catalog_command(t):
        d,catalog=today_catalog()
        if d.get("errors"):
            log.error("Catalog API error: %s",d.get("errors"))
            send(chat_id,"⚠️ API-SPORTS respondió con un error al consultar el catálogo de hoy. Revisa Deploy Logs; no lo trataré como 'partido no encontrado'.")
            return
        log.info("CATALOG today count=%d",len(catalog))
        list_catalog(chat_id,catalog); return
    if t.upper()=="AHORA":
        m=STATE.get(chat_id)
        if not isinstance(m,dict) or m.get("_options"):
            send(chat_id,"No hay partido seleccionado. Escribe PARTIDOS DE HOY o el nombre de un equipo."); return
        d,catalog=today_catalog()
        if d.get("errors"):
            send(chat_id,"⚠️ No pude actualizar AHORA por un error de API-SPORTS."); return
        mid=str(m.get("id")); fresh=next((x for x in catalog if str(x.get("id"))==mid),m)
        STATE[chat_id]=fresh; send(chat_id,render(fresh)); return
    if t.isdigit() and isinstance(STATE.get(chat_id),dict) and STATE[chat_id].get("_options"):
        opts=STATE[chat_id]["_options"]; i=int(t)-1
        if 0<=i<len(opts): choose(chat_id,opts[i]); return
        send(chat_id,"Ese número no corresponde a la lista actual."); return
    d,catalog=today_catalog()
    if d.get("errors"):
        log.error("Search API error: %s",d.get("errors"))
        send(chat_id,"⚠️ API-SPORTS respondió con un error. La búsqueda no se marcará como 'sin partido'. Revisa Deploy Logs."); return
    ms=find_matches(t,catalog)
    log.info("SEARCH query=%r catalog=%d matches=%d",t,len(catalog),len(ms))
    if not ms:
        log.info("SEARCH fallback_global query=%r",t)
        ms,reason=global_team_search(t)
        log.info("SEARCH fallback_global query=%r matches=%d reason=%s",t,len(ms),reason)
    if not ms:
        send(chat_id,"No pude vincular ese equipo con un encuentro de HOY en la cobertura disponible de API-SPORTS. Esto NO significa que el partido no exista. Prueba con el otro equipo o con ambos nombres: Equipo A vs Equipo B."); return
    if len(ms)==1: choose(chat_id,ms[0]); return
    STATE[chat_id]={"_options":ms}; lines=[f"🏐 Coincidencias de HOY para: {t}"]
    for i,m in enumerate(ms,1):
        h,a=names(m); lines.append(f"{i}. {h} vs {a} · {kickoff_text(m)} · {league_text(m)}")
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
