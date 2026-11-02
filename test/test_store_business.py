"""Operating analytics: accounting reconciliation, scope, dates, and MCP boundary."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent.analytics.store_business.seed import seed_database
from agent.analytics.store_business.service import BusinessQueryService, query_window
from agent.analytics.store_business.schema import open_readonly
from agent.mcp_server.business_server import BusinessMCPServer, configured_server, tool_definitions


class StoreBusinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=Path(cls.temp.name)/"operating.db"
        seed_database(cls.path,end=date(2026,10,27),days=92,seed=7)
        cls.service=BusinessQueryService(cls.path,allowed_stores=("business-store-001","business-store-002","business-store-003"),
                                        default_store="business-store-001",today=date(2026,10,27),
                                        high_stock_days=90,new_product_days=30)

    @contextmanager
    def editable_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"fixture.db"
            shutil.copyfile(self.path,path)
            yield path

    def service_at(self,path,**rules):
        return BusinessQueryService(path,allowed_stores=("business-store-001",),
            default_store="business-store-001",today=date(2026,10,27),**rules)

    @staticmethod
    def product_row(report,product):
        return next(row for row in report["rows"] if row["dimensions"].get("product")==product)

    def test_refund_and_exchange_have_independent_rankings_even_at_limit_one(self):
        with self.editable_fixture() as path:
            with closing(sqlite3.connect(path)) as db:
                dataset=db.execute("SELECT id FROM dataset").fetchone()[0]
                db.execute("DELETE FROM measurements WHERE store_id='business-store-001' AND business_date>='2026-10-01' AND metric IN ('units','refund_units','exchange_units')")
                for index in range(12):
                    product=f"商品{index:02d}"
                    for metric,value in (("units",2 if index==0 else 3 if index==1 else 1000),
                                         ("refund_units",1 if index<2 else 20),
                                         ("exchange_units",2 if index==1 else 0)):
                        db.execute("INSERT INTO measurements(dataset_id,store_id,business_date,metric,dimensions,value) VALUES (?,?,?,?,?,?)",
                            (dataset,"business-store-001","2026-10-27",metric,json.dumps({"product":product}),value))
                db.commit()
            service=self.service_at(path)
            for limit in (1,20,50):
                result=service.query(20,limit=limit)
                self.assertEqual("商品00",result["rankings"]["refund_rate"]["rows"][0]["dimensions"]["product"])
                self.assertEqual("商品01",result["rankings"]["exchange_rate"]["rows"][0]["dimensions"]["product"])
                self.assertEqual(50,result["rankings"]["refund_rate"]["rows"][0]["value"])
                self.assertAlmostEqual(200/3,result["rankings"]["exchange_rate"]["rows"][0]["value"],places=4)
                self.assertLess(len(json.dumps(result,ensure_ascii=False)),9000)

    def test_zero_sales_are_declines_and_stock_attention_but_unknown_is_not_zero(self):
        with self.editable_fixture() as path:
            with closing(sqlite3.connect(path)) as db:
                db.execute("DELETE FROM measurements WHERE store_id='business-store-001' AND business_date='2026-10-27' AND metric IN ('units','sales','cogs') AND json_extract(dimensions,'$.product')='曲奇'")
                db.commit()
            service=self.service_at(path,high_stock_days=90)
            result=service.query(12,period="today")
            units=self.product_row(result,"曲奇")["metrics"][0]
            self.assertEqual(0,units["value"])
            self.assertEqual(-100,units["change_percent"])
            row=self.product_row(service.query(15,period="today"),"曲奇")
            self.assertEqual("positive_stock_zero_sales",row["inventory_observation"])
            self.assertIsNone(row["metrics"][1]["value"])
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE metric_coverage SET absence_means_zero=0 WHERE metric='units'")
                db.commit()
            self.assertNotIn("曲奇",[row["dimensions"]["product"] for row in service.query(12,period="today")["rows"]])
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE metric_coverage SET absence_means_zero=1,end_date='2026-10-26' WHERE metric='units'")
                db.commit()
            self.assertEqual([],service.query(12,period="today")["rows"])

    def test_unconfigured_rules_return_facts_and_explicit_config_changes_classification(self):
        service=self.service_at(self.path)
        for question in (14,15):
            result=service.query(question)
            self.assertEqual("not_configured",result["classification"]["status"])
            self.assertGreater(len(result["rows"]),1)
        self.assertTrue(all("introduced_on" in row for row in service.query(14)["rows"]))
        self.assertEqual([],self.service_at(self.path,new_product_days=5).query(14)["rows"])
        self.assertEqual(["保温杯"],[r["dimensions"]["product"] for r in self.service_at(self.path,new_product_days=30).query(14)["rows"]])
        self.assertEqual([],self.service_at(self.path,high_stock_days=10000).query(15)["rows"])
        for rules in ({"new_product_days":0},{"high_stock_days":True},{"high_stock_days":-1}):
            with self.assertRaises(ValueError):
                self.service_at(self.path,**rules)

    def test_expiry_batches_reconcile_and_dates_use_requested_snapshot(self):
        for end in ("2026-10-15","2026-10-27"):
            result=self.service.query(28,period="custom",start_date=end,end_date=end)
            for row in result["rows"]:
                detail=row["expiry_details"]
                self.assertEqual("available",detail["status"])
                self.assertEqual(end,detail["as_of"])
                self.assertEqual(row["metrics"][0]["value"],sum(b["quantity"] for b in detail["batches"]))
                self.assertEqual(2,detail["minimum_remaining_days"])
                for batch in detail["batches"]:
                    self.assertEqual(batch["remaining_days"],(date.fromisoformat(batch["expires_on"])-date.fromisoformat(end)).days)

    def test_old_or_partial_expiry_records_never_invent_deadlines(self):
        with self.editable_fixture() as path:
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE measurements SET dimensions=json_remove(dimensions,'$.expires_on') WHERE metric='expiring_stock' AND business_date='2026-10-27'")
                db.commit()
            report=self.service_at(path).query(28)
            self.assertTrue(report["rows"])
            for row in report["rows"]:
                self.assertGreater(row["metrics"][0]["value"],0)
                self.assertEqual("unavailable",row["expiry_details"]["status"])
                self.assertNotIn("earliest_expiry",row["expiry_details"])

    def test_large_expiry_batch_list_is_bounded_without_losing_earliest_date(self):
        with self.editable_fixture() as path:
            with closing(sqlite3.connect(path)) as db:
                dataset=db.execute("SELECT id FROM dataset").fetchone()[0]
                db.execute("DELETE FROM measurements WHERE store_id='business-store-001' AND business_date='2026-10-27' AND metric='expiring_stock' AND json_extract(dimensions,'$.product')='曲奇'")
                db.executemany("INSERT INTO measurements(dataset_id,store_id,business_date,metric,dimensions,value) VALUES (?,?,?,?,?,?)",
                    [(dataset,"business-store-001","2026-10-27","expiring_stock",
                      json.dumps({"product":"曲奇","category":"烘焙","batch_id":f"batch-{i:03d}","expires_on":"2026-10-28"}),1)
                     for i in range(200)])
                db.commit()
            result=self.service_at(path).query(28)
            detail=self.product_row(result,"曲奇")["expiry_details"]
            self.assertEqual("2026-10-28",detail["earliest_expiry"])
            self.assertEqual(200,detail["total_batches"])
            self.assertTrue(detail["batches_limited"])
            self.assertEqual(len(detail["batches"]),detail["returned_batches"])
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=False,sort_keys=True)),8500)

    def test_area_ranking_uses_area_sales_instead_of_revenue(self):
        with closing(sqlite3.connect(self.path)) as db:
            expected=db.execute("SELECT s.name,sum(m.value)/s.floor_area FROM measurements m JOIN stores s ON s.id=m.store_id WHERE m.metric='sales' AND m.business_date BETWEEN '2026-10-01' AND '2026-10-27' GROUP BY s.id ORDER BY 2 DESC").fetchone()[0]
        self.assertEqual(expected,self.service.query(73,limit=1)["rows"][0]["dimensions"]["store"])

    def test_tool_domains_do_not_depend_on_question_file_order(self):
        from agent.mcp_server import business_server
        original=tool_definitions()
        with patch.object(business_server,"QUESTIONS",list(reversed(business_server.QUESTIONS))):
            self.assertEqual(original,tool_definitions())

    def test_trusted_optional_rules_are_loaded_and_invalid_configuration_rejected(self):
        env={"RETAILVISION_BUSINESS_MCP_ENABLED":"true","RETAILVISION_BUSINESS_DATABASE_PATH":str(self.path),
             "RETAILVISION_BUSINESS_ALLOWED_STORES":"business-store-001","RETAILVISION_BUSINESS_DEFAULT_STORE":"business-store-001"}
        with patch.dict(os.environ,env,clear=True):
            self.assertIsNone(configured_server().service.new_product_days)
        for value in ("0","-1","abc","1.5"):
            with patch.dict(os.environ,{**env,"RETAILVISION_BUSINESS_HIGH_STOCK_DAYS":value},clear=True),self.assertRaises(ValueError):
                configured_server()
        with patch.dict(os.environ,{**env,"RETAILVISION_BUSINESS_NEW_PRODUCT_DAYS":"14"},clear=True):
            self.assertEqual(14,configured_server().service.new_product_days)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def raw_sum(self,metric):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT sum(value) FROM measurements WHERE metric=? AND store_id='business-store-001' AND business_date BETWEEN '2026-10-01' AND '2026-10-27'",(metric,)).fetchone()[0]

    @staticmethod
    def totals(report):
        return {m["label"]:m["value"] for m in report["totals"]}

    def test_all_100_reports_read_persisted_data_without_writes_and_fit_tool_budget(self):
        before=hashlib.sha256(self.path.read_bytes()).digest()
        for question in range(1,101):
            with self.subTest(question=question):
                result=self.service.query(question)
                self.assertEqual("ok",result["status"])
                self.assertEqual(question,result["question_id"])
                self.assertLess(len(json.dumps(result,ensure_ascii=False,sort_keys=True)),9000)
                self.assertNotIn("SQL",json.dumps(result,ensure_ascii=False))
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).digest())

    def test_sales_payments_product_totals_and_profit_reconcile(self):
        sales=self.raw_sum("sales")
        cogs=self.raw_sum("cogs")
        expenses=self.raw_sum("expenses")
        income=self.totals(self.service.query(31))
        payments=self.totals(self.service.query(36))
        profit=self.totals(self.service.query(33))
        self.assertAlmostEqual(sales/100,income["销售净额"],places=4)
        self.assertAlmostEqual(sales/100,payments["支付流水金额"],places=4)
        self.assertEqual(0,payments["支付与结算差额"])
        self.assertAlmostEqual((sales-cogs-expenses)/100,profit["经营净收益（含已入账税费）"],places=4)
        self.assertAlmostEqual((sales-cogs)/sales*100,profit["毛利率"],places=4)
        ranking=self.service.query(11)
        self.assertEqual(10,len(ranking["rows"]))
        self.assertTrue(ranking["rows_limited"])
        self.assertFalse(ranking.get("truncated",False))
        self.assertEqual(100,self.totals(ranking)["销售净额占比"])
        self.assertEqual(income["销售净额"],self.totals(ranking)["销售净额"])
        cost=self.totals(self.service.query(32))["成本费用"]
        self.assertAlmostEqual((cogs+expenses)/100,cost,places=4)

    def test_limited_rows_keep_independently_calculated_totals(self):
        result=self.service.query(17,limit=3)
        self.assertEqual(3,result["returned_rows"])
        self.assertGreater(result["total_rows"],3)
        self.assertTrue(result["rows_limited"])
        self.assertFalse(result.get("truncated",False))
        pairs=self.totals(result)["同篮购买订单数"]
        self.assertEqual(self.raw_sum("pairs"),pairs)
        self.assertGreater(pairs,sum(row["metrics"][0]["value"] for row in result["rows"]))

    def test_ticket_is_ratio_of_totals_not_average_of_product_prices(self):
        ticket=self.totals(self.service.query(4))["每单平均销售净额"]
        self.assertAlmostEqual(self.raw_sum("sales")/self.raw_sum("orders")/100,ticket,places=4)

    def test_breakeven_includes_variable_expenses_in_contribution_margin(self):
        sales=self.raw_sum("sales")
        cogs=self.raw_sum("cogs")
        expenses=self.raw_sum("expenses")
        fixed=self.raw_sum("fixed_expenses")
        contribution=(sales-cogs-(expenses-fixed))/sales
        result=self.totals(self.service.query(34))["日均盈亏平衡销售额估计"]
        self.assertGreater(expenses,fixed)
        self.assertGreater(contribution,0)
        self.assertAlmostEqual(fixed/27/100/contribution,result,places=4)
        self.assertGreater(result,fixed/27/100/((sales-cogs)/sales))

    def test_total_acquisition_cost_uses_all_spend_and_new_customers(self):
        result=self.totals(self.service.query(66))
        self.assertEqual(self.raw_sum("acquired_customers"),result["期间获取新客数"])
        self.assertAlmostEqual(self.raw_sum("marketing_spend")/100,result["营销投入"],places=4)
        self.assertAlmostEqual(self.raw_sum("marketing_spend")/100/self.raw_sum("acquired_customers"),result["新客获取成本"],places=4)

    def test_filtered_reports_do_not_label_store_totals_as_subset_totals(self):
        fresh=self.service.query(14)
        self.assertEqual([],fresh["totals"])
        self.assertEqual(["保温杯"],[r["dimensions"]["product"] for r in fresh["rows"]])
        competitors=self.service.query(84)
        self.assertEqual([],competitors["totals"])
        self.assertNotIn("春禾中心店",[r["dimensions"]["competitor"] for r in competitors["rows"]])

    def test_roi_percent_and_capital_use_same_money_units(self):
        report=self.totals(self.service.query(40))
        capital=650000
        self.assertEqual(capital,report["投入资本"])
        self.assertAlmostEqual(report["经营净收益（含已入账税费）"]/capital*100,report["期间经营净收益/投入资本"],places=4)

    def test_inventory_is_last_snapshot_not_sum_of_snapshots(self):
        with closing(sqlite3.connect(self.path)) as connection:
            expected=connection.execute("SELECT sum(value) FROM measurements WHERE metric='stock' AND store_id='business-store-001' AND business_date='2026-10-27'").fetchone()[0]
        self.assertEqual(expected,self.totals(self.service.query(21))["期末库存数量"])
        self.assertGreater(self.raw_sum("stock"),expected)
        low=self.service.query(22)
        self.assertEqual(["牛奶"],[r["dimensions"]["product"] for r in low["rows"]])
        self.assertTrue(self.service.query(15)["rows"])

    def test_traffic_breakdowns_reconcile_and_weather_is_daily_normalized(self):
        total=self.totals(self.service.query(1))["进店人次"]
        for question in (2,9):
            self.assertEqual(total,sum(r["metrics"][0]["value"] for r in self.service.query(question)["rows"]))
        self.assertGreater(len(self.service.query(9)["rows"]),1)
        self.assertTrue(all(r["metrics"][0]["unit"]=="人次/天" for r in self.service.query(10)["rows"]))

    def test_customer_repeat_counts_distinct_members_and_uses_real_order_counts(self):
        with closing(sqlite3.connect(self.path)) as connection:
            rows=connection.execute("SELECT json_extract(dimensions,'$.member'),sum(value) FROM measurements WHERE metric='buyers' AND store_id='business-store-001' AND business_date BETWEEN '2026-10-01' AND '2026-10-27' GROUP BY json_extract(dimensions,'$.member')").fetchall()
            historic={r[0] for r in connection.execute("SELECT DISTINCT json_extract(dimensions,'$.member') FROM measurements WHERE metric='buyers' AND store_id='business-store-001' AND business_date<'2026-10-01'")}
        actual=self.totals(self.service.query(68))["来源渠道顾客复购率"]
        self.assertAlmostEqual(sum(count>=2 for _,count in rows)/len(rows)*100,actual,places=4)
        old_counts=[count for member,count in rows if member in historic]
        old_rate=self.totals(self.service.query(3))["期内再次购买至少两次的老客占比"]
        self.assertAlmostEqual(sum(count>=2 for count in old_counts)/len(old_counts)*100,old_rate,places=4)
        self.assertTrue(self.service.query(7)["rows"])
        self.assertTrue(all(r["days_since_visit"]>30 for r in self.service.query(7)["rows"]))

    def test_zero_denominator_is_unavailable_not_zero_and_no_future_data(self):
        # No October hires on this particular day: onboarding is undefined.
        report=self.service.query(46,period="custom",start_date="2026-10-11",end_date="2026-10-11")
        self.assertIsNone(self.totals(report)["新员工平均上手周期"])
        future=self.service.query(31,period="custom",start_date="2027-01-01",end_date="2027-01-31")
        self.assertEqual("unavailable",future["status"])
        self.assertEqual([],future["rows"])
        self.assertTrue(all(m["year_ago_value"] is None for m in self.service.query(31)["rows"][0]["metrics"]))

    def test_month_and_week_comparisons_preserve_progress_and_leap_boundaries(self):
        self.assertEqual((date(2026,10,1),date(2026,10,27),date(2026,9,1),date(2026,9,27)),query_window("this_month",date(2026,10,27),None,None))
        self.assertEqual((date(2024,3,1),date(2024,3,31),date(2024,2,1),date(2024,2,29)),query_window("this_month",date(2024,3,31),None,None))
        first,last,previous_first,previous_last=query_window("this_week",date(2026,10,27),None,None)
        self.assertEqual(0,first.weekday())
        self.assertEqual(7,(first-previous_first).days)

    def test_scope_and_invalid_arguments_are_rejected(self):
        for kwargs in ({"store_id":"other-tenant"},{"question_id":True},{"limit":True},{"limit":0},{"period":"custom","start_date":"2026-10-01"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                self.service.query(**({"question_id":31}|kwargs))
        self.assertEqual(3,len(self.service.query(18)["rows"]))
        self.assertEqual(1,len(self.service.query(18,store_id="business-store-002")["rows"]))

    def test_database_read_only_and_seed_refuses_existing_target(self):
        with closing(open_readonly(self.path)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM measurements")
        with self.assertRaises(FileExistsError):
            seed_database(self.path,end=date(2026,10,27),days=92)
        absent=Path(self.temp.name)/"absent.db"
        with self.assertRaises(FileNotFoundError):
            open_readonly(absent)
        self.assertFalse(absent.exists())

    def test_mcp_initialization_discovery_dispatch_and_disabled_configuration(self):
        server=BusinessMCPServer(self.service)
        self.assertIn("error",server.handle({"id":1,"method":"tools/list"}))
        server.handle({"id":2,"method":"initialize","params":{"protocolVersion":"2024-11-05"}})
        tools=server.handle({"id":3,"method":"tools/list"})["result"]["tools"]
        self.assertEqual(10,len(tools))
        self.assertEqual(set(range(1,101)),set().union(*(set(t["inputSchema"]["properties"]["question_id"]["enum"]) for t in tools)))
        call={"id":4,"method":"tools/call","params":{"name":"query_business_finance","arguments":{"question_id":31}}}
        result=server.handle(call)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(31,json.loads(result["content"][0]["text"])["question_id"])
        call["params"]["arguments"]["sql"]="SELECT * FROM members"
        self.assertTrue(server.handle(call)["result"]["isError"])
        with patch.dict(os.environ,{"RETAILVISION_BUSINESS_MCP_ENABLED":"false","RETAILVISION_BUSINESS_DATABASE_PATH":"does-not-exist"}):
            self.assertIsNone(configured_server().service)

    def test_initialize_separates_fact_constraints_from_tool_routing(self):
        instructions=BusinessMCPServer(self.service).handle({"id":1,"method":"initialize"})["result"]["instructions"]
        constraints=[line for line in instructions.splitlines() if line.startswith("Public fact interpretation contract:")]
        self.assertTrue(any("classification.status=not_configured" in line for line in constraints))
        self.assertTrue(any("comparison_period" in line for line in constraints))
        self.assertTrue(any("rows_limited" in line for line in constraints))
        self.assertTrue(any("预置" in line for line in constraints))
        self.assertFalse(any("query_business_" in line for line in constraints))

    def test_churn_question_is_discovered_and_executed_as_customer_analysis(self):
        server=BusinessMCPServer(self.service)
        server.handle({"id":1,"method":"initialize"})
        tools=server.handle({"id":2,"method":"tools/list"})["result"]["tools"]
        owners=[tool["name"] for tool in tools if 59 in tool["inputSchema"]["properties"]["question_id"]["enum"]]
        self.assertEqual(["query_business_customers"],owners)
        request={"id":3,"method":"tools/call","params":{"name":owners[0],"arguments":{"question_id":59}}}
        result=server.handle(request)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(59,json.loads(result["content"][0]["text"])["question_id"])
        request["params"]["name"]="query_business_feedback"
        self.assertTrue(server.handle(request)["result"]["isError"])

    def test_discovered_store_names_are_restricted_to_allowed_scope(self):
        service=BusinessQueryService(self.path,allowed_stores=("business-store-002",),default_store="business-store-002")
        result=BusinessMCPServer(service).handle({"id":1,"method":"initialize"})["result"]
        self.assertIn("春禾东街店=business-store-002",result["instructions"])
        self.assertNotIn("春禾中心店",result["instructions"])
        with self.assertRaises(ValueError):
            service.query(31,store_id="business-store-001")


if __name__=="__main__":
    unittest.main()
