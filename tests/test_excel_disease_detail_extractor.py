import json
import unittest

import excel_disease_detail_extractor as extractor


FORBIDDEN = [
    "方剂", "基础方", "中药", "治法", "治则", "辨证", "证型", "病机",
    "处方", "用药", "剂量", "加减", "药店", "气虚", "阴虚", "阳虚", "寒证", "热证",
]


class ExcelDiseaseDetailExtractorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = extractor.build_excel_diagnostic_details()

    def test_extracts_all_manifest_diseases(self):
        self.assertGreaterEqual(self.result["cleaned_disease_count"], 600)
        self.assertEqual(self.result["cleaned_disease_count"], len(self.result["diseases"]))

    def test_extracts_western_details_for_most_diseases(self):
        self.assertGreaterEqual(self.result["diseases_with_any_western_detail"], 600)
        self.assertGreater(self.result["diseases_with_required_checks"], 300)

    def test_no_tcm_or_medication_content_in_detail_cache(self):
        blob = json.dumps(self.result["diseases"], ensure_ascii=False)
        for term in FORBIDDEN:
            self.assertNotIn(term, blob)

    def test_rhinitis_asthma_has_objective_diagnostic_markers(self):
        card = _find(self.result, "鼻炎哮喘综合征")
        blob = json.dumps(card, ensure_ascii=False)
        for term in ["EOS", "FeNO", "PEF", "SpO2"]:
            self.assertIn(term, blob)
        self.assertTrue(card["clinical_manifestations"])
        axes = {item["axis"] for item in card["pathology_axes"]}
        self.assertIn("allergic_th2_inflammation", axes)
        self.assertIn("lower_airway_involvement", axes)

    def test_pending_cards_include_excel_backfilled_diseases(self):
        cards = json.loads(extractor.PENDING_PATH.read_text(encoding="utf-8"))
        names = {card["disease_name"] for card in cards}
        self.assertIn("鼻炎哮喘综合征", names)
        self.assertGreaterEqual(len(names), 600)

    def test_report_written(self):
        text = extractor.REPORT_PATH.read_text(encoding="utf-8")
        self.assertIn("cleaned_disease_count", text)
        self.assertIn("diseases_with_any_western_detail", text)


def _find(result, name):
    for item in result["diseases"]:
        if item["standard_western_name"] == name:
            return item
    raise AssertionError(f"missing disease: {name}")


if __name__ == "__main__":
    unittest.main()
