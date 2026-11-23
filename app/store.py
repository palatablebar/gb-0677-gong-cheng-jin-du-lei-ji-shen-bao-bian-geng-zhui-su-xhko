import sqlite3,json
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError
from app.values import number, money
from app.modules.m8_jvshare.rules import build_certificate, validate_shares, valid_period
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS jv_agreements (jv TEXT NOT NULL,agreement_id TEXT NOT NULL,effective TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,PRIMARY KEY (jv,agreement_id))")
    db.execute("CREATE TABLE IF NOT EXISTS jv_shares (jv TEXT NOT NULL,agreement_id TEXT NOT NULL,member TEXT NOT NULL,ratio TEXT NOT NULL,PRIMARY KEY (jv,agreement_id,member))")
    db.execute("CREATE TABLE IF NOT EXISTS jv_certificates (jv TEXT NOT NULL,period TEXT NOT NULL,net TEXT NOT NULL,reported TEXT NOT NULL,baseline TEXT NOT NULL,agreement_id TEXT NOT NULL,splits TEXT NOT NULL,status TEXT NOT NULL,run_id TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL,PRIMARY KEY (jv,period))")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS jv_single_current ON jv_certificates(jv) WHERE status='current'")
    return db
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)
def jv_has_data():
    with connect() as db: return db.execute("SELECT 1 FROM jv_agreements LIMIT 1").fetchone() is not None
def jv_state():
    with connect() as db:
        agreements=db.execute("SELECT jv,agreement_id,effective,status,created_at FROM jv_agreements ORDER BY jv,rowid").fetchall()
        shares=db.execute("SELECT jv,agreement_id,member,ratio FROM jv_shares ORDER BY jv,agreement_id,rowid").fetchall()
        certs=db.execute("SELECT jv,period,net,reported,baseline,agreement_id,splits,status,run_id,version,updated_at FROM jv_certificates ORDER BY jv,period").fetchall()
    sharemap={}
    for jv,aid,member,ratio in shares: sharemap.setdefault((jv,aid),{})[member]=ratio
    jvs={}
    for jv,aid,effective,status,created in agreements:
        j=jvs.setdefault(jv,{"jv":jv,"members":[],"agreements":[],"certificates":[],"current":None,"signed_baseline":{}})
        j["agreements"].append({"id":aid,"effective":effective,"status":status,"created_at":created,"shares":sharemap.get((jv,aid),{})})
    for jv,period,net,reported,baseline,aid,splits_json,status,run_id,version,updated in certs:
        splits=json.loads(splits_json)
        cert={"period":period,"net":net,"reported":reported,"difference":money(number(net)-number(reported)),
              "baseline":baseline,"cumulative":money(number(baseline)+number(net)),"agreement_id":aid,
              "splits":splits,"status":status,"run_id":run_id,"version":version,"updated_at":updated,
              "members_sum":money(sum((number(s["amount"]) for s in splits),Decimal(0))),
              "uncarried_total":money(sum((number(s.get("uncarried","0")) for s in splits),Decimal(0)))}
        cert["balanced"]=cert["members_sum"]==cert["net"]
        cert["by_member"]={s["member"]:s for s in splits}
        jvs[jv]["certificates"].append(cert)
        if status=="current": jvs[jv]["current"]=cert
    for j in jvs.values():
        if j["agreements"]: j["members"]=list(j["agreements"][0]["shares"])
        totals={m:Decimal(0) for m in j["members"]};signed_total=Decimal(0)
        for c in j["certificates"]:
            if c["status"]!="signed": continue
            signed_total+=number(c["net"])
            for s in c["splits"]: totals[s["member"]]=totals.get(s["member"],Decimal(0))+number(s["amount"])
        j["signed_baseline"]={"total":money(signed_total),"members":{m:money(v) for m,v in totals.items()}}
    return {"jvs":list(jvs.values())}
