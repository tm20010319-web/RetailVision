"""Read-only, scoped statistical queries over a versioned operating snapshot."""
from __future__ import annotations

import calendar
from contextlib import closing
from collections import defaultdict
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

from .definitions import AVERAGE, DISTINCT, FIRST, I, QUESTIONS_BY_ID, REPORTS, SNAPSHOT

RESULT_BUDGET_CHARS = 8500
from .schema import open_readonly


def shift_month(value: date, count: int) -> date:
    month=value.year*12+value.month-1+count
    year,zero_month=divmod(month,12)
    return date(year,zero_month+1,min(value.day,calendar.monthrange(year,zero_month+1)[1]))


def query_window(period: str, anchor: date, start: str | None, end: str | None) -> tuple[date,date,date,date]:
    if start is not None or end is not None:
        if not start or not end or period != "custom":
            raise ValueError("business_custom_window_requires_both_dates")
        first,last=date.fromisoformat(start),date.fromisoformat(end)
        previous_last=first-timedelta(days=1)
        previous_first=previous_last-(last-first)
    elif period=="today":
        first=last=anchor
        previous_first=previous_last=anchor-timedelta(days=1)
    elif period=="this_week":
        first,last=anchor-timedelta(days=anchor.weekday()),anchor
        previous_first,previous_last=first-timedelta(days=7),last-timedelta(days=7)
    elif period in {"this_month","last_month"}:
        first=anchor.replace(day=1)
        last=anchor
        if period=="last_month":
            last=first-timedelta(days=1)
            first=last.replace(day=1)
        previous_first=shift_month(first,-1)
        previous_last=shift_month(last,-1)
        if last.day==calendar.monthrange(last.year,last.month)[1]:
            previous_last=first-timedelta(days=1)
    elif period=="last_30_days":
        first,last=anchor-timedelta(days=29),anchor
        previous_first,previous_last=first-timedelta(days=30),last-timedelta(days=30)
    elif period=="this_year":
        first,last=anchor.replace(month=1,day=1),anchor
        previous_first,previous_last=shift_month(first,-12),shift_month(last,-12)
    else:
        raise ValueError("business_period_invalid")
    if last < first or (last-first).days >= 366:
        raise ValueError("business_window_invalid")
    return first,last,previous_first,previous_last


