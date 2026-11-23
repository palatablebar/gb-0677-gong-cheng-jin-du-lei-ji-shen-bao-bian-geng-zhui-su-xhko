from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from app.values import number, money
from app.errors import DomainError
CENT = Decimal("0.01")
def valid_period(period):
    if not isinstance(period, str) or len(period) != 7: raise DomainError("BAD_PERIOD", "周期需为YYYY-MM")
    try: datetime.strptime(period, "%Y-%m")
    except ValueError: raise DomainError("BAD_PERIOD", "周期需为YYYY-MM")
    return period
def validate_shares(shares):
    if not isinstance(shares, dict) or len(shares) < 2: raise DomainError("BAD_SHARES", "份额需覆盖至少两名成员")
    ratios = {}
    for member, raw in shares.items():
        name = str(member).strip()
        if not name: raise DomainError("BAD_MEMBER", "成员名不能为空")
        ratio = number(raw)
        if not Decimal(0) <= ratio <= 1: raise DomainError("BAD_SHARES", "份额须在0与1之间")
        ratios[name] = ratio
    if sum(ratios.values(), Decimal(0)) != Decimal(1): raise DomainError("SHARES_NOT_ONE", "份额合计必须等于1")
    return ratios
def cents(value, label):
    try: return number(value).quantize(CENT, rounding=ROUND_HALF_UP)
    except DomainError as e: raise DomainError(e.code, f"{label}无效")
def apportion(total, ratios):
    "按份额把total分到分，末位成员兜底，合计恒等于total。"
    amounts = {}; remaining = total; items = list(ratios.items())
    for member, ratio in items[:-1]:
        amounts[member] = (total * ratio).quantize(CENT, rounding=ROUND_HALF_UP)
        remaining -= amounts[member]
    amounts[items[-1][0]] = remaining
    return amounts
def build_certificate(period, net, reported, baseline, signed_totals, shares, agreement_id, run_id=""):
    "裁决：新份额自最近已签基线承接，仅及本期增量；已签历史保持原样；追溯重算之差记未承接量。"
    net = cents(net, "本期净额"); reported = cents(reported, "原申报"); baseline = cents(baseline, "已签基线")
    cumulative = baseline + net
    current = apportion(net, shares)
    wished = apportion(cumulative, shares)
    splits = []
    for member, ratio in shares.items():
        signed = signed_totals.get(member, Decimal(0))
        splits.append({"member": member, "ratio": str(ratio), "amount": money(current[member]),
                       "signed_amount": money(signed), "recognized": money(signed + current[member]),
                       "uncarried": money(wished[member] - signed - current[member])})
    return {"period": period, "net": money(net), "reported": money(reported), "difference": money(net - reported),
            "baseline": money(baseline), "cumulative": money(cumulative), "agreement_id": agreement_id,
            "run_id": run_id, "splits": splits}
