import json
from decimal import Decimal
import pytest
from app import store
from app.errors import DomainError
from app.modules.m8_jvshare import rules
from app.modules.m8_jvshare.demo import ensure_seeded, JV, AGREEMENT
NEW={"id":"BC-2026-11","effective":"2026-11","shares":{"甲公司":"0.5","乙公司":"0.5"}}
@pytest.fixture
def seeded():
    ensure_seeded()
    return store.jv_state()
def current(state): return state["jvs"][0]["current"]
def signed(state): return [c for c in state["jvs"][0]["certificates"] if c["status"]=="signed"]
def switch(period="2026-11",net=None,reported=None,agreement=None,jv=JV):
    return store.jv_switch(jv,period,net,reported,agreement or NEW)
def test_seed_current_certificate(seeded):
    cur=current(seeded)
    assert cur["period"]=="2026-11" and cur["net"]=="2550.00" and cur["reported"]=="2550.00"
    assert cur["baseline"]=="21250.00" and cur["agreement_id"]=="XY-2026" and cur["balanced"] is True
    assert {s["member"]:s["amount"] for s in cur["splits"]}=={"甲公司":"1530.00","乙公司":"1020.00"}
    assert cur["uncarried_total"]=="0.00"
def test_seed_signed_history_immutable_baseline(seeded):
    assert len(signed(seeded))==10
    assert all(c["status"]=="signed" for c in seeded["jvs"][0]["certificates"][:-1])
    assert seeded["jvs"][0]["signed_baseline"]=={"total":"21250.00","members":{"甲公司":"12750.00","乙公司":"8500.00"}}
def test_apportion_conserves_total():
    parts=rules.apportion(Decimal("100.00"),{"a":Decimal(1)/3,"b":Decimal(1)/3,"c":Decimal(1)/3})
    assert sum(parts.values())==Decimal("100.00") and parts["c"]==Decimal("33.34")
def test_apportion_negative_and_odd_cent():
    neg=rules.apportion(Decimal("-100.00"),{"a":Decimal("0.5"),"b":Decimal("0.5")})
    assert neg=={"a":Decimal("-50.00"),"b":Decimal("-50.00")}
    odd=rules.apportion(Decimal("2550.01"),{"a":Decimal("0.5"),"b":Decimal("0.5")})
    assert odd["a"]+odd["b"]==Decimal("2550.01")
def test_validate_shares_rejects_bad_input():
    with pytest.raises(DomainError) as e: rules.validate_shares({"甲公司":"0.9","乙公司":"0.4"})
    assert e.value.code=="SHARES_NOT_ONE"
    with pytest.raises(DomainError): rules.validate_shares({"甲公司":"1.2","乙公司":"-0.2"})
    with pytest.raises(DomainError): rules.validate_shares({"甲公司":"1"})
    with pytest.raises(DomainError): rules.validate_shares({"":"1","乙公司":"0"})
def test_valid_period():
    for bad in ("2026-1","2026-13","2026/11",202611,""):
        with pytest.raises(DomainError): rules.valid_period(bad)
    assert rules.valid_period("2026-11")=="2026-11"
def test_switch_carries_new_share_from_signed_baseline(seeded):
    before={c["period"]:c for c in signed(seeded)}
    state=switch()["state"];cur=current(state)
    assert cur["agreement_id"]=="BC-2026-11" and cur["balanced"] is True
    splits={s["member"]:s for s in cur["splits"]}
    assert splits["甲公司"]["amount"]=="1275.00" and splits["乙公司"]["amount"]=="1275.00"
    assert cur["members_sum"]==cur["net"]=="2550.00"
    assert splits["甲公司"]["signed_amount"]=="12750.00" and splits["甲公司"]["recognized"]=="14025.00"
    assert splits["甲公司"]["uncarried"]=="-2125.00" and splits["乙公司"]["uncarried"]=="2125.00"
    assert cur["uncarried_total"]=="0.00"
    after={c["period"]:c for c in signed(state)}
    assert before==after
def test_switch_records_declaration_and_basis(seeded):
    cur=current(switch()["state"])
    assert cur["reported"]=="2550.00" and cur["difference"]=="0.00"
    assert cur["agreement_id"]=="BC-2026-11"
    agreements={a["id"]:a for a in cur and store.jv_state()["jvs"][0]["agreements"]}
    assert agreements["BC-2026-11"]["status"]=="active"
    assert agreements["XY-2026"]["status"]=="superseded"
    assert agreements["XY-2026"]["shares"]=={"甲公司":"0.6","乙公司":"0.4"}