def jv_seed(jv,agreement,certificates):
    "空库时一次事务写入演示联营体；已有数据或并发冲突则跳过。"
    now=datetime.now(timezone.utc).isoformat()
    try:
        with connect() as db:
            if db.execute("SELECT 1 FROM jv_agreements LIMIT 1").fetchone(): return False
            db.execute("INSERT INTO jv_agreements(jv,agreement_id,effective,status,created_at) VALUES (?,?,?,'active',?)",(jv,agreement["id"],agreement["effective"],now))
            for member,ratio in agreement["shares"].items():
                db.execute("INSERT INTO jv_shares(jv,agreement_id,member,ratio) VALUES (?,?,?,?)",(jv,agreement["id"],member,str(number(ratio))))
            for c in certificates:
                db.execute("INSERT INTO jv_certificates(jv,period,net,reported,baseline,agreement_id,splits,status,run_id,version,updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                           (jv,c["period"],c["net"],c["reported"],c["baseline"],agreement["id"],json.dumps(c["splits"],ensure_ascii=False,sort_keys=True),c["status"],c.get("run_id",""),now))
        return True
    except sqlite3.IntegrityError: return False
def jv_switch(jv,period,net,reported,agreement,run_id=""):
    "把现行签证切换到指定份额协议；单事务，失败整体回滚保留旧版；同协议重试幂等。"
    jv=str(jv or "").strip()
    if not jv: raise DomainError("BAD_JV","联营体不能为空")
    period=valid_period(period)
    if not isinstance(agreement,dict): raise DomainError("BAD_AGREEMENT","需要份额协议")
    aid=str(agreement.get("id","")).strip()
    if not aid: raise DomainError("BAD_AGREEMENT","协议号不能为空")
    effective=valid_period(agreement.get("effective"))
    shares=validate_shares(agreement.get("shares"))
    if effective>period: raise DomainError("AGREEMENT_NOT_EFFECTIVE","协议生效月份晚于签证周期",409)
    now=datetime.now(timezone.utc).isoformat();replayed=False
    try:
        with connect() as db:
            row=db.execute("SELECT status FROM jv_certificates WHERE jv=? AND period=?",(jv,period)).fetchone()
            if row and row[0]=="signed": raise DomainError("SIGNED_IMMUTABLE","该月签证已签出，历史不得回写",409)
            latest_signed=db.execute("SELECT MAX(period) FROM jv_certificates WHERE jv=? AND status='signed'",(jv,)).fetchone()[0]
            if latest_signed and period<latest_signed: raise DomainError("BACKDATING","新签证周期须晚于全部已签月份",409)
            members=[r[0] for r in db.execute("SELECT member FROM jv_shares WHERE jv=? GROUP BY member ORDER BY MIN(rowid)",(jv,))]
            if members and set(members)!=set(shares): raise DomainError("MEMBER_SET_CHANGED","成员构成与既有协议不一致，历史依据不可变更",409)
            existing=db.execute("SELECT effective FROM jv_agreements WHERE jv=? AND agreement_id=?",(jv,aid)).fetchone()
            if existing:
                stored={m:number(r) for m,r in db.execute("SELECT member,ratio FROM jv_shares WHERE jv=? AND agreement_id=?",(jv,aid))}
                if existing[0]!=effective or stored!=shares: raise DomainError("AGREEMENT_CONFLICT","同一协议号内容不一致，协议依据不可改写",409)
            else:
                db.execute("INSERT INTO jv_agreements(jv,agreement_id,effective,status,created_at) VALUES (?,?,?,'active',?)",(jv,aid,effective,now))
                for member,ratio in shares.items():
                    db.execute("INSERT OR IGNORE INTO jv_shares(jv,agreement_id,member,ratio) VALUES (?,?,?,?)",(jv,aid,member,str(ratio)))
            current=db.execute("SELECT period,net,reported,baseline,agreement_id,splits,run_id,version FROM jv_certificates WHERE jv=? AND status='current'",(jv,)).fetchone()
            if current and current[0]!=period: raise DomainError("CURRENT_EXISTS",f"现行签证{current[0]}尚未签认，不能另开{period}",409)
            if net is None:
                if not current: raise DomainError("NET_REQUIRED","开立现行签证需要本期净额与原申报")
                net=current[1]
                if reported is None: reported=current[2]
            net=money(number(net));reported=money(number(reported if reported is not None else net))
            signed_rows=db.execute("SELECT net,splits FROM jv_certificates WHERE jv=? AND status='signed'",(jv,)).fetchall()
            baseline=sum((Decimal(r[0]) for r in signed_rows),Decimal(0))
            totals={m:Decimal(0) for m in shares}
            for _,sp in signed_rows:
                for s in json.loads(sp): totals[s["member"]]=totals.get(s["member"],Decimal(0))+Decimal(s["amount"])
            keep_run_id=run_id or (current[6] if current else "")
            cert=build_certificate(period,net,reported,baseline,totals,shares,aid,keep_run_id)
            splits_json=json.dumps(cert["splits"],ensure_ascii=False,sort_keys=True)
            db.execute("UPDATE jv_agreements SET status='superseded' WHERE jv=? AND agreement_id<>?",(jv,aid))
            db.execute("UPDATE jv_agreements SET status='active' WHERE jv=? AND agreement_id=?",(jv,aid))
            if current:
                if (current[1],current[2],current[3],current[4],current[5],current[6])==(cert["net"],cert["reported"],cert["baseline"],aid,splits_json,keep_run_id):
                    replayed=True
                else:
                    db.execute("UPDATE jv_certificates SET net=?,reported=?,baseline=?,agreement_id=?,splits=?,run_id=?,version=version+1,updated_at=? WHERE jv=? AND period=? AND status='current'",
                               (cert["net"],cert["reported"],cert["baseline"],aid,splits_json,keep_run_id,now,jv,period))
            else:
                db.execute("INSERT INTO jv_certificates(jv,period,net,reported,baseline,agreement_id,splits,status,run_id,version,updated_at) VALUES (?,?,?,?,?,?,?,'current',?,1,?)",
                           (jv,period,cert["net"],cert["reported"],cert["baseline"],aid,splits_json,keep_run_id,now))
    except sqlite3.IntegrityError: raise DomainError("CURRENT_CONFLICT","现行签证唯一性冲突，请刷新后重试",409)
    return {"replayed":replayed,"state":jv_state()}
def jv_sign(jv,period):
    "签认现行签证转为已签历史；已签记录此后任何写操作都不可触及。"
    jv=str(jv or "").strip();period=str(period or "").strip()
    if not jv or not period: raise DomainError("BAD_BODY","需要联营体与周期")
    now=datetime.now(timezone.utc).isoformat()
    with connect() as db:
        current=db.execute("SELECT period FROM jv_certificates WHERE jv=? AND status='current'",(jv,)).fetchone()
        if not current: raise DomainError("NO_CURRENT","没有现行签证可签认",409)
        if current[0]!=period: raise DomainError("PERIOD_MISMATCH",f"现行签证为{current[0]}，不能签认{period}",409)
        db.execute("UPDATE jv_certificates SET status='signed',updated_at=? WHERE jv=? AND period=? AND status='current'",(now,jv,period))
    return jv_state()
