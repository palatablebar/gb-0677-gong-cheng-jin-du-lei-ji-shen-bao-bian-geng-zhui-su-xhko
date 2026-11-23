from decimal import Decimal
from app.pipeline import calculate, load_sample
from app import store
from app.modules.m8_jvshare.rules import build_certificate
JV="JT-01"
AGREEMENT={"id":"XY-2026","effective":"2026-01","shares":{"甲公司":"0.6","乙公司":"0.4"}}
SIGNED_THROUGH="2026-10"
CURRENT_PERIOD="2026-11"
def ensure_seeded():
    "首次进入联营份额工作台时，按常规申报批的实际计量结果铺演示基线：2026-01至2026-10已签，2026-11为现行。"
    if store.jv_has_data(): return
    run=calculate(load_sample("clean.csv"))
    per={}
    for row in run["stages"][5]["rows"]:
        total=per.setdefault(row["period"],[Decimal(0),Decimal(0)])
        total[0]+=Decimal(row["expected"]);total[1]+=Decimal(row["reported"])
    shares={m:Decimal(r) for m,r in AGREEMENT["shares"].items()}
    certs=[];baseline=Decimal(0);totals={m:Decimal(0) for m in shares}
    for period in sorted(p for p in per if p<=SIGNED_THROUGH):
        net,reported=per[period]
        cert=build_certificate(period,net,reported,baseline,totals,shares,AGREEMENT["id"],run["run_id"])
        certs.append({**cert,"status":"signed"})
        baseline+=net
        for s in cert["splits"]: totals[s["member"]]+=Decimal(s["amount"])
    net,reported=per[CURRENT_PERIOD]
    certs.append({**build_certificate(CURRENT_PERIOD,net,reported,baseline,totals,shares,AGREEMENT["id"],run["run_id"]),"status":"current"})
    store.jv_seed(JV,AGREEMENT,certs)