def test_switch_retry_is_idempotent(seeded):
    switch()
    again=switch()
    assert again["replayed"] is True
    state=again["state"]
    assert len(state["jvs"][0]["agreements"])==2
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jv_shares WHERE agreement_id='BC-2026-11'").fetchone()[0]==2
    cur=current(state)
    assert cur["version"]==2 and cur["agreement_id"]=="BC-2026-11"
def test_switch_conflict_keeps_agreement_basis(seeded):
    switch()
    with pytest.raises(DomainError) as e:
        switch(agreement={"id":"BC-2026-11","effective":"2026-11","shares":{"甲公司":"0.7","乙公司":"0.3"}})
    assert e.value.code=="AGREEMENT_CONFLICT"
    agreements={a["id"]:a for a in store.jv_state()["jvs"][0]["agreements"]}
    assert agreements["BC-2026-11"]["shares"]=={"甲公司":"0.5","乙公司":"0.5"}
def test_failed_switch_keeps_old_version(seeded):
    with pytest.raises(DomainError) as e:
        switch(agreement={"id":"BC-2026-11","effective":"2026-11","shares":{"甲公司":"0.7","乙公司":"0.4"}})
    assert e.value.code=="SHARES_NOT_ONE"
    state=store.jv_state();cur=current(state)
    assert cur["agreement_id"]=="XY-2026" and cur["version"]==1
    assert [a["id"] for a in state["jvs"][0]["agreements"]]==["XY-2026"]
def test_failed_switch_rolls_back_new_agreement(seeded):
    with pytest.raises(DomainError) as e: switch(period="2026-12",net="2125.00")
    assert e.value.code=="CURRENT_EXISTS"
    assert [a["id"] for a in store.jv_state()["jvs"][0]["agreements"]]==["XY-2026"]
def test_signed_month_never_rewritten(seeded):
    store.jv_sign(JV,"2026-11")
    with pytest.raises(DomainError) as e: switch()
    assert e.value.code=="SIGNED_IMMUTABLE"
    cur=current(store.jv_state())
    assert cur is None and len(signed(store.jv_state()))==11
def test_backdating_rejected(seeded):
    with pytest.raises(DomainError) as e:
        switch(period="2025-12",net="1000.00",agreement={"id":"BC-2025","effective":"2025-01","shares":{"甲公司":"0.5","乙公司":"0.5"}})
    assert e.value.code=="BACKDATING"
def test_member_set_fixed(seeded):
    with pytest.raises(DomainError) as e:
        switch(agreement={"id":"BC-2026-11","effective":"2026-11","shares":{"甲公司":"0.5","丙公司":"0.5"}})
    assert e.value.code=="MEMBER_SET_CHANGED"
def test_agreement_not_effective_rejected(seeded):
    with pytest.raises(DomainError) as e:
        switch(agreement={"id":"BC-2026-12","effective":"2026-12","shares":{"甲公司":"0.5","乙公司":"0.5"}})
    assert e.value.code=="AGREEMENT_NOT_EFFECTIVE"
def test_sign_then_open_next_period(seeded):
    switch()
    store.jv_sign(JV,"2026-11")
    state=switch(period="2026-12",net="2125.00",reported="2000.00")["state"]
    cur=current(state)
    assert cur["period"]=="2026-12" and cur["baseline"]=="23800.00" and cur["agreement_id"]=="BC-2026-11"
    assert cur["balanced"] is True and cur["members_sum"]=="2125.00"
def test_sign_requires_current(seeded):
    with pytest.raises(DomainError) as e: store.jv_sign(JV,"2026-12")
    assert e.value.code=="PERIOD_MISMATCH"
def test_api_flow(client):
    assert client.get("/api/jvshare").status_code==200
    r=client.post("/api/jvshare/switch",json={"jv":JV,"period":"2026-11","agreement":NEW})
    assert r.status_code==200
    cur=r.get_json()["state"]["jvs"][0]["current"]
    assert cur["agreement_id"]=="BC-2026-11" and cur["balanced"] is True
    assert client.post("/api/jvshare/sign",json={"jv":JV,"period":"2026-11"}).status_code==200
    r=client.post("/api/jvshare/switch",json={"jv":JV,"period":"2026-11","agreement":NEW})
    assert r.status_code==409 and r.get_json()["code"]=="SIGNED_IMMUTABLE"
def test_api_page_renders(client):
    assert client.get("/jvshare").status_code==200
