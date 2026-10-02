from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Iterable, List
from edge_engine import EdgeResult

CFG = {
    'senior':   dict(enabled=True,  min_quality=88, min_prob=.975, max_odds=1.18, min_cushion=.015, min_edge=.018, min_ev=.018, max_disagreement=.035),
    'women':    dict(enabled=True,  min_quality=88, min_prob=.980, max_odds=1.16, min_cushion=.020, min_edge=.022, min_ev=.018, max_disagreement=.040),
    'youth':    dict(enabled=False, min_quality=92, min_prob=.985, max_odds=1.14, min_cushion=.025, min_edge=.030, min_ev=.020, max_disagreement=.030),
    'reserve':  dict(enabled=False, min_quality=94, min_prob=.988, max_odds=1.12, min_cushion=.030, min_edge=.035, min_ev=.020, max_disagreement=.025),
    'friendly': dict(enabled=False, min_quality=94, min_prob=.988, max_odds=1.12, min_cushion=.030, min_edge=.035, min_ev=.020, max_disagreement=.025),
}

@dataclass(frozen=True)
class ConservativeDecision:
    market: str
    side: str
    line: float
    odds: Optional[float]
    conservative_prob: float
    break_even_prob: Optional[float]
    cushion: Optional[float]
    quality: int
    category: str
    status: str
    reason: str
    def as_dict(self): return asdict(self)

def evaluate_conservative(r: EdgeResult, *, disagreement: Optional[float]) -> ConservativeDecision:
    c = CFG.get((r.category or 'senior').lower(), CFG['senior'])
    be = r.break_even_prob
    cushion = None if be is None else r.conservative_prob - be
    status = '🔴 NO ENTRAR'
    reason = 'no cumple el filtro de alta exigencia'
    if not c['enabled']:
        reason = 'categoría demasiado volátil para modo conservador de monto alto'
    elif r.decimal_odds is None or be is None:
        reason = 'sin cuota real no se puede validar seguridad/precio'
    elif r.decimal_odds < 1.02 or r.decimal_odds > c['max_odds']:
        reason = f'cuota fuera del corredor conservador 1.02–{c["max_odds"]:.2f}'
    elif r.quality < c['min_quality']:
        reason = f'calidad {r.quality}/100; exige ≥{c["min_quality"]}'
    elif disagreement is None:
        reason = 'sin cotejo modelo/IA; para monto alto se exige consenso'
    elif disagreement > c['max_disagreement']:
        reason = f'discrepancia modelo/IA {disagreement*100:.1f} pp demasiado alta'
    elif r.conservative_prob < c['min_prob']:
        reason = f'probabilidad conservadora {r.conservative_prob*100:.1f}% < {c["min_prob"]*100:.1f}%'
    elif cushion is None or cushion < c['min_cushion']:
        reason = f'colchón sobre break-even insuficiente; exige ≥{c["min_cushion"]*100:.1f} pp'
    elif r.edge_safe < c['min_edge']:
        reason = f'edge seguro insuficiente; exige ≥{c["min_edge"]*100:.1f} pp'
    elif r.ev_safe is None or r.ev_safe < c['min_ev']:
        reason = f'EV conservador insuficiente; exige ≥{c["min_ev"]*100:.1f}%'
    else:
        status = '🟢 ALTA EXIGENCIA APROBADA'
        reason = 'probabilidad, consenso, calidad, edge y precio superan todos los filtros'
    return ConservativeDecision(r.market, r.side, r.line, r.decimal_odds, r.conservative_prob, be, cushion,
                                r.quality, r.category, status, reason)

def rank_conservative(results: Iterable[EdgeResult], disagreement: Optional[float]) -> List[ConservativeDecision]:
    ds = [evaluate_conservative(r, disagreement=disagreement) for r in results]
    return sorted(ds, key=lambda d: (d.status.startswith('🟢'), d.conservative_prob, d.cushion or -1), reverse=True)

def format_conservative(results: Iterable[EdgeResult], disagreement: Optional[float], category_label: str='') -> str:
    ds = rank_conservative(results, disagreement)
    L = ['🛡️ MODO CONSERVADOR · ALTA EXIGENCIA', '']
    if category_label: L.append(f'🧠 Categoría: {category_label}')
    greens = [d for d in ds if d.status.startswith('🟢')]
    if greens:
        for d in greens[:4]:
            label = ('+' if d.side == 'over' else 'U') + f'{d.line:.1f}'
            L += [f'🟢 {label} @{d.odds:.2f} · APROBADA',
                  f'   Prob. conservadora {d.conservative_prob*100:.1f}% · break-even {d.break_even_prob*100:.1f}% · colchón {d.cushion*100:+.1f} pp']
        L += ['', '🎯 SOLO estas líneas superaron todos los filtros de alta exigencia.']
    else:
        L += ['⛔ NO HAY ENTRADA DE ALTA EXIGENCIA', '']
        for d in ds[:4]:
            label = ('+' if d.side == 'over' else 'U') + f'{d.line:.1f}'
            odds = 's/cuota' if d.odds is None else f'@{d.odds:.2f}'
            L.append(f'🔴 {label} {odds} · {d.reason}')
    L += ['', '⚠️ “Aprobada” significa que supera filtros muy estrictos; no significa resultado garantizado.']
    return '\n'.join(L)
