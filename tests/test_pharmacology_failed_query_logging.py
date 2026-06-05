import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "agent_pharmacology_miner.py"


def load_miner_module():
    spec = importlib.util.spec_from_file_location("agent_pharmacology_miner", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PharmacologyFailedQueryLoggingTest(unittest.TestCase):
    def test_failed_query_log_writer_outputs_required_jsonl_fields(self):
        miner = load_miner_module()
        original_output = miner.FAILED_QUERY_OUTPUT
        with tempfile.TemporaryDirectory() as tmpdir:
            miner.FAILED_QUERY_OUTPUT = Path(tmpdir) / "failed_queries.jsonl"
            try:
                record = miner.build_failed_query_record(
                    zh_name="乳腺癌",
                    en_name="Breast cancer",
                    query_stage="target_herb",
                    query='("Breast cancer") AND ("IL6")',
                    blocked_reason="no_herb_term",
                    recommendation_hint="review_herb_synonym",
                )
                miner.append_failed_query(record)
                lines = miner.FAILED_QUERY_OUTPUT.read_text(encoding="utf-8").splitlines()
            finally:
                miner.FAILED_QUERY_OUTPUT = original_output

        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        for field in ("zh_name", "en_name", "query_stage", "blocked_reason"):
            self.assertIn(field, row)
        self.assertEqual(row["zh_name"], "乳腺癌")
        self.assertEqual(row["query_stage"], "target_herb")
        self.assertEqual(row["blocked_reason"], "no_herb_term")


if __name__ == "__main__":
    unittest.main()
