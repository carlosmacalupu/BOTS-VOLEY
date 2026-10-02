import bot
from edge_engine import EdgeInput, evaluate
from conservative import evaluate_conservative

def mk(cat='senior', p=.995, pm=.95, odds=1.04, q=95, dis=.01):
    r=evaluate(EdgeInput('total_goals_ft','over',.5,p,pm,odds,q,cat,disagreement=dis))
    return evaluate_conservative(r, disagreement=dis)

def test_version():
    assert bot.DISPLAY_VERSION == 'BOTS_BETANO_MASTER_UNICO_V11_MODO_CONSERVADOR'

def test_strong_senior_green():
    assert mk().status.startswith('🟢')

def test_103_needs_real_margin():
    d=mk(p=.979, pm=.95, odds=1.03, q=95, dis=.01)
    assert d.status.startswith('🔴')

def test_youth_blocked():
    assert mk(cat='youth').status.startswith('🔴')

def test_requires_ai_consensus():
    r=evaluate(EdgeInput('total_goals_ft','over',.5,.995,.95,1.04,95,'senior'))
    assert evaluate_conservative(r, disagreement=None).status.startswith('🔴')

def test_low_quality_blocked():
    assert mk(q=80).status.startswith('🔴')

def test_command_present():
    import inspect
    assert 'cmd == "/seguro"' in inspect.getsource(bot.answer)

if __name__=='__main__':
    ts=[v for k,v in globals().copy().items() if k.startswith('test_') and callable(v)]
    for t in ts: t(); print('OK',t.__name__)
    print(f'{len(ts)}/{len(ts)}')
