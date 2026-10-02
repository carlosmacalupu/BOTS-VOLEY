import time
import bot
import analysis_engine as engine

# 1) El motor local debe calcular ganador aunque el horario haya pasado y el estado LIVE no esté confirmado.
m={
    'id':'unknown-anchor-test','timestamp':time.time()-1800,'status':{'short':'UNKNOWN'},
    'teams':{'home':{'name':'Universitario de La Plata','id':None},'away':{'name':'Harrods','id':None}},
    'league':{'name':'FMV Campeonato Oficial — Superiores Primera Femenino'},
    '_best_of':5,'scores':{},'points':{}
}
def summary(sf,sa,n):
    return {'n':n,'wins':max(0,n-1),'sets_for':sf,'sets_against':sa,
            'set_rate':(sf+6)/(sf+sa+12),
            'recent':{'n':n,'sets_for':sf,'sets_against':sa},
            'home':{'n':n,'sets_for':sf,'sets_against':sa},
            'away':{'n':n,'sets_for':sf,'sets_against':sa},
            'rest_days':3,'effective_matches':n,'opponents':[]}
data={'limitations':[],
      'home':{'summary':summary(15,9,6),'matches':[]},
      'away':{'summary':summary(10,14,6),'matches':[]},
      'h2h':[],'common_opponents':[],'_pool':[],'archive':{}}
r=engine.numerical(m,data)
assert not r.get('blocked'), r
assert r.get('markets',{}).get('match'), r

# 2) La capa de selección no debe cancelar el análisis si el refresco falla después del horario.
sent=[]; captured=[]
old_refresh=bot.sources.refresh; old_analyze=bot.engine.analyze; old_send=bot.send
try:
    bot.sources.refresh=lambda match: None
    def fake_analyze(match,api,requested=None):
        captured.append((match['status']['short'],match.get('_state_unconfirmed_after_start')))
        return 'OK_ANALISIS'
    bot.engine.analyze=fake_analyze
    bot.send=lambda cid,text: sent.append(text)
    m2={'id':'choose-test','timestamp':time.time()-1800,'status':{'short':'NS'},
        'teams':{'home':{'name':'Universitario de La Plata'},'away':{'name':'Harrods'}},
        'league':{'name':'FMV'},'_source':'fmv_fast'}
    bot.choose(999999,m2,refresh=True,use_cache=False)
finally:
    bot.sources.refresh=old_refresh; bot.engine.analyze=old_analyze; bot.send=old_send
assert captured==[('UNKNOWN',True)], captured
assert sent==['OK_ANALISIS'], sent
print('OK — estado UNKNOWN/hora superada conserva análisis PRE anclado y no corta el flujo')
