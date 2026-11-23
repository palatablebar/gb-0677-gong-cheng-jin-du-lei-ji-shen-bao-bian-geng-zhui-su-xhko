# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

联营份额工作台：GET /api/jvshare返回联营体现行签证、协议台账与已签历史。POST /api/jvshare/switch接收jv、period、agreement{id,effective,shares}，可带net、reported、run_id；已签周期返回409 SIGNED_IMMUTABLE，早于已签月份返回409 BACKDATING，同号协议内容不一致返回409 AGREEMENT_CONFLICT，份额合计不为1返回422 SHARES_NOT_ONE；整个切换单事务提交，失败保留旧版，同一协议重试返回replayed=true且不新增份额记录。POST /api/jvshare/sign接收jv、period，把现行签证转为已签历史。
