import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transfer_agent as ta  # noqa: E402

POLICY = json.loads((ROOT / "transfer_policy.json").read_text())
AS_OF = date(2026, 11, 2)


def sample_paths():
    return {"reps": ROOT / "sample_reps.csv", "accounts": ROOT / "sample_accounts.csv",
            "deals": ROOT / "sample_open_deals.csv", "tasks": ROOT / "sample_open_tasks.csv",
            "sequences": ROOT / "sample_sequence_enrollments.csv"}


class TestNormalising(unittest.TestCase):
    def test_dates_iso_uk_us(self):
        self.assertEqual(ta.parse_date("2026-11-20"), date(2026, 11, 20))
        self.assertEqual(ta.parse_date("28/09/2026"), date(2026, 9, 28))
        self.assertEqual(ta.parse_date("10/30/2026"), date(2026, 10, 30))
        self.assertEqual(ta.parse_date("03/04/2026"), date(2026, 4, 3))  # ambiguous reads day first
        self.assertIsNone(ta.parse_date("next week"))

    def test_owner_resolver_handles_messy_values(self):
        reps = ta.build_reps(ta.load_csv(ROOT / "sample_reps.csv"), POLICY)
        resolve = ta.OwnerResolver(reps, POLICY["owner_aliases"])
        for raw in ("Dan Okafor", "DAN OKAFOR ", "dan.okafor@northbeam.io", "dan.okafor", "D. Okafor"):
            self.assertEqual(resolve(raw), "R01", raw)
        self.assertIsNone(resolve("dokafor"))

    def test_rep_regions_and_segments_normalised(self):
        reps = {r.rep_id: r for r in ta.build_reps(ta.load_csv(ROOT / "sample_reps.csv"), POLICY)}
        self.assertEqual(reps["R02"].region, "EMEA")
        self.assertEqual(reps["R03"].region, "EMEA")
        self.assertEqual(reps["R02"].segment, "Mid-Market")
        self.assertEqual(reps["R03"].status, "active")
        self.assertEqual(reps["R02"].industries, ["Fintech", "Healthcare"])


class TestPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.res = ta.run(sample_paths(), ROOT / "transfer_policy.json", cls.out, AS_OF, None)
        cls.book = {a.account_id: a for a in cls.res["book"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_book_scoped_to_departing_rep(self):
        self.assertEqual(set(self.book), {"A100", "A101", "A102", "A103", "A104", "A107"})

    def test_priority_bands(self):
        self.assertEqual(self.book["A100"].priority, "P1")
        self.assertEqual(self.book["A104"].priority, "P3")
        self.assertIn("no activity in 120+ days", self.book["A104"].reasons)

    def test_previous_owner_beats_round_robin(self):
        self.assertEqual(self.book["A102"].new_owner.name, "Priya Shah")

    def test_segment_by_employees_routes_to_enterprise(self):
        self.assertEqual(self.book["A103"].segment, "Enterprise")
        self.assertEqual(self.book["A103"].new_owner.name, "Sam Ortiz")

    def test_capacity_respected(self):
        # Tom fits Manufacturing best but is full after taking Brightwave Ltd.
        self.assertEqual(self.book["A104"].new_owner.name, "Priya Shah")
        loads = self.res["load"]
        self.assertLessEqual(loads.get("Tom Becker", 0), 1)

    def test_on_leave_rep_never_receives(self):
        owners = {a.new_owner.name for a in self.book.values() if a.new_owner}
        self.assertNotIn("Lena Park", owners)
        self.assertNotIn("Dan Okafor", owners)

    def test_review_reasons(self):
        codes = {r["account_id"]: r["reason_codes"] for r in self.res["review"]}
        self.assertIn("NAMED_ACCOUNT_SIGNOFF", codes["A100"])
        self.assertIn("LATE_STAGE_DEAL_IN_FLIGHT", codes["A100"])
        self.assertEqual(codes["A107"], "NO_ELIGIBLE_REP")
        self.assertEqual(codes["A101"], "FAMILY_OVER_CAPACITY")
        self.assertEqual(codes["A105"], "UNRESOLVED_OWNER")

    def test_import_excludes_review_accounts(self):
        ids = {r["account_id"] for r in self.res["imports"]}
        self.assertFalse(ids & {"A100", "A101", "A105", "A107"})
        self.assertTrue(all(r["new_owner_email"] for r in self.res["imports"]))

    def test_cross_owned_deal_follows_account_owner(self):
        rows = [r for r in self.res["imports"] if r["account_id"] == "A106"]
        self.assertEqual({r["record_id"] for r in rows}, {"D4", "T4"})
        self.assertTrue(all(r["new_owner_email"] == "priya.shah@northbeam.io" for r in rows))
        self.assertIn("close date in the past", next(r for r in rows if r["record_id"] == "D4")["flags"])

    def test_sequence_actions(self):
        acts = {s["contact_email"]: s for s in self.res["sequences"]}
        self.assertEqual(acts["ben@ledgerly.com"]["action"], "LET_FINISH")
        self.assertEqual(acts["kai@forgeline.de"]["action"], "UNENROL_AND_REENROL")
        self.assertEqual(acts["kai@forgeline.de"]["restart_step"], 2)
        self.assertEqual(acts["c.dupont@medcore.fr"]["action"], "UNENROL")
        self.assertEqual(acts["ana@brightwave.co.uk"]["action"], "HOLD")

    def test_output_files_written(self):
        for name in ("owner_update_import.csv", "review_queue.csv", "sequence_plan.csv", "transition_brief.md", "handoff_briefs.md"):
            self.assertTrue((self.out / name).exists(), name)
        with open(self.out / "owner_update_import.csv", newline="") as f:
            header = next(csv.reader(f))
        self.assertEqual(header[:3], ["object_type", "record_id", "record_name"])


class TestEdgeCases(unittest.TestCase):
    def test_family_follows_parent_when_capacity_allows(self):
        policy = json.loads(json.dumps(POLICY))
        reps = ta.build_reps(ta.load_csv(ROOT / "sample_reps.csv"), policy)
        for r in reps:
            if r.rep_id == "R03":
                r.max_accounts = 50
        resolve = ta.OwnerResolver(reps, policy["owner_aliases"])
        accts = ta.build_accounts(ta.load_csv(ROOT / "sample_accounts.csv"), resolve, policy)
        book = [a for a in accts.values() if a.owner_id == "R01"]
        for a in book:
            ta.prioritise(a, policy, AS_OF)
        ta.assign(book, accts, reps, policy, {"R01"})
        self.assertEqual(accts["A101"].new_owner.rep_id, accts["A100"].new_owner.rep_id)
        self.assertNotIn("FAMILY_OVER_CAPACITY", accts["A101"].review)

    def test_explicit_departing_unknown_rep_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                ta.run(sample_paths(), ROOT / "transfer_policy.json", Path(d), AS_OF, "nobody@example.com")

    def test_no_api_key_uses_templates(self):
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertIsNone(ta.llm_text({"x": 1}))


if __name__ == "__main__":
    unittest.main()
