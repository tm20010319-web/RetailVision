"""Independent read-only MCP surface for operating statistics, not vision."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from agent.analytics.store_business.definitions import QUESTIONS
from agent.analytics.store_business.service import BusinessQueryService
from agent.config_values import strict_bool_env
from .schemas import jsonrpc_error, jsonrpc_result, negotiate_protocol_version, text_result
from .transport import StdioJsonRpcTransport

DOMAINS=("customers","sales","inventory","finance","staff","feedback","marketing","operations","market","procurement")


def tool_definitions() -> list[dict]:
    tools=[]
    for domain in DOMAINS:
        questions=sorted((q for q in QUESTIONS if q["domain"]==domain),key=lambda q:q["id"])
        tools.append({"name":f"query_business_{domain}",
            "description":"查询已落库的门店经营数据并确定性计算。"+"；".join(f'{q["id"]}：{q["question"]}' for q in questions)+
                          "。question_id选最符合问题的一项。客流等为经营数据源记录，本工具不运行摄像头。返回时间窗、统计口径、对比及限制；缺数据不补造。",
            "inputSchema":{"type":"object","additionalProperties":False,
                "properties":{"question_id":{"type":"integer","enum":[q["id"] for q in questions]},
                    "period":{"type":"string","enum":["today","this_week","this_month","last_month","last_30_days","this_year","custom"],"default":"this_month"},
                    "store_id":{"type":"string","description":"省略使用可信默认门店；跨店排行省略时仅比较部署允许的门店。"},
                    "start_date":{"type":"string","description":"custom时必填，YYYY-MM-DD，含当天"},
                    "end_date":{"type":"string","description":"custom时必填，YYYY-MM-DD，含当天"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":20}},
                "required":["question_id"]}})
    return tools


class BusinessMCPServer:
    def __init__(self,service: BusinessQueryService | None) -> None:
        self.service=service
        self.initialized=False

    def handle(self,request: dict) -> dict | None:
        identifier=request.get("id")
        method=request.get("method")
        if method=="initialize":
            params=request.get("params") or {}
            if not isinstance(params,dict):
                return jsonrpc_error(identifier,-32602,"params_object_required")
            self.initialized=True
            instructions="Store operating statistics are disabled."
            if self.service:
                instructions="\n".join((
                    "RetailVision Store Business operating-data queries; separate from anonymous camera interaction tools.",
                    f"Trusted scope: default_store_id={self.service.default_store}; allowed_store_ids={','.join(self.service.allowed_stores)}.",
                    "Trusted store names: "+"; ".join(f"{name}={store}" for store,name in self.service.store_labels.items())+". 指定门店名称时使用对应ID，未知门店不可猜测。",
                    "Trusted store timezone: Asia/Shanghai. 日期按真实当前日期解析；工具若报告覆盖不足，不用历史或记忆补数。",
                    "销售、库存、财务、会员消费、员工、营销、采购、市场与客流统计调用 query_business_*；商品拿起/持有和已确认会员视觉行为仍使用原行为工具。",
                    "Public fact interpretation contract: 预置经营数据已用于用户知情的功能验收。普通回答无需重复数据模式标签；若被问是否真实、数据来源或能否作真实经营决策，必须如实说明当前是预置数据且未接真实业务系统。",
                    "每轮先调用工具，以当前结果回答。允许引用经营工具返回的销售额和转化率；禁止由匿名拿起/持有推断成交或人数。",
                    "Public fact interpretation contract: 工具给出null/unavailable的指标不可当零；不超出period与data_as_of声称实时或最新。样本不足、零分母、无对比期必须说明对应限制。",
                    "Public fact interpretation contract: rows_limited=true仅表示明细只展示了部分行：说明返回行数/排行范围，不推断缺失行；total_rows与非空totals是工具独立查库的完整统计，totals对应所选门店及时间窗。筛选新品、低库存等子集时不提供totals，不能用全店汇总代替该子集。若另标truncated=true则证据正文不完整，按截断证据限制回答。",
                    "Public fact interpretation contract: 遵守interpretation中的指标定义；归因销售不等于因果增量，相关性不等于原因，历史顾客价值不等于LTV。建议不能冒充已执行动作。",
                    "Public fact interpretation contract: classification.status=not_configured时只报告库存、销量、上市日期等事实，整份回答不能给出高库存、滞销、积压、新品或非新品分类，不能用“最像压货”“偏长”“不具备积压特征”等近义判断绕过限制。其他报表的固定库龄分桶只是数量事实，不是此分类标准；查询区间也不是新品期限。先说明未配置标准，再给事实；建议只能是核对数据或确定标准，不能根据自行分类给出处置建议。rankings中每个指标有独立榜单、范围和裁剪状态，不能从另一榜单推断最高值。expiry_details不可用时不能捏造到期日或剩余天数；remaining_days以as_of为基准，不是实时天数；batches_limited=true时批次明细不完整，earliest_expiry仍来自全量批次，不用部分批次数量相加推断总库存。",
                    "Public fact interpretation contract: 汇总比例是先汇总分子与分母再相除，不是各分组比例相加或简单平均；同比环比用返回值，不能自行改统计口径。自定义区间的comparison_period是紧邻前一等长区间，按返回的起止日期描述，不能称为上月同期。",
                    "Public fact interpretation contract: 所有单位为%的value已是百分数，例如0.0803就是0.0803%，不可再乘100。比较大小与排序按实际数值核对，不因行顺序猜其他指标排序；仅有同期点不能声称连续多月趋势。没有因果证据时，不能先断言原因再用免责声明撤回。",
                    "Public fact interpretation contract: 会员信息仅使用工具公开的名称与汇总，不暴露电话、身份原始记录、内部路径或SQL。question_id只是工具查询目录，不是预写答案。",
                    "经营综合报告按用户要求组合经营工具；视觉互动报告才使用原商品/会员行为工具。所有结果按各自时间范围表述。",
                ))
            return jsonrpc_result(identifier,{"protocolVersion":negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"retailvision-store-business","version":"1.0.0"},"instructions":instructions})
        if method in {"notifications/initialized","notifications/cancelled"}:
            return None
        if method=="ping":
            return jsonrpc_result(identifier,{})
        if not self.initialized:
            return jsonrpc_error(identifier,-32002,"server_not_initialized")
        if method=="tools/list":
            return jsonrpc_result(identifier,{"tools":tool_definitions() if self.service else []})
        if method=="tools/call":
            params=request.get("params") or {}
            if not isinstance(params,dict):
                return jsonrpc_error(identifier,-32602,"params_object_required")
            try:
                if self.service is None:
                    raise ValueError("business_mcp_disabled")
                definition=next((t for t in tool_definitions() if t["name"]==params.get("name")),None)
                if definition is None:
                    raise ValueError("business_tool_unknown")
                arguments=params.get("arguments") or {}
                if not isinstance(arguments,dict) or set(arguments)-set(definition["inputSchema"]["properties"]):
                    raise ValueError("business_arguments_invalid")
                question=arguments.get("question_id")
                if type(question) is not int or question not in definition["inputSchema"]["properties"]["question_id"]["enum"]:
                    raise ValueError("business_question_invalid")
                for key in ("period","store_id","start_date","end_date"):
                    if key in arguments and not isinstance(arguments[key],str):
                        raise ValueError("business_arguments_invalid")
                result=text_result(self.service.query(**arguments))
            except ValueError as exc:
                code=str(exc)
                if not code.startswith("business_"):
                    code="business_arguments_invalid"
                result=text_result({"error":code},is_error=True)
            except Exception:
                result=text_result({"error":"business_query_failed"},is_error=True)
            return jsonrpc_result(identifier,result)
        return jsonrpc_error(identifier,-32601,"method_not_found")


def configured_server() -> BusinessMCPServer:
    if not strict_bool_env("RETAILVISION_BUSINESS_MCP_ENABLED","false"):
        return BusinessMCPServer(None)
    path=os.environ.get("RETAILVISION_BUSINESS_DATABASE_PATH","").strip()
    stores=tuple(s.strip() for s in os.environ.get("RETAILVISION_BUSINESS_ALLOWED_STORES","").split(",") if s.strip())
    default=os.environ.get("RETAILVISION_BUSINESS_DEFAULT_STORE","").strip()
    if not path or not Path(path).is_file():
        raise ValueError("business_database_required")
    def threshold(name: str) -> int | None:
        value=os.environ.get(name,"").strip()
        if not value:
            return None
        if not value.isascii() or not value.isdecimal() or int(value)<=0:
            raise ValueError("business_rule_invalid")
        return int(value)
    return BusinessMCPServer(BusinessQueryService(path,allowed_stores=stores,default_store=default,
        high_stock_days=threshold("RETAILVISION_BUSINESS_HIGH_STOCK_DAYS"),
        new_product_days=threshold("RETAILVISION_BUSINESS_NEW_PRODUCT_DAYS")))


def main() -> int:
    for stream in (sys.stdin,sys.stdout):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="strict")
    try:
        server=configured_server()
    except Exception:
        print("business_mcp_configuration_invalid",file=sys.stderr)
        return 2
    StdioJsonRpcTransport(server.handle).serve()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
