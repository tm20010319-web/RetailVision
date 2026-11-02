"""Create a new reproducible operating-data fixture; never overwrite a database.

All names are fictional. No camera, production DB or model service is contacted.
The written snapshot records synthetic provenance; the conversational UI need
not repeat that provenance on each answer during the agreed acceptance period.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import random
import sqlite3

from .schema import SCHEMA, SCHEMA_VERSION, json_value
from .definitions import SNAPSHOT, AVERAGE, FIRST

PRODUCTS = (
    ("矿泉水", "饮料", 300, 140), ("果汁", "饮料", 1200, 650),
    ("牛奶", "乳品", 800, 430), ("酸奶", "乳品", 1000, 550),
    ("三明治", "鲜食", 1800, 900), ("便当", "鲜食", 2800, 1450),
    ("面包", "烘焙", 1200, 550), ("曲奇", "烘焙", 2200, 1000),
    ("坚果", "零食", 3500, 1750), ("薯片", "零食", 900, 400),
    ("纸巾", "日用", 1500, 700), ("保温杯", "日用", 8900, 4200),
)
CHANNELS = ("到店", "外卖", "小程序")
ACQUISITION = ("自然到店", "微信", "抖音", "小红书")


def seed_database(path: Path, *, end: date, seed: int = 27100, days: int = 427) -> dict:
    if days < 32 or days > 1100:
        raise ValueError("seed_days_out_of_range")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL reserves only this requested output, including across processes.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    start = end - timedelta(days=days - 1)
    rng = random.Random(seed)
    revision = hashlib.sha256(Path(__file__).read_bytes() + Path(__file__).with_name("schema.py").read_bytes()).hexdigest()
    dataset_id = hashlib.sha256(f"business-v1:{start}:{end}:{seed}:{revision}".encode()).hexdigest()[:24]
    connection = sqlite3.connect(path)
    supported: set[str] = set()
    counts = 0
    try:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO dataset VALUES (?,?,?,?,?,?,?,?,?)", (
            dataset_id, SCHEMA_VERSION, "synthetic_acceptance", datetime.now(timezone.utc).isoformat(),
            str(start), str(end), "Asia/Shanghai", seed,
            "综合零售门店含食品配送；内部验收预置数据，未连接真实POS或摄像头。generator_sha256="+revision))
        for store_index, store_name in enumerate(("春禾中心店", "春禾东街店", "春禾社区店"), 1):
            store = f"business-store-{store_index:03d}"
            connection.execute("INSERT INTO stores VALUES (?,?,?,?,?)", (
                store, store_name, ("中心区", "东城区", "社区区")[store_index-1],
                120 + 20*store_index, 60000000 + 5000000*store_index))
            def entity(kind: str, key: str, **attrs: object) -> None:
                connection.execute("INSERT INTO entities VALUES (?,?,?,?)", (store, kind, key, json_value(attrs)))

            for pi, (name, category, price, cost) in enumerate(PRODUCTS):
                entity("product", name, name=name, category=category, unit_price_cents=price,
                       unit_cost_cents=cost, introduced_on=str(end - timedelta(days=15 if pi == 11 else 600)))
            members = []
            for mi in range(120):
                joined = start + timedelta(days=0 if mi < 60 else rng.randrange(1, days))
                member = {"member": f"顾客{store_index}-{mi+1:03d}",
                          "joined": joined, "age_band": ("18–29岁", "30–44岁", "45–59岁", "60岁及以上")[mi % 4],
                          "acquisition_channel": ACQUISITION[mi % 4]}
                members.append(member)
                entity("member", member["member"], name=member["member"], joined_on=str(joined))
            for ei in range(6):
                entity("staff", f"员工{store_index}-{ei+1:02d}", name=f"员工{store_index}-{ei+1:02d}",
                       role="店员" if ei < 4 else "主管")
            for ci in range(4):
                entity("competitor", f"邻里商店{ci+1}", name=f"邻里商店{ci+1}",
                       distance_km=round(.6 + ci*.8, 1),
                       opened_on=str(end-timedelta(days=12 if ci == 3 else 500+ci*30)), category="综合零售")
            historic_buyers=set()
            margin_history=deque()
            for di in range(days):
                day = start + timedelta(days=di)
                rows: dict[tuple[str, str], float] = defaultdict(float)
                def add(metric: str, value: float, **dimensions: object) -> None:
                    supported.add(metric)
                    rows[(metric, json_value(dimensions))] += value
                registered = [m for m in members if m["joined"] <= day]
                eligible = [m for m in registered if not (day>end-timedelta(days=65) and m in members[:8])]
                weather = "雨天" if di % 5 == 0 else "晴天"
                holiday = "节假日/周末" if day.weekday() >= 5 or (day.month == 10 and day.day <= 7) else "工作日"
                campaign = "周末会员日" if day.weekday() >= 5 else ("月中优惠" if 12 <= day.day <= 16 else "日常经营")
                total_sales = total_cogs = total_orders = 0
                units_by_product: dict[str, int] = defaultdict(int)
                buyers = set()
                acquired=defaultdict(set)
                channel_buyers=defaultdict(set)
                coupon_count=defaultdict(int)
                coupon_value=defaultdict(int)
                hour_sales: dict[str, int] = defaultdict(int)
                for oi in range(16 + store_index*2 + (8 if day.weekday() >= 5 else 0)):
                    member = rng.choice(eligible)
                    buyers.add(member["member"])
                    acquisition=member["acquisition_channel"]
                    channel_buyers[acquisition].add(member["member"])
                    if member["member"] not in historic_buyers:
                        acquired[acquisition].add(member["member"])
                    historic_buyers.add(member["member"])
                    channel = rng.choices(CHANNELS, (7,2,1))[0]
                    hour = rng.choice(("上午", "下午", "晚上"))
                    staff = f"员工{store_index}-{rng.randrange(1,7):02d}"
                    payment = rng.choices(("现金", "微信", "支付宝"), (1,6,3))[0]
                    common = {"member": member["member"], "age_band": member["age_band"],
                              "acquisition_channel": member["acquisition_channel"], "channel": channel,
                              "hour": hour, "staff": staff, "campaign": campaign, "payment": payment}
                    order_sales = order_cogs = 0
                    selected = rng.sample(range(len(PRODUCTS) if day>=end-timedelta(days=15) else len(PRODUCTS)-1), 2)
                    for pi in selected:
                        name, category, price, cost = PRODUCTS[pi]
                        # A recent introduction has no pre-introduction sales.
                        if pi == 11 and day < end-timedelta(days=15):
                            pi = 8
                            name, category, price, cost = PRODUCTS[pi]
                        quantity = rng.randrange(1,4)
                        refunded = int(rng.random() < .025)
                        exchanged = int(not refunded and rng.random() < .018)
                        discount = 90 if campaign != "日常经营" else 100
                        line_sales = (quantity-refunded)*price*discount//100
                        line_cost = (quantity-refunded)*cost
                        dimensions = {**common, "product": name, "category": category,
                                      "price_band": "20元以下" if price < 2000 else "20–49元" if price < 5000 else "50元以上"}
                        for metric, value in (("sales",line_sales),("cogs",line_cost),("units",quantity),
                                              ("refund_units",refunded),("exchange_units",exchanged)):
                            add(metric, value, **dimensions)
                        units_by_product[name] += quantity
                        order_sales += line_sales
                        order_cogs += line_cost
                    for metric, value in (("orders",1),("paid",order_sales),("settled",order_sales),
                                          ("buyers",1),("checkout_seconds",rng.randrange(40,151))):
                        add(metric, value, **common)
                    pair = " + ".join(sorted(PRODUCTS[p][0] for p in selected))
                    add("pairs",1,pair=pair)
                    for metric,value in (("campaign_sales",order_sales),("campaign_cogs",order_cogs),("campaign_orders",1)):
                        add(metric,value,campaign=campaign,acquisition_channel=acquisition)
                    if oi%5==0:
                        coupon_count[acquisition]+=1
                        coupon_value[acquisition]+=order_sales
                    margin_history.append((day,acquisition,member["member"],order_sales-order_cogs))
                    total_sales += order_sales
                    total_cogs += order_cogs
                    total_orders += 1
                    hour_sales[hour] += order_sales
                for member in eligible:
                    if member["joined"] == day:
                        add("joins",1)
                    # Visit observations are explicitly prefilled, not camera inference.
                    if member["member"] in buyers or rng.random() < .02:
                        add("member_visits",1,member=member["member"])
                add("member_total",len(registered))
                add("investment",60000000+5000000*store_index)
                add("floor_area",120+20*store_index)
                add("credit_sales",round(total_sales*.15))
                add("avg_receivable",round(total_sales*2.1))
                add("avg_payable",round(total_cogs*3.2))
                add("open_hours",12)
                add("active_hours",10 if day.weekday() < 5 else 12)
                for hour, portion in (("上午",.25),("下午",.4),("晚上",.35)):
                    entries = round((110+store_index*20)*(1.25 if day.weekday()>=5 else 1)*portion)
                    for source,source_entries in (("线上引流到店",round(entries*.2)),("线下自然到店",entries-round(entries*.2))):
                        dims = {"hour":hour,"weather":weather,"holiday":holiday,"channel":source,"campaign":campaign}
                        add("entries",source_entries,**dims)
                        add("dwell_seconds",source_entries*(320+di%100),**dims)
                    add("peak_entries",entries if hour=="下午" else 0,hour=hour)
                    add("peak_lost",round(entries*.06) if hour=="下午" else 0,hour=hour)
                    add("wait_seconds",entries*(50+di%70),hour=hour,channel="到店")
                    add("wait_samples",entries,hour=hour,channel="到店")
                for channel,seconds in (("外卖",240),("小程序",130)):
                    add("wait_seconds",5*seconds,channel=channel)
                    add("wait_samples",5,channel=channel)
                for ei in range(6):
                    staff = f"员工{store_index}-{ei+1:02d}"
                    role = "店员" if ei < 4 else "主管"
                    present = int((di+ei)%29 != 0)
                    dims = {"staff":staff,"role":role}
                    for metric,value in (("scheduled",1),("attended",present),("late",int(present and (di+ei)%23==0)),
                                         ("early",int(present and (di+ei)%41==0)),("overtime",present*(1 if di%7==0 else 0))):
                        add(metric,value,**dims)
                    for hour in ("上午","下午","晚上"):
                        add("staff_hours",present*(8+int(di%7==0))/3,**dims,hour=hour)
                    add("receptions",20+ei,staff=staff)
                    # Exact paid-order staff attribution, with an explicit reception population.
                    paid_count = sum(v for (m,d),v in rows.items() if m=="orders" and f'"staff":"{staff}"' in d)
                    add("reception_orders",paid_count,staff=staff)
                for role,headcount in (("店员",4),("主管",2)):
                    add("avg_headcount",headcount,role=role)
                    add("departures",int(day.day==25 and role=="店员" and day.month%3==0),role=role)
                    add("vacancies",int(role=="店员"),role=role)
                    add("hires",int(day.day==10),role=role)
                    add("hire_days",18*int(day.day==10),role=role)
                add("training_assigned",6 if day.day==1 else 0)
                add("training_done",5 if day.day==1 else 0)
                add("onboard_count",int(day.day==10))
                add("onboard_days",12*int(day.day==10))
                for reason,score in (("排班安排",3.7),("薪酬福利",3.9),("团队协作",4.3)):
                    add("staff_feedback",int(di%7==0),reason=reason)
                    add("staff_score",score*3,reason=reason)
                    add("staff_responses",3,reason=reason)
                for name,category,price,cost in PRODUCTS:
                    qty = 5 if name=="牛奶" else (950 if name=="曲奇" else 45+di%30)
                    aged = 400 if name=="曲奇" else 0
                    opening_qty=5 if name=="牛奶" else (950 if name=="曲奇" else 45+max(di-1,0)%30)
                    receipts=max(0,units_by_product[name]+int(di%20==0)+qty-opening_qty)
                    expiry = min(qty,6 if category in {"乳品","鲜食","烘焙"} else 0)
                    dims={"product":name,"category":category}
                    for metric,value in (("stock",qty),("stock_value",qty*cost),("avg_stock_value",qty*cost),
                                         ("safety",15),("aged_stock",aged),("aged_value",aged*cost),
                                         ("alert_skus",int(name!="保温杯")),("skus",1),
                                         ("opening_stock",opening_qty),("stock_receipts",receipts),("loss_units",int(di%20==0)),
                                         ("loss_value",cost*int(di%20==0)),("count_book",qty),("count_difference",int(di%25==0)),
                                         ("expired_units",int(category=="鲜食" and di%11==0)),
                                         ("expired_cost",cost*int(category=="鲜食" and di%11==0)),
                                         ("sales_target",price*4 if name!="保温杯" or day>=end-timedelta(days=15) else 0)):
                        add(metric,value,**dims)
                    # Persist expiry provenance, keeping exactly the same product
                    # totals. These dated batches belong to this snapshot only;
                    # they do not retrofit dates into an older published database.
                    if expiry:
                        for batch_index,quantity in enumerate((expiry//2,expiry-expiry//2),1):
                            add("expiring_stock",quantity,**dims,
                                batch_id=f"{store}:{name}:{day}:{batch_index}",
                                expires_on=str(day+timedelta(days=2 if batch_index==1 else 6)))
                    else:
                        add("expiring_stock",0,**dims)
                for cost_type,value in (("租金",18000),("人工",40000),("水电燃气",3500),("营销",2000),("税费及其他",1500)):
                    add("expenses",value,cost_type=cost_type)
                    add("cost_total",value,cost_type=cost_type)
                    add("fixed_expenses",value if cost_type in {"租金","人工"} else 0,cost_type=cost_type)
                add("energy_cost",3500)
                add("cost_total",total_cogs,cost_type="已售商品成本")
                for supplier_i,supplier in enumerate(("禾丰食品", "远山日配", "安宜百货")):
                    material = ("食品原料","乳品鲜食","日用商品")[supplier_i]
                    qty=20+di%12
                    unit=(500,700,1000)[supplier_i]
                    accepted=qty-int(di%13==0)
                    dims={"supplier":supplier,"material":material}
                    for metric,value in (("purchase",qty*unit),("credit_purchase",qty*unit),
                                         ("purchased_units",qty),("benchmark_cost",qty*(unit-20)),
                                         ("deliveries",1),("ontime_deliveries",int(di%17!=0)),
                                         ("delivered_units",qty),("accepted_units",accepted),
                                         ("fulfilled_po",int(di%19!=0)),("due_po",1),("due_units",qty),
                                         ("short_units",int(di%19==0)),("lead_days",2+di%3),
                                         ("cold_shipments",int(supplier_i<2)),("cold_pass",int(supplier_i<2 and di%43!=0)),
                                         ("supplier_score",88+supplier_i*3),("supplier_assessments",1),("backups",1+supplier_i)):
                        add(metric,value,**dims)
                for channel,score in (("到店",4.4),("外卖",4.1),("小程序",4.3)):
                    add("customer_score",score*5,channel=channel)
                    add("customer_responses",5,channel=channel)
                for reason in ("服务响应","商品质量","配送时效"):
                    complaint=int((di+len(reason))%6==0)
                    staff=f"员工{store_index}-{di%6+1:02d}"
                    for metric,value in (("complaints",complaint),("complaints_due",complaint),
                                         ("complaints_timely",complaint*int(di%5!=0)),
                                         ("complaints_closed",complaint*int(di%13!=0)),
                                         ("complaints_repeated",complaint*int(di%11==0))):
                        add(metric,value,reason=reason,staff=staff)
                    add("promoters",6,reason=reason)
                    add("detractors",1,reason=reason)
                    add("nps_responses",10,reason=reason)
                    add("incidents",int(di%80==0 and reason=="服务响应"),reason="纠纷" if reason=="服务响应" else "设施事故")
                for platform in ("美团","大众点评"):
                    add("reviews",8,platform=platform)
                    add("review_score",34+di%3,platform=platform)
                    add("platform_rank",7+store_index,platform=platform)
                    add("exposure",400+di%100,platform=platform)
                    add("mentions",12,platform=platform,sentiment="正面")
                    add("mentions",3,platform=platform,sentiment="负面")
                    add("delivery_orders",8,platform=platform)
                    add("delivery_ontime",7,platform=platform)
                    add("delivery_reviews",5,platform=platform)
                    add("bad_delivery_reviews",int(di%4==0),platform=platform)
                for sentiment,keyword,count in (("正面","服务耐心",6),("正面","品类齐全",5),("负面","等待较久",2)):
                    add("review_tags",count,sentiment=sentiment,keyword=keyword)
                while margin_history and margin_history[0][0]<day-timedelta(days=364):
                    margin_history.popleft()
                for acquisition in ACQUISITION:
                    dims={"campaign":campaign,"acquisition_channel":acquisition}
                    buyer_count=len(channel_buyers[acquisition])
                    add("marketing_spend",500,**dims)
                    candidates=[m["member"] for m in eligible if m["acquisition_channel"]==acquisition and m["member"] not in channel_buyers[acquisition]]
                    extra=rng.sample(candidates,min(8,len(candidates)))
                    participants=channel_buyers[acquisition]|set(extra)
                    invited=participants|set(candidates[:max(0,20-len(participants))])
                    for metric,population in (("participants",participants),("campaign_buyers",channel_buyers[acquisition]),
                                              ("member_invited",invited),("member_participants",participants)):
                        for member_name in sorted(population):
                            add(metric,1,**dims,member=member_name)
                    for metric,value in (("coupon_issued",20),("coupon_redeemed",coupon_count[acquisition]),
                                         ("member_followup_eligible",8),("member_repeated",3),("campaign_target",20000),
                                         ("acquired_customers",len(acquired[acquisition])),
                                         ("community_members",100),("community_active",30),("community_buyers",5)):
                        add(metric,value,**dims)
                    # Cohort quantities above are imported daily aggregates in this fixture.
                    # Attribution sales retain exact order-level sums instead of invented uplift.
                    add("coupon_sales",coupon_value[acquisition],**dims)
                    history=[record for record in margin_history if record[1]==acquisition]
                    add("customer_margin_365",sum(record[3] for record in history),acquisition_channel=acquisition)
                    add("customer_cohort",len({record[2] for record in history}),acquisition_channel=acquisition)
                for equipment in ("冷柜","收银设备"):
                    failure=int(di%53==0)
                    add("failures",failure,equipment=equipment)
                    add("equipment_days",3,equipment=equipment)
                    add("repair_hours",failure*2,equipment=equipment)
                    add("downtime_hours",failure*.5,equipment=equipment)
                add("audits",1); add("audit_score",92+di%5)
                add("rectification_due",2); add("rectified",2 if di%7 else 1)
                for procedure in ("开店检查","食品温控","闭店盘点"):
                    add("sop_checks",10,procedure=procedure)
                    add("sop_pass",9 if di%5==0 else 10,procedure=procedure)
                for ci,competitor in enumerate((store_name,"邻里商店1","邻里商店2","邻里商店3","邻里商店4")):
                    active=ci!=4 or day>=end-timedelta(days=12)
                    add("market_sales",(total_sales if ci==0 else 60000+ci*5000)*int(active),competitor=competitor)
                    add("basket_price",(16500+ci*300)*int(active),competitor=competitor)
                    add("basket_items",12*int(active),competitor=competitor)
                    add("competitor_campaigns",int(active and day.day%10==0),competitor=competitor)
                add("district_entries",2000+di%300,district="门店周边商圈")
                for si,segment in enumerate(("健康食品","即时配送","家庭日用")):
                    add("market_demand",20+si*5+di//45,segment=segment)
                add("brand_respondents",20); add("brand_known",14); add("brand_positive",11)
                batch=[(dataset_id,store,str(day),metric,dims,value) for (metric,dims),value in rows.items()]
                connection.executemany("INSERT INTO measurements(dataset_id,store_id,business_date,metric,dimensions,value) VALUES (?,?,?,?,?,?)",batch)
                counts+=len(batch)
        connection.executemany("INSERT INTO metric_coverage VALUES (?,?,?,?)", (
            (metric,str(start),str(end),int(metric not in SNAPSHOT and metric not in AVERAGE and metric not in FIRST)) for metric in sorted(supported)))
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("seed_integrity_failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("seed_foreign_key_failed")
    except Exception:
        connection.close()
        # Only remove the new output reserved by this invocation, never old data.
        path.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    return {"path":str(path),"dataset_id":dataset_id,"source_kind":"synthetic_acceptance",
            "start_date":str(start),"end_date":str(end),"measurement_rows":counts,
            "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--end-date",type=date.fromisoformat,required=True)
    parser.add_argument("--days",type=int,default=427)
    parser.add_argument("--seed",type=int,default=27100)
    args=parser.parse_args()
    print(json_value(seed_database(args.output,end=args.end_date,days=args.days,seed=args.seed)))


if __name__=="__main__":
    main()
