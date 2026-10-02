import inspect
import bot
from edge_market import devig
from edge_engine import EdgeInput, evaluate
from edge_scanner import MarketSnapshot, scan_totals, select_nonredundant


def test_display_version():
    assert bot.DISPLAY_VERSION == "BOTS_BETANO_MASTER_UNICO_V11_MODO_CONSERVADOR"


def test_valor_command_is_on_demand():
    src = inspect.getsource(bot.answer)
    assert 'cmd == "/valor"' in src
    assert 'edge_value_active' in src


def test_valor_reuses_anchor_before_market():
    src = inspect.getsource(bot.edge_value_active)
    assert 'store.get_anchor' in src
    assert src.index('store.get_anchor') < src.index('get_market')
    assert 'OpenAI' in src or 'OPENAI' not in src


def test_devig():
    p = devig({"over": 1.91, "under": 1.95})
    assert abs(sum(p.values()) - 1) < 1e-9


def test_bad_low_odds_rejected():
    r = evaluate(EdgeInput("totals", "over", 0.5, 0.95, 0.97, 1.03, 92, "senior"))
    assert r.ev_safe < 0
    assert r.entry_status.startswith("🔴")


def test_robust_gap_green():
    r = evaluate(EdgeInput("totals", "over", 1.5, 0.95, 0.875, 1.12, 94, "senior", disagreement=.01))
    assert r.edge_safe > .025
    assert r.entry_status.startswith("🟢")


def test_nonredundant():
    snap = MarketSnapshot(
        probs={"over": {0.5:.90,1.5:.74}, "under": {4.5:.80,5.5:.90}},
        prices={"over": {0.5:1.12,1.5:1.36}, "under": {4.5:1.28,5.5:1.12}},
    )
    rs = scan_totals(p_over={0.5:.97,1.5:.86}, p_under={4.5:.91,5.5:.97},
                     snapshot=snap, quality=95, category="senior", lines=(.5,1.5,4.5,5.5))
    picks = select_nonredundant(rs)
    assert len([x for x in picks if x.side == "over"]) <= 1
    assert len([x for x in picks if x.side == "under"]) <= 1


if __name__ == '__main__':
    tests=[v for k,v in globals().copy().items() if k.startswith('test_') and callable(v)]
    for t in tests:
        t(); print('OK', t.__name__)
    print(f'{len(tests)}/{len(tests)}')
