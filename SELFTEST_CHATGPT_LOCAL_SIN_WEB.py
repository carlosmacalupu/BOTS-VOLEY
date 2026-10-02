import os, importlib.util, time
os.environ['OPENAI_API_KEY']='test-key'
os.environ['VOLEY_ANALYSIS_TOTAL_SECONDS']='28'
spec=importlib.util.spec_from_file_location('bot_v113','/mnt/data/v113_clean/bot.py')
bot=importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
e=bot.engine

match={
 'id':'test-opole','timestamp':time.time()-1800,
 'teams':{'home':{'name':'Opole W','id':1},'away':{'name':'Chemik Police W','id':2}},
 'league':{'name':'TAURON Liga Women','id':99,'season':2026},
 'status':{'short':'LIVE'},'scores':{'home':1,'away':2},'points':{'home':0,'away':0},
 '_best_of':5,'_source':'api','_fetched_at':time.time()
}
summary={'n':8,'wins':4,'sets_for':15,'sets_against':14,'recent':{'n':5,'wins':3,'sets_for':10,'sets_against':8},
         'home':{'n':4,'wins':2,'sets_for':8,'sets_against':7},'away':{'n':4,'wins':2,'sets_for':7,'sets_against':7},
         'effective_matches':7.0,'rest_days':3.0,'seasons':['2026'],'opponents':['A','B']}
data={'home':{'summary':summary,'matches':[],'missing':[]},'away':{'summary':summary,'matches':[],'missing':[]},
      'h2h':[],'common_opponents':[],'limitations':['test'],'_pool':[],'archive':{}}
stats={'status':'LIVE','markets':{'match':{'home':.26,'away':.74}},'limitations':[], 'technical_evidence':{'comparisons':[],'evidence_ids':[]}}

def fake_collect(m,api): return data

def fake_web(*a,**k): raise e.AIError('TimeoutError')

def fake_response(prompt,schema,search=False,discovery=False,timeout_override=None):
    assert search is False, 'local route must not request web'
    parsed={'historical_games':[],'measurements':[],'facts':[],'missing_layers':list(e.LAYERS),
            'independent_lean':'away','independent_home_pct':31,'independent_away_pct':69,
            'independent_confidence':'medium','assessment':'Chemik lidera 2-1 y el expediente local favorece su posición actual.'}
    return parsed,[],{'id':'mock-local','model':'mock','usage':{}}

def fake_numerical(m,d,requested_set=None): return stats

def fake_integrate(m,d,s,w):
    ah=(w or {}).get('independent_home_pct')
    return {'match':e.public_match(m),'numerical':s,'history':{'home':d['home'],'away':d['away']},'modules':{},'web':w,'origins':[],
            'blockers':[],'comparison':{'local_home_pct':26,'ai_home_pct':ah,'local_pick':'away','ai_pick':'away' if isinstance(ah,int) and ah<50 else 'unclear','same_pick':True,'ai_confidence':(w or {}).get('independent_confidence')},
            'allowed_decisions':['WAIT','EXPERIMENTAL_LEAN']}

def fake_final(packet,timeout_override=None):
    assert packet['web']['independent_away_pct']==69
    return {'decision':'EXPERIMENTAL_LEAN','market':'match','side':'away','final_home_pct':28,'final_away_pct':72,
            'agreement':'agree','reason':'BOT local y ChatGPT independiente coinciden a favor de Chemik.','risk':'Web adicional no disponible.','evidence_ids':['match:identity'],'meta':{}}

e.collect=fake_collect
e.research=fake_web
e.response_json=fake_response
e.numerical=fake_numerical
e.enrich_history=lambda m,d,w:d
e.integrate=fake_integrate
e.final_review=fake_final
e.safe_refresh=lambda m:m
e.audit=lambda *a,**k:None
out=e.analyze(match,lambda *a,**k:{},None)
print(out)
assert '🌐 CHATGPT INDEPENDIENTE' in out
assert 'Chemik Police W · 69%' in out
assert 'N/D · investigación no disponible' not in out
assert 'ChatGPT analizó expediente local' in out
assert 'Chemik Police W · 72.0%' in out
print('SELFTEST_CHATGPT_LOCAL_SIN_WEB_OK')
