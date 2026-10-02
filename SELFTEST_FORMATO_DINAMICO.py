import bot

def packet(best=5):
    return {
      'match':{'teams':{'home':{'name':'Universitario de La Plata'},'away':{'name':'Harrods'}},
               'league':{'name':'Campeonato Oficial FMV — Superiores Primera Femenino'},
               'status':{'short':'NS'},'_best_of':best,'timestamp':1790901000,'scores':{},'points':{}},
      'numerical':{'markets':{'match':{'home':.84,'away':.16}},'model':'test','technical_evidence':{'comparisons':[1,2]},'blocked':None},
      'initial_numerical':{'markets':{'match':{'home':.81,'away':.19}}},
      'history':{'home':{'summary':{'n':8}},'away':{'summary':{'n':7}}},
      'web':{'independent_home_pct':88,'independent_away_pct':12,'independent_confidence':'high'},
      'research_status':'available'}

def decision():
    return {'final_home_pct':87,'final_away_pct':13,'agreement':'agree','decision':'EXPERIMENTAL_LEAN','side':'home','market':'match','reason':'Prueba','risk':'Prueba'}

out=bot.engine.format_result(packet(5),decision())
for key in ['📚 CALIDAD DEL ESTUDIO','🧭 CORREDOR PROTEGIDO','📈 ESCALERA · SETS DEL FAVORITO','⚖️ HÁNDICAP DE SETS','📊 TOTAL DE SETS','🧩 MARCADORES PROBABLES','🤖 BOT LOCAL','🌐 CHATGPT INDEPENDIENTE','⚖️ COTEJO FINAL']:
    assert key in out,key
assert '+2.5 SETS' in out and '3–0' in out

p3=packet(3);p3['match']['league']['name']='Liga Juvenil U19 Women'
out3=bot.engine.format_result(p3,decision())
assert '+2.5 SETS' in out3 and 'U2.5 SETS' in out3
assert '2–0' in out3 and '3–0' not in out3
print('SELFTEST_FORMATO_DINAMICO OK')
