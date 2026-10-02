import os,tempfile,importlib.util,time
from datetime import datetime,timedelta
os.environ['TELEGRAM_BOT_TOKEN']='test'
os.environ['VOLEY_DATA_DIR']=tempfile.mkdtemp(prefix='voley_catalog_')
os.environ['VOLEY_CATALOG_TTL_SECONDS']='600'
spec=importlib.util.spec_from_file_location('bot_catalog','bot.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

day=datetime.now(b.LIMA).date().isoformat()
base=datetime.combine(datetime.now(b.LIMA).date(),datetime.min.time(),tzinfo=b.LIMA)+timedelta(hours=18)

def mm(src,eid,h,a,league,mins=0):
    return b.sources.make_match(src,eid,(base+timedelta(minutes=mins)).timestamp(),h,a,league,'NS',{})

api=mm('api','1','Alpha Volley','Beta Club','Liga Global',0)
sofa=mm('sofa','2','Gamma Voley','Delta VC','Liga Women',60)
sports=mm('sportsdb','3','Epsilon','Zeta','National League',120)
dupe=mm('sofa','22','Alpha Volley','Beta Club','Liga Global',5)
fmv=mm('fmv','4','Universitario de La Plata','Harrods','FMV Campeonato Oficial',180)
fmv['_team_aliases']={'home':['ULP','Universitario LP'],'away':['HARRODS']}

calls={'catalog':0,'fmv':0,'flash':0}
def fake_catalog(d=None):
    calls['catalog']+=1
    assert d==day
    return [api,sofa,sports,dupe],{'api':['ok'],'sofa':['ok'],'sportsdb':['ok']}
def fake_fmv(d=None,force=False):
    calls['fmv']+=1
    return [fmv]
def fake_flash(d=None):
    calls['flash']+=1
    return [{'day':day,'source':'flashscore','url':'https://x/match/1/','home':'Omega','away':'Sigma','league':'X'}]

b.sources.catalog=fake_catalog
b.fmv_fast_catalog=fake_fmv
b._flashscore_snapshot=fake_flash
rows,meta=b.refresh_sovereign_catalog(day,force=True)
assert len(rows)==4,(len(rows),rows)
assert calls=={'catalog':1,'fmv':1,'flash':1},calls
mem=b.memory_day_rows(day)
assert len(mem)==4,len(mem)
assert b.memory_matches('ULP',day), 'alias ULP no indexado'
assert b.memory_matches('Gama Voley',day) or b.memory_matches('Gamma Voley',day), 'fuzzy/local no resuelve Gamma'
state=b._catalog_state(day)
assert state and state['match_count']==4,state
print('SELFTEST_CATALOGO_SOBERANO OK — 4 fuentes fusionadas, persistencia y alias local')
