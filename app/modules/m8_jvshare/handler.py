from flask import Blueprint,request,jsonify
from app.errors import DomainError
from app import store
from app.modules.m8_jvshare.demo import ensure_seeded
bp=Blueprint("jvshare",__name__)
@bp.route("/api/jvshare",methods=["GET"])
def state():
    ensure_seeded()
    return jsonify(store.jv_state())
@bp.route("/api/jvshare/switch",methods=["POST"])
def switch():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    return jsonify(store.jv_switch(payload.get("jv"),payload.get("period"),payload.get("net"),payload.get("reported"),payload.get("agreement"),run_id=str(payload.get("run_id") or "")))
@bp.route("/api/jvshare/sign",methods=["POST"])
def sign():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    return jsonify(store.jv_sign(payload.get("jv"),payload.get("period")))