class BusinessQueryService:
    def __init__(self, path: str | Path, *, allowed_stores: tuple[str,...], default_store: str,
                 today: date | None = None, high_stock_days: int | None = None,
                 new_product_days: int | None = None) -> None:
        if not allowed_stores or default_store not in allowed_stores:
            raise ValueError("business_trusted_store_required")
        self.path=Path(path)
        self.allowed_stores=tuple(dict.fromkeys(allowed_stores))
        self.default_store=default_store
        self.today=today
        for threshold in (high_stock_days, new_product_days):
            if threshold is not None and (type(threshold) is not int or threshold <= 0):
                raise ValueError("business_rule_invalid")
        self.high_stock_days=high_stock_days
        self.new_product_days=new_product_days
        self._lock=threading.RLock()
        with closing(open_readonly(path)) as connection:
            available=dict(connection.execute("SELECT id,name FROM stores"))
            if not set(allowed_stores)<=set(available):
                raise ValueError("business_store_not_in_dataset")
            self.store_labels={store:available[store] for store in self.allowed_stores}

    def query(self, question_id: int, *, period: str="this_month", store_id: str | None=None,
              start_date: str | None=None, end_date: str | None=None, limit: int=20) -> dict:
        with self._lock:
            return self._query(question_id,period=period,store_id=store_id,start_date=start_date,end_date=end_date,limit=limit)

    def _query(self, question_id: int, *, period: str, store_id: str | None,
               start_date: str | None, end_date: str | None, limit: int) -> dict:
        if type(question_id) is not int or question_id not in REPORTS:
            raise ValueError("business_question_invalid")
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError("business_limit_invalid")
        if store_id is not None and store_id not in self.allowed_stores:
            raise ValueError("business_store_out_of_scope")
        spec=REPORTS[question_id]
        stores=self.allowed_stores if "store" in spec.group and store_id is None else (store_id or self.default_store,)
        with closing(open_readonly(self.path)) as db:
            dataset=dict(db.execute("SELECT * FROM dataset").fetchone())
            anchor=self.today or datetime.now(ZoneInfo(dataset["timezone"])).date()
            first,last,pfirst,plast=query_window(period,anchor,start_date,end_date)
            years=(shift_month(first,-12),shift_month(last,-12))
            self.db=db
            self._cache={}
            self.stores=stores
            self.coverage={row["metric"]:dict(row) for row in db.execute("SELECT * FROM metric_coverage")}
            self.store_names={row["id"]:row["name"] for row in db.execute("SELECT id,name FROM stores")}
            base={"question_id":question_id,"question":QUESTIONS_BY_ID[question_id]["question"],
                  "stores":[self.store_names[s] for s in stores],"period":{"start":str(first),"end":str(last),"inclusive_end":True},
                  "timezone":dataset["timezone"],"data_as_of":dataset["end_date"],
                  "dataset_id":dataset["id"],"interpretation":spec.note}
            if spec.filter_kind in {"high_stock", "new_products"}:
                threshold=self.high_stock_days if spec.filter_kind=="high_stock" else self.new_product_days
                base["classification"]={"rule":spec.filter_kind,"threshold_days":threshold,
                    "status":"configured" if threshold is not None else "not_configured"}
                base["interpretation"] += (" 高库存筛选按配置的可售天数阈值；有库存且明确零销量单独列为停销关注项，不能计算有限可售天数。"
                    if spec.filter_kind=="high_stock" and threshold is not None else
                    " 新品筛选按配置的上市天数阈值。" if threshold is not None else
                    " 分类阈值未配置；以下为商品事实明细，不是高库存或新品名单，不得自行分类。")
            if str(first)<dataset["start_date"] or str(last)>dataset["end_date"]:
                return {**base,"status":"unavailable","reason":"requested_period_not_covered",
                        "available_period":{"start":dataset["start_date"],"end":dataset["end_date"]},"rows":[]}
            if spec.filter_kind in {"inactive_members","competitors","new_competitors"}:
                rows=self.entity_report(spec.filter_kind,first,last)
                return {**base,"status":"ok","total_rows":len(rows),"returned_rows":min(len(rows),limit),
                        "rows_limited":len(rows)>limit,"rows":rows[:limit]}
            current=self.compute(spec.metrics,spec.group,first,last)
            previous=self.compute(spec.metrics,spec.group,pfirst,plast)
            yearly=self.compute(spec.metrics,spec.group,*years)
            totals=self.compute(spec.metrics,(),first,last).get((),{})
            output=[]
            for group,values in current.items():
                if not self.keep_row(spec.filter_kind,group,values,previous.get(group,{}),last):
                    continue
                dimensions=dict(zip(spec.group,group))
                dimensions={k:self.store_names.get(v,v) if k=="store" else v for k,v in dimensions.items()}
                measures=[]
                for metric in spec.metrics:
                    value=values.get(metric)
                    old=previous.get(group,{}).get(metric)
                    year=yearly.get(group,{}).get(metric)
                    measures.append({"label":I[metric].label,"value":self.rounded(value),"unit":I[metric].unit,
                                     "previous_value":self.rounded(old),"change_percent":self.change(value,old),
                                     "year_ago_value":self.rounded(year),"year_change_percent":self.change(value,year),
                                     "status":"available" if value is not None else "unavailable"})
                row={"dimensions":dimensions,"metrics":measures}
                if spec.filter_kind=="new_products":
                    row["introduced_on"]=self.product_introductions().get(group[0])
                if spec.filter_kind=="high_stock" and (values.get("stock") or 0)>0 and values.get("units")==0:
                    row["inventory_observation"]="positive_stock_zero_sales"
                if "expiring" in spec.metrics and "product" in spec.group:
                    row["expiry_details"]=self.expiry_details(group[0],last,values.get("expiring"))
                output.append(row)
            if spec.metrics:
                index=spec.metrics.index(spec.sort_by) if spec.sort_by else 0
                output.sort(key=lambda row: (row["metrics"][index]["value"] is None, -(row["metrics"][index]["value"] or 0), str(row["dimensions"])))
            if spec.top_limit is not None:
                limit=min(limit,spec.top_limit)
            result={**base,"status":"ok","comparison_period":{"start":str(pfirst),"end":str(plast)},
                    "year_comparison_period":{"start":str(years[0]),"end":str(years[1])},
                    "comparison_note":"月份按同日进度比较，完整月份与前一完整月份比较；自定义区间与紧邻前一等长区间比较。百分比指标的change_percent是相对变化，不是百分点。",
                    "totals":[{"label":I[k].label,"value":self.rounded(totals.get(k)),"unit":I[k].unit} for k in spec.metrics] if not spec.filter_kind else [],
                    "totals_scope":"selected_stores_and_period" if not spec.filter_kind else "not_provided_for_filtered_groups",
                    "total_rows":len(output),"rows_limited":len(output)>limit,"rows":output[:limit]}
            if spec.rankings:
                result["rankings"]={}
                for metric in spec.rankings:
                    index=spec.metrics.index(metric)
                    ranked=sorted((row for row in output if row["metrics"][index]["value"] is not None),
                        key=lambda row:(-row["metrics"][index]["value"],str(row["dimensions"])))
                    entries=[]
                    last_value=None
                    rank=0
                    for position,row in enumerate(ranked,1):
                        value=row["metrics"][index]["value"]
                        if value!=last_value:
                            rank=position
                        entries.append({"rank":rank,"dimensions":row["dimensions"],"value":value})
                        last_value=value
                    result["rankings"][metric]={"label":I[metric].label,"unit":I[metric].unit,
                        "order":"descending","total_rows":len(entries),"rows":entries[:limit]}
            # Keep the result valid JSON under the existing Java tool budget.
            # Whole-range totals are computed before row selection, never inferred
            # from a limited list. `truncated` is reserved for damaged/partial
            # evidence envelopes from Java's character budget, not row selection.
            while len(json.dumps(result,ensure_ascii=False,sort_keys=True))>RESULT_BUDGET_CHARS-200 and len(result["rows"])>1:
                result["rows"].pop()
                result["rows_limited"]=True
            rankings=result.get("rankings",{})
            while len(json.dumps(result,ensure_ascii=False,sort_keys=True))>RESULT_BUDGET_CHARS-200:
                removable=[r for r in rankings.values() if len(r["rows"])>1]
                if not removable:
                    break
                max(removable,key=lambda r:len(r["rows"]))["rows"].pop()
            for ranking in rankings.values():
                ranking["returned_rows"]=len(ranking["rows"])
                ranking["rows_limited"]=len(ranking["rows"])<ranking["total_rows"]
            result["returned_rows"]=len(result["rows"])
            while len(json.dumps(result,ensure_ascii=False,sort_keys=True))>RESULT_BUDGET_CHARS:
                details=[r["expiry_details"] for r in result["rows"]
                    if len(r.get("expiry_details",{}).get("batches",[]))>1]
                if not details:
                    raise ValueError("business_result_budget_exceeded")
                detail=max(details,key=lambda d:len(d["batches"]))
                detail["batches"].pop()
                detail["returned_batches"]=len(detail["batches"])
                detail["batches_limited"]=True
            return result

    @staticmethod
    def rounded(value: float | None) -> float | None:
        return round(value,4) if value is not None else None

    @staticmethod
    def change(value: float | None, previous: float | None) -> float | None:
        if value is None or previous is None or previous==0:
            return None
        return round((value-previous)/abs(previous)*100,4)

    @staticmethod
    def formula(metric: str) -> str:
        # Public business label is the canonical definition; internal field names
        # and SQL are not model-facing facts.
        return I[metric].label

    def covered(self,key: str,first: date,last: date) -> bool:
        row=self.coverage.get(key)
        return bool(row and row["start_date"]<=str(first) and row["end_date"]>=str(last))

    def measurements(self,key: str,first: date,last: date) -> list:
        marks=",".join("?" for _ in self.stores)
        return self.db.execute(
            f"SELECT store_id,business_date,dimensions,value FROM measurements WHERE metric=? AND store_id IN ({marks}) AND business_date BETWEEN ? AND ?",
            (key,*self.stores,str(first),str(last))).fetchall()

    def aggregate(self,key: str,group: tuple[str,...],first: date,last: date) -> dict[tuple,float | None]:
        cache_key=(key,group,first,last)
        if cache_key not in self._cache:
            self._cache[cache_key]=self._aggregate(key,group,first,last)
        return self._cache[cache_key]

    def _aggregate(self,key: str,group: tuple[str,...],first: date,last: date) -> dict[tuple,float | None]:
        derived={"new_customers","return_customers","repeat_customers","repeat_return_customers"}
        source="buyers" if key in derived else "entries" if key=="entry_days" else key
        if not self.covered(source,first,last):
            return {}
        rows=self.measurements(source,first,last)
        buckets=defaultdict(list)
        for row in rows:
            dims=json.loads(row["dimensions"])
            dims["store"]=row["store_id"]
            if not all(d in dims for d in group):
                continue
            buckets[tuple(str(dims[d]) for d in group)].append((row,dims))
        result={}
        historic=set()
        if key in {"new_customers","return_customers","repeat_return_customers"}:
            coverage_start=date.fromisoformat(self.coverage["buyers"]["start_date"])
            historic={(row["store_id"],json.loads(row["dimensions"])["member"]) for row in self.measurements("buyers",coverage_start,first-timedelta(days=1))}
        for bucket,items in buckets.items():
            if key=="entry_days":
                value=len({row["business_date"] for row,dims in items})
            elif key=="platform_rank" and not group:
                result[bucket]=None
                continue
            elif key in DISTINCT or key in {"repeat_customers","repeat_return_customers"}:
                members=defaultdict(float)
                for row,dims in items:
                    members[(row["store_id"],dims["member"])]+=row["value"]
                if key=="new_customers":
                    value=len(set(members)-historic)
                elif key=="return_customers":
                    value=len(set(members)&historic)
                elif key=="repeat_customers":
                    value=sum(count>=2 for count in members.values())
                elif key=="repeat_return_customers":
                    value=sum(count>=2 for member,count in members.items() if member in historic)
                else:
                    value=len(members)
            elif key in SNAPSHOT or key in FIRST:
                latest={}
                for row,dims in items:
                    chooser=min if key in FIRST else max
                    latest[row["store_id"]]=chooser(latest.get(row["store_id"],row["business_date"]),row["business_date"])
                value=sum(row["value"] for row,dims in items if row["business_date"]==latest[row["store_id"]])
            elif key in AVERAGE:
                value=sum(row["value"] for row,dims in items)/((last-first).days+1)
            else:
                value=sum(row["value"] for row,dims in items)
            result[bucket]=float(value)
        if not group and not result and self.coverage[source]["absence_means_zero"]:
            result[()]=0.0
        return result

    def zero_for_missing_group(self,key: str,group: tuple[str,...],first: date,last: date) -> bool:
        source="buyers" if key in {"new_customers","return_customers","repeat_customers","repeat_return_customers"} else key
        if not self.covered(source,first,last) or not self.coverage[source]["absence_means_zero"]:
            return False
        # Coverage alone does not prove a metric supports a dimension (e.g. staff
        # hours cannot be silently invented for product groups). Establish shape
        # from the allowed stores' observations before filling an absent group.
        cache_key=("group_shape",source,group)
        if cache_key not in self._cache:
            marks=",".join("?" for _ in self.stores)
            dimensions=[d for d in group if d!="store"]
            checks="".join(" AND json_type(dimensions, ?) IS NOT NULL" for d in dimensions)
            self._cache[cache_key]=bool(self.db.execute(
                f"SELECT 1 FROM measurements WHERE metric=? AND store_id IN ({marks}){checks} LIMIT 1",
                (source,*self.stores,*(f"$.{d}" for d in dimensions))).fetchone())
        return self._cache[cache_key]

    def product_introductions(self) -> dict:
        key=("product_introductions",)
        if key not in self._cache:
            marks=",".join("?" for _ in self.stores)
            self._cache[key]={row["entity_id"]:json.loads(row["attributes"]).get("introduced_on") for row in self.db.execute(
                f"SELECT entity_id,attributes FROM entities WHERE entity_type='product' AND store_id IN ({marks})",self.stores)}
        return self._cache[key]

    def expiry_details(self,product: str,as_of: date,expected: float | None) -> dict:
        missing={"status":"unavailable","reason":"batch_expiry_dates_not_covered","as_of":str(as_of),"batches":[]}
        if expected is None:
            return missing
        batches=[]
        for row in self.measurements("expiring_stock",as_of,as_of):
            dims=json.loads(row["dimensions"])
            if dims.get("product")!=product or row["value"]==0:
                continue
            try:
                expiry=date.fromisoformat(dims["expires_on"])
                remaining=(expiry-as_of).days
                if not dims.get("batch_id") or not 0<=remaining<=7 or row["value"]<0:
                    return missing
            except (KeyError,ValueError,TypeError):
                return missing
            batches.append({"batch_id":dims["batch_id"],"quantity":row["value"],
                "expires_on":str(expiry),"remaining_days":remaining})
        if not batches or abs(sum(b["quantity"] for b in batches)-expected)>1e-6:
            return missing
        batches.sort(key=lambda b:(b["expires_on"],b["batch_id"]))
        return {"status":"available","as_of":str(as_of),"earliest_expiry":batches[0]["expires_on"],
            "minimum_remaining_days":batches[0]["remaining_days"],"batches":batches,
            "total_batches":len(batches),"returned_batches":len(batches),"batches_limited":False}

    def compute(self,metrics: tuple[str,...],group: tuple[str,...],first: date,last: date) -> dict:
        keys={key for metric in metrics for key,coef in (*I[metric].numerator,*I[metric].denominator)}
        dependencies={"@daily_units":"units","@daily_cogs":"cogs","@daily_fixed":"fixed_expenses",
                      "@daily_credit_sales":"credit_sales","@daily_credit_purchase":"credit_purchase"}
        values={}
        for key in keys:
            if not key.startswith("@"):
                values[key]=self.aggregate(key,group,first,last)
            elif key in dependencies:
                values[key]={k:v/((last-first).days+1) for k,v in self.aggregate(dependencies[key],group,first,last).items()}
            elif key.startswith("@all_"):
                values[key]=self.aggregate(key[5:],(),first,last)
            elif key=="@contribution_margin":
                sales=self.aggregate("sales",group,first,last)
                cogs=self.aggregate("cogs",group,first,last)
                expenses=self.aggregate("expenses",group,first,last)
                fixed=self.aggregate("fixed_expenses",group,first,last)
                values[key]={}
                for k,v in sales.items():
                    if k in cogs and k in expenses and k in fixed:
                        contribution=v-cogs[k]-expenses[k]+fixed[k]
                        values[key][k]=contribution/v if v>0 and contribution>0 else None
        groups=set().union(*(v.keys() for k,v in values.items() if not k.startswith("@all_"))) if values else {()}
        if not group:
            groups={()}
        def component(key: str,bucket: tuple) -> float | None:
            if key=="@days":
                return float((last-first).days+1)
            if key.startswith("@all_"):
                return values.get(key,{}).get(())
            found=values.get(key,{})
            if bucket in found:
                return found[bucket]
            source=dependencies.get(key,key)
            if self.zero_for_missing_group(source,group,first,last):
                return 0.0
            return None
        output={}
        for bucket in groups:
            indicators={}
            for metric in metrics:
                definition=I[metric]
                numerator=[(component(key,bucket),coef) for key,coef in definition.numerator]
                denominator=[(component(key,bucket),coef) for key,coef in definition.denominator]
                if any(v is None for v,c in (*numerator,*denominator)):
                    indicators[metric]=None
                    continue
                top=sum(v*c for v,c in numerator)
                bottom=sum(v*c for v,c in denominator) if denominator else 1
                indicators[metric]=top/bottom*definition.scale if bottom else None
            output[bucket]=indicators
        return output

    def keep_row(self,kind: str,group: tuple,values: dict,previous: dict,last: date) -> bool:
        if kind=="competitors_only":
            return group[0] not in {self.store_names[store] for store in self.stores}
        if kind=="peak_only":
            return values.get("lost") is not None
        if kind=="cold_only":
            return values.get("cold_chain") is not None
        if kind=="decline20":
            change=self.change(values.get("units"),previous.get("units"))
            return change is not None and change < -20
        if kind=="low_stock":
            return values.get("stock") is not None and values.get("safety") is not None and values["stock"]<values["safety"]
        if kind=="high_stock":
            if self.high_stock_days is None:
                return True
            return (values.get("stock_days") is not None and values["stock_days"]>self.high_stock_days
                    or values.get("stock") is not None and values["stock"]>0 and values.get("units")==0)
        if kind=="positive_first":
            return any(v is not None and v>0 for v in values.values())
        if kind=="new_products":
            introduced=self.product_introductions().get(group[0])
            if self.new_product_days is None:
                return introduced is None or date.fromisoformat(introduced)<=last
            return introduced is not None and 0<=(last-date.fromisoformat(introduced)).days<=self.new_product_days
        return True

    def entity_report(self,kind: str,first: date,last: date) -> list[dict]:
        marks=",".join("?" for _ in self.stores)
        entity_type="member" if kind=="inactive_members" else "competitor"
        entities=self.db.execute(f"SELECT store_id,entity_id,attributes FROM entities WHERE entity_type=? AND store_id IN ({marks})",(entity_type,*self.stores)).fetchall()
        latest={}
        if kind=="inactive_members":
            start=date.fromisoformat(self.coverage["member_visits"]["start_date"])
            for row in self.measurements("member_visits",start,last):
                member=json.loads(row["dimensions"])["member"]
                key=(row["store_id"],member)
                latest[key]=max(latest.get(key,""),row["business_date"])
        output=[]
        for row in entities:
            attrs=json.loads(row["attributes"])
            if kind=="inactive_members":
                if attrs["joined_on"]>str(last):
                    continue
                visited=latest.get((row["store_id"],row["entity_id"]))
                if visited is None:
                    continue  # no visit evidence does not prove a >30-day absence
                gap=(last-date.fromisoformat(visited)).days
                if gap<=30:
                    continue
                output.append({"member":attrs["name"],"last_recorded_visit":visited,"days_since_visit":gap,
                               "suggestion":"可复核联系偏好后考虑召回；本工具不发送营销消息。"})
            else:
                if attrs["distance_km"]>3 or attrs["opened_on"]>str(last):
                    continue
                if kind=="new_competitors" and attrs["opened_on"]<str(first):
                    continue
                output.append({"name":attrs["name"],"distance_km":attrs["distance_km"],"opened_on":attrs["opened_on"],"category":attrs["category"]})
        return sorted(output,key=lambda r:(-r.get("days_since_visit",0),r.get("distance_km",0),str(r)))
