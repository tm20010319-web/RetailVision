"""Public measurement definitions and the 100-question report catalogue.

Only trusted, declarative linear expressions are evaluated. No LLM SQL/code.
Amounts are stored in cents; ratios use pooled numerators and denominators.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Indicator:
    label: str
    numerator: tuple[tuple[str, float], ...]
    denominator: tuple[tuple[str, float], ...] = ()
    scale: float = 1
    unit: str = ""


def linear(value: str) -> tuple[tuple[str, float], ...]:
    return tuple((part.lstrip("-"), -1.0 if part.startswith("-") else 1.0)
                 for part in value.replace("-", "+-").split("+") if part)


def number(label: str, source: str, unit: str = "次") -> Indicator:
    return Indicator(label, linear(source), unit=unit)


def money(label: str, source: str) -> Indicator:
    return Indicator(label, linear(source), scale=.01, unit="元")


def ratio(label: str, numerator: str, denominator: str, *, scale: float = 100,
          unit: str = "%") -> Indicator:
    return Indicator(label, linear(numerator), linear(denominator), scale, unit)


I = {
    "entries": number("进店人次", "entries", "人次"),
    "daily_entries": ratio("日均进店人次", "entries", "entry_days", scale=1, unit="人次/天"),
    "new_customers": number("首次购买顾客数", "new_customers", "人"),
    "return_customers": number("此前已购买顾客数", "return_customers", "人"),
    "repeat_rate": ratio("期内购买至少两次的顾客占比", "repeat_customers", "buyers"),
    "old_repeat_rate": ratio("期内再次购买至少两次的老客占比", "repeat_return_customers", "return_customers"),
    "sales": money("销售净额", "sales"),
    "orders": number("已支付订单数", "orders", "单"),
    "ticket": ratio("每单平均销售净额", "sales", "orders", scale=.01, unit="元/单"),
    "units": number("销售件数", "units", "件"),
    "joins": number("新增会员数", "joins", "人"),
    "active_members": number("期内购买会员数", "buyers", "人"),
    "member_total": number("截至期末会员数", "member_total", "人"),
    "member_activity": ratio("购买活跃会员占比", "buyers", "member_total"),
    "dwell": ratio("平均停留时长", "dwell_seconds", "entries", scale=1, unit="秒"),
    "sales_share": ratio("销售净额占比", "sales", "@all_sales"),
    "sales_target": money("所选期间进度目标", "sales_target"),
    "target_rate": ratio("销售目标完成率", "sales", "sales_target"),
    "stock": number("期末库存数量", "stock", "件"),
    "stock_value": money("期末库存成本", "stock_value"),
    "stock_days": ratio("库存可售天数估计", "stock", "@daily_units", scale=1, unit="天"),
    "pairs": number("同篮购买订单数", "pairs", "单"),
    "pair_support": ratio("同篮组合占全部订单比例", "pairs", "@all_orders"),
    "refunds": number("退货件数", "refund_units", "件"),
    "refund_rate": ratio("退货件数占销售件数", "refund_units", "units"),
    "exchanges": number("换货件数", "exchange_units", "件"),
    "exchange_rate": ratio("换货件数占销售件数", "exchange_units", "units"),
    "turnover_days": ratio("期内平均库存成本周转天数", "avg_stock_value", "@daily_cogs", scale=1, unit="天"),
    "safety": number("安全库存线", "safety", "件"),
    "aged": number("库龄超过90天库存", "aged_stock", "件"),
    "aged_value": money("库龄超过90天库存成本", "aged_value"),
    "loss_rate": ratio("损耗件数/期初库存加到货件数", "loss_units", "opening_stock+stock_receipts"),
    "loss_value": money("损耗成本", "loss_value"),
    "stock_share": ratio("库存成本占比", "stock_value", "@all_stock_value"),
    "on_time": ratio("到货准时率", "ontime_deliveries", "deliveries"),
    "stock_sales": ratio("期末库存成本/期间销售净额", "stock_value", "sales"),
    "expiring": number("期末未来7天内到期库存", "expiring_stock", "件"),
    "alert_coverage": ratio("配置库存预警商品占比", "alert_skus", "skus"),
    "count_difference": ratio("盘点绝对差异件数/账面件数", "count_difference", "count_book"),
    "expense": money("费用", "expenses"),
    "cost": money("成本费用", "cost_total"),
    "cost_share": ratio("成本费用/销售净额", "cost_total", "@all_sales"),
    "expense_share": ratio("费用/销售净额", "expenses", "@all_sales"),
    "gross_profit": money("毛利", "sales-cogs"),
    "gross_margin": ratio("毛利率", "sales-cogs", "sales"),
    "net_profit": money("经营净收益（含已入账税费）", "sales-cogs-expenses"),
    "net_margin": ratio("经营净收益率", "sales-cogs-expenses", "sales"),
    "daily_sales": ratio("日均销售净额", "sales", "@days", scale=.01, unit="元/天"),
    "daily_fixed": ratio("日均固定费用", "fixed_expenses", "@days", scale=.01, unit="元/天"),
    "breakeven": ratio("日均盈亏平衡销售额估计", "@daily_fixed", "@contribution_margin", scale=.01, unit="元/天"),
    "staff_hours": number("实际工作工时", "staff_hours", "小时"),
    "sales_per_hour": ratio("每工时销售净额", "sales", "staff_hours", scale=.01, unit="元/小时"),
    "paid": money("支付流水金额", "paid"),
    "payment_share": ratio("支付金额占比", "paid", "@all_paid"),
    "payment_difference": money("支付与结算差额", "paid-settled"),
    "coupon_rate": ratio("所选发行批次优惠券核销率", "coupon_redeemed", "coupon_issued"),
    "coupon_sales": money("使用优惠券订单销售净额", "coupon_sales"),
    "receivable_days": ratio("应收周转天数", "avg_receivable", "@daily_credit_sales", scale=1, unit="天"),
    "payable_days": ratio("应付周转天数", "avg_payable", "@daily_credit_purchase", scale=1, unit="天"),
    "roi": ratio("期间经营净收益/投入资本", "sales-cogs-expenses", "investment"),
    "investment": money("投入资本", "investment"),
    "attendance": ratio("出勤班次率", "attended", "scheduled"),
    "late_rate": ratio("迟到班次率", "late", "attended"),
    "early_rate": ratio("早退班次率", "early", "attended"),
    "reception_conversion": ratio("接待顾客成交率", "reception_orders", "receptions"),
    "receptions": number("接待顾客人次", "receptions", "人次"),
    "reception_orders": number("接待后成交人次", "reception_orders", "人次"),
    "complaints": number("投诉数量", "complaints"),
    "training": ratio("培训完成率", "training_done", "training_assigned"),
    "onboarding": ratio("新员工平均上手周期", "onboard_days", "onboard_count", scale=1, unit="天"),
    "attrition": ratio("离职人数/平均在岗人数", "departures", "avg_headcount"),
    "vacancies": number("期末待招聘人数", "vacancies", "人"),
    "hire_days": ratio("平均招聘周期", "hire_days", "hires", scale=1, unit="天"),
    "staff_feedback": number("员工反馈数量", "staff_feedback"),
    "overtime": ratio("加班工时占比", "overtime", "staff_hours"),
    "staff_satisfaction": ratio("员工满意度均分", "staff_score", "staff_responses", scale=1, unit="分/5分"),
    "csat": ratio("顾客满意度均分", "customer_score", "customer_responses", scale=1, unit="分/5分"),
    "timely_resolution": ratio("投诉按时处理率", "complaints_timely", "complaints_due"),
    "platform_score": ratio("平台评价均分", "review_score", "reviews", scale=1, unit="分/5分"),
    "reviews": number("评价数量", "reviews", "条"),
    "tags": number("含该关键词的评价数", "review_tags", "条"),
    "nps": ratio("净推荐值NPS", "promoters-detractors", "nps_responses", unit="分"),
    "wait": ratio("平均等待时长", "wait_seconds", "wait_samples", scale=1, unit="秒"),
    "closed_rate": ratio("投诉闭环率", "complaints_closed", "complaints"),
    "repeat_complaint": ratio("重复投诉率", "complaints_repeated", "complaints"),
    "mentions": number("社交提及数量", "mentions", "条"),
    "participants": number("活动参与人数", "participants", "人"),
    "campaign_conversion": ratio("活动参与顾客成交率", "campaign_buyers", "participants"),
    "marketing_roi": ratio("归因毛利减营销费用/营销费用", "campaign_sales-campaign_cogs-marketing_spend", "marketing_spend"),
    "marketing_spend": money("营销投入", "marketing_spend"),
    "participation": ratio("受邀会员参与率", "member_participants", "member_invited"),
    "member_repeat": ratio("活动会员后续复购率（已完成观察期）", "member_repeated", "member_followup_eligible"),
    "campaign_sales": money("活动归因销售净额", "campaign_sales"),
    "campaign_target": ratio("活动销售目标完成率", "campaign_sales", "campaign_target"),
    "cac": ratio("新客获取成本", "marketing_spend", "acquired_customers", scale=.01, unit="元/人"),
    "acquired_customers": number("期间获取新客数", "acquired_customers", "人"),
    "customer_value": ratio("历史365天平均顾客毛利贡献", "customer_margin_365", "customer_cohort", scale=.01, unit="元/人"),
    "campaign_ticket": ratio("活动订单客单价", "campaign_sales", "campaign_orders", scale=.01, unit="元/单"),
    "channel_repeat": ratio("来源渠道顾客复购率", "repeat_customers", "buyers"),
    "community_active": ratio("社群活跃率", "community_active", "community_members"),
    "community_conversion": ratio("社群活跃顾客成交率", "community_buyers", "community_active"),
    "hours_use": ratio("有成交营业时段占比", "active_hours", "open_hours"),
    "checkout": ratio("平均收银处理时长", "checkout_seconds", "orders", scale=1, unit="秒"),
    "area_sales": ratio("期间坪效", "sales", "floor_area", scale=.01, unit="元/平方米"),
    "energy_share": ratio("水电燃气费用/销售净额", "energy_cost", "sales"),
    "equipment_rate": ratio("设备故障次数/设备运行天数", "failures", "equipment_days", scale=1000, unit="次/千设备天"),
    "repair": ratio("平均维修响应时长", "repair_hours", "failures", scale=1, unit="小时"),
    "downtime": number("设备故障停业时长", "downtime_hours", "小时"),
    "audit_score": ratio("卫生检查均分", "audit_score", "audits", scale=1, unit="分/100分"),
    "rectification": ratio("到期整改完成率", "rectified", "rectification_due"),
    "incidents": number("安全事件数", "incidents"),
    "delivery_timely": ratio("出餐交付准时率", "delivery_ontime", "delivery_orders"),
    "bad_review": ratio("差评/已评价配送订单", "bad_delivery_reviews", "delivery_reviews"),
    "sop": ratio("SOP抽查达标率", "sop_pass", "sop_checks"),
    "lost": ratio("高峰未完成接待离店人次占比", "peak_lost", "peak_entries"),
    "market_share": ratio("调研覆盖市场销售份额", "market_sales", "@all_market_sales"),
    "basket_price": ratio("可比商品篮子平均单价", "basket_price", "basket_items", scale=.01, unit="元"),
    "competitor_campaigns": number("竞品活动次数", "competitor_campaigns"),
    "district_entries": number("商圈人流人次", "district_entries", "人次"),
    "market_demand": number("调研需求选择数", "market_demand", "人次"),
    "platform_rank": number("期末平台排名", "platform_rank", "名"),
    "exposure": number("平台曝光量", "exposure", "次"),
    "brand_awareness": ratio("品牌知晓率", "brand_known", "brand_respondents"),
    "brand_positive": ratio("知晓者正面评价比例", "brand_positive", "brand_known"),
    "purchase": money("采购金额", "purchase"),
    "quality": ratio("到货验收合格率", "accepted_units", "delivered_units"),
    "purchase_price": ratio("采购平均单价", "purchase", "purchased_units", scale=.01, unit="元/件"),
    "benchmark_price": ratio("同数量市场基准单价", "benchmark_cost", "purchased_units", scale=.01, unit="元/件"),
    "premium": ratio("采购成本相对基准偏差", "purchase-benchmark_cost", "benchmark_cost"),
    "supplier_share": ratio("供应商采购额占比", "purchase", "@all_purchase"),
    "backups": number("合格备选供应商数", "backups", "家"),
    "fulfillment": ratio("到期采购单完整履约率", "fulfilled_po", "due_po"),
    "shortage": ratio("到期采购数量缺货率", "short_units", "due_units"),
    "cold_chain": ratio("冷链温控合规批次率", "cold_pass", "cold_shipments"),
    "purchase_cycle": ratio("采购平均到货周期", "lead_days", "deliveries", scale=1, unit="天"),
    "expired_units": number("过期损耗件数", "expired_units", "件"),
    "expired_cost": money("过期损耗金额", "expired_cost"),
    "supplier_score": ratio("供应商评分均分", "supplier_score", "supplier_assessments", scale=1, unit="分/100分"),
}

# Date snapshots must never be summed across days. The daily average is weighted
# equally by covered calendar days per store, including explicitly stored zeroes.
SNAPSHOT = frozenset("stock stock_value safety aged_stock aged_value expiring_stock alert_skus skus member_total vacancies investment floor_area platform_rank backups customer_margin_365 customer_cohort".split())
AVERAGE = frozenset("avg_stock_value avg_receivable avg_payable avg_headcount".split())
FIRST = frozenset({"opening_stock"})
DISTINCT = frozenset("buyers new_customers return_customers participants campaign_buyers member_invited member_participants".split())


@dataclass(frozen=True)
class Report:
    metrics: tuple[str, ...]
    group: tuple[str, ...] = ()
    note: str = ""
    filter_kind: str = ""
    sort_by: str | None = None
    rankings: tuple[str, ...] = ()
    top_limit: int | None = None


def report(metrics: str, group: str = "", note: str = "", filter_kind: str = "", *,
           sort_by: str | None = None, rankings: tuple[str, ...] = (), top_limit: int | None = None) -> Report:
    return Report(tuple(metrics.split()), tuple(group.split()), note, filter_kind, sort_by, rankings, top_limit)


REPORTS = {
    1: report("entries"), 2: report("entries", "hour"),
    3: report("new_customers return_customers old_repeat_rate", note="新老客按有覆盖的购买历史划分，不等于首次进店；老客复购口径为期前已购买且期内购买的顾客中，期内至少两笔订单的占比，不是全体顾客复购率或流失率。"),
    4: report("ticket sales orders"), 5: report("sales units", "age_band category", "消费偏好仅描述购买结构，不推断个人心理。"),
    6: report("joins active_members member_total member_activity"),
    7: report("", filter_kind="inactive_members"),
    8: report("dwell entries"), 9: report("entries", "channel"),
    10: report("daily_entries entries", "weather holiday", "按各天气/节假日分组的覆盖天数比较日均值；描述关联，不证明天气导致变化。"),
    11: report("sales sales_share units", "product", top_limit=10),
    12: report("units sales stock", "product", filter_kind="decline20"),
    13: report("sales sales_share units", "category"),
    14: report("sales sales_target target_rate", "product", filter_kind="new_products"),
    15: report("stock stock_days units", "product", "可售天数为当前库存/期内日均销量，不是采购预测。", "high_stock"),
    16: report("sales units", "price_band", "已售价格带不能证明未覆盖价格带存在需求。"),
    17: report("pairs pair_support", "pair", "仅用同篮订单数与占全部订单的比例描述关联；不称更受欢迎、更喜欢、更有吸引力或购买意愿更高。组合陈列只可作为待验证建议，不保证提升成交。多商品订单可含多种组合，各组合占比不要求合计100%。"), 18: report("sales", "store"),
    19: report("sales units target_rate stock_days", "category", "使用历史销量与目标作备货参考，不承诺未来销量。"),
    20: report("refunds refund_rate exchanges exchange_rate", "product", "退货率与换货率分别按rankings排序，不能把明细行顺序当作两个榜单。退换货率不能独立证明质量问题，原因需结合售后记录。",
               sort_by="refund_rate", rankings=("refund_rate", "exchange_rate")),
    21: report("stock stock_value turnover_days", note="总量与周转天数不能独立判定库存健康或压力大小；需结合SKU安全线、库龄和临期明细，不使用未配置的健康阈值。"), 22: report("stock safety", "product", filter_kind="low_stock"),
    23: report("aged aged_value", "product", filter_kind="positive_first"),
    24: report("loss_rate loss_value", "category"), 25: report("stock_value stock_share sales_share", "category"),
    26: report("on_time", "supplier"), 27: report("stock_value sales stock_sales"),
    28: report("expiring", "product", "临期数量按期末快照；expiry_details含批次到期日及距查询期末剩余天数，非实时倒计时。缺少完整批次日期时不得推断处理时限；具体下架时刻依门店制度。", "positive_first"),
    29: report("alert_coverage"), 30: report("count_difference", "category", "差异提示复核，不直接归因为管理漏洞或盗损。"),
    31: report("sales sales_target target_rate"), 32: report("cost cost_share", "cost_type"),
    33: report("gross_profit gross_margin net_profit net_margin"),
    34: report("daily_sales daily_fixed breakeven", note="盈亏平衡以期间贡献率（净销售减商品成本及非固定费用，再除净销售）与固定费用估算；贡献率非正时不可用，非审计利润。"),
    35: report("sales staff_hours sales_per_hour", "hour"),
    36: report("paid payment_share payment_difference", "payment"),
    37: report("coupon_rate coupon_sales", "campaign", "核销销售额是归因金额；无对照组时不能认定为增量营收。"),
    38: report("receivable_days payable_days"), 39: report("expense", "cost_type"),
    40: report("roi net_profit investment", note="ROI为所选期间经营净收益/投入资本，value已换算为百分数，单位%时不得再乘100；不是年化回报或未来预测。"),
    41: report("attendance late_rate early_rate"), 42: report("sales staff_hours sales_per_hour", "staff"),
    43: report("reception_conversion receptions reception_orders", "staff"), 44: report("complaints", "staff", "只用于复核客诉与培训需求，不直接认定员工责任。"),
    45: report("entries staff_hours", "hour", "展示各时段客流与工时，需结合服务标准判断人力是否足够。"),
    46: report("training onboarding"), 47: report("attrition vacancies hire_days", "role"),
    48: report("staff_feedback", "reason"), 49: report("overtime staff_hours", "role"),
    50: report("staff_satisfaction", "reason", "因素为问卷项目评分，不是因果结论。"),
    51: report("csat"), 52: report("complaints timely_resolution", "reason"),
    53: report("platform_score reviews", "platform"), 54: report("tags", "sentiment keyword"),
    55: report("nps", "reason", "NPS=(9–10分推荐者−0–6分贬损者)/全部有效受访者×100。"),
    56: report("wait csat", "channel", "等待与满意度仅为同期描述，不能直接判定因果。"),
    57: report("csat", "channel"), 58: report("closed_rate repeat_complaint"),
    59: report("", note="仅按最近一次有记录的到店/购买间隔提示关注，不是已验证流失预测。", filter_kind="inactive_members"),
    60: report("mentions", "platform sentiment"),
    61: report("participants campaign_conversion", "campaign"),
    62: report("marketing_roi marketing_spend", "acquisition_channel", "按活动归因毛利计算ROI，不能当作因果增量ROI。"),
    63: report("coupon_rate coupon_sales", "campaign", "没有随机对照或其他可信反事实，不计算额外/增量销售。"),
    64: report("participation member_repeat", "campaign", "仅统计完成观察期的参与会员；提升效果需可比对照，当前只报告复购表现。"),
    65: report("campaign_sales campaign_target", "campaign"),
    66: report("cac customer_value marketing_spend acquired_customers", "acquisition_channel", "CAC=期间营销投入/期间获取新客数；全渠道CAC使用全渠道总投入除总新客，绝不是各渠道CAC相加或简单平均。零新客渠道的CAC不可计算，不代表成本或新客记录缺失。历史365天毛利贡献不等于完整生命周期LTV；无留存模型时不声称LTV/CAC健康。"),
    67: report("campaign_ticket ticket", "campaign", "活动与普通订单的差异是描述性对比，不能证明活动导致提升。"),
    68: report("channel_repeat ticket", "acquisition_channel"),
    69: report("daily_entries entries", "campaign", "按覆盖天数比较日均客流，活动/普通日期差异不等于活动因果效果。"),
    70: report("community_active community_conversion", "acquisition_channel"),
    71: report("hours_use", note="利用率定义为有成交营业小时/营业小时；无成交不直接等于浪费。"),
    72: report("checkout wait", note="平均处理和等待时长不代表高峰分布；无排队长度及服务标准时不能断言没有拥堵。"), 73: report("sales area_sales", "store", sort_by="area_sales"),
    74: report("energy_share"), 75: report("equipment_rate repair downtime", "equipment"),
    76: report("audit_score rectification"), 77: report("incidents", "reason"),
    78: report("delivery_timely bad_review", "platform"), 79: report("sop", "procedure"),
    80: report("lost wait", "hour", "流失为已有高峰接待记录中的未完成接待离店口径，不能推断每位顾客离店原因。", "peak_only"),
    81: report("", filter_kind="competitors"), 82: report("market_share", "competitor", "份额只涵盖所注明的调研样本市场，不是全市场真实普查；份额同期变化不能证明新店分流或导致本店变化，原因尚需验证。"),
    83: report("basket_price", "competitor", "同篮子可比价格；无法单凭价差证明影响本店客流。"),
    84: report("competitor_campaigns", "competitor", "只列本店之外的竞品活动；缺少可信竞品交易数据，不报告活动带来的新增收入。", "competitors_only"),
    85: report("district_entries", "district", "仅描述变化，外部因素影响需要额外证据。"),
    86: report("market_demand", "segment", "以数据覆盖日期内的调研为依据，不声称实时全行业最新趋势。"),
    87: report("platform_rank exposure", "platform"), 88: report("", filter_kind="new_competitors"),
    89: report("brand_awareness brand_positive"),
    90: report("market_demand", "segment", "市场机会是基于调研的建议，需小规模实验验证，不能承诺收益。"),
    91: report("purchase"), 92: report("on_time quality", "supplier"),
    93: report("purchase_price benchmark_price premium", "material"),
    94: report("purchase_price benchmark_price", "material"),
    95: report("purchase supplier_share backups", "supplier", "按采购金额集中度识别依赖；备选需完成资质和供应能力验证。"),
    96: report("fulfillment shortage", "supplier"), 97: report("cold_chain", "supplier", "仅含有冷链运输批次的供应商。", "cold_only"),
    98: report("purchase_cycle", "supplier"), 99: report("expiring expired_units expired_cost", "product"),
    100: report("supplier_score on_time quality", "supplier", "评分来自供应商评估记录，合作调整仍需综合核查。"),
}

QUESTIONS = json.loads(Path(__file__).with_name("questions.json").read_text(encoding="utf-8"))
QUESTIONS_BY_ID = {q["id"]: q for q in QUESTIONS}
assert len(QUESTIONS_BY_ID) == len(QUESTIONS)
assert all(q.get("domain") in {"customers","sales","inventory","finance","staff","feedback",
                            "marketing","operations","market","procurement"} for q in QUESTIONS)
assert set(REPORTS) == {q["id"] for q in QUESTIONS} == set(range(1, 101))
