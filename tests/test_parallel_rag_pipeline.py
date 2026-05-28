import json
import tempfile
import unittest
from pathlib import Path

from services.parallel_rag_pipeline import (
    LocalSyndromeKnowledgeBase,
    ParallelRAGPipeline,
    PatientInput,
    char_ngram_jaccard,
)


class ParallelRAGPipelineTest(unittest.TestCase):
    def test_semantic_retrieval_uses_synonyms_not_exact_string_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            kb_path = Path(tmp) / "syndrome.json"
            kb_path.write_text(json.dumps([
                {
                    "syndrome": "风寒袭肺",
                    "formula_name": "三拗汤",
                    "treatment_principle": "疏风散寒，宣肺止咳",
                    "core_herbs": ["麻黄", "杏仁", "甘草"],
                    "symptom_keywords": ["恶寒", "清涕", "咳喘", "白痰"],
                    "aliases": ["外寒束肺"],
                },
                {
                    "syndrome": "肺热咳喘",
                    "formula_name": "麻杏石甘汤",
                    "treatment_principle": "清肺泄热，宣肺平喘",
                    "core_herbs": ["麻黄", "石膏", "杏仁", "甘草"],
                    "symptom_keywords": ["发热", "黄痰", "咽痛", "气喘"],
                },
            ], ensure_ascii=False), encoding="utf-8")
            kb = LocalSyndromeKnowledgeBase(kb_path)
            hits = kb.retrieve(["怕冷", "鼻涕清", "喘息"], top_k=2)

        self.assertEqual(hits[0]["syndrome"], "风寒袭肺")
        self.assertGreater(hits[0]["score"], 0)

    def test_parallel_tracks_cross_check_and_five_line_output(self):
        kb = LocalSyndromeKnowledgeBase(fallback_plans={
            "风寒袭肺": {
                "main_formula": "三拗汤",
                "base_herbs": ["麻黄", "杏仁", "甘草"],
                "note": "疏风散寒，宣肺止咳",
                "rag_additions": {"喘息": [], "清涕": []},
            },
            "肺热咳喘": {
                "main_formula": "麻杏石甘汤",
                "base_herbs": ["石膏", "杏仁", "甘草"],
                "note": "清肺泄热，宣肺平喘",
                "rag_additions": {"黄痰": [], "发热": []},
            },
        })
        pipeline = ParallelRAGPipeline(kb)
        result = pipeline.run(PatientInput(
            chief_complaint="咳喘伴高血压",
            symptoms=["喘息", "怕冷", "清涕", "咳嗽"],
            history="高血压",
        ))
        lines = result["five_line_output"].splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(result["trace"]["parallel_routing"])
        self.assertEqual(result["track_a"]["trace"]["track"], "A")
        self.assertFalse(result["track_a"]["trace"]["local_tcm_rag_accessed"])
        self.assertTrue(result["track_b"]["trace"]["local_rag_only"])
        self.assertFalse(any("麻黄" in warning for warning in result["warnings"]))
        self.assertEqual(result["reranked_candidates"][0]["formula_name"], "三拗汤")

    def test_llm_reranker_weights_candidates(self):
        kb = LocalSyndromeKnowledgeBase(fallback_plans={
            "肝胆湿热": {
                "main_formula": "龙胆泻肝汤",
                "base_herbs": ["龙胆草", "黄芩"],
                "note": "清肝胆湿热",
                "rag_additions": {"口苦": [], "胁痛": []},
            },
            "脾胃虚弱": {
                "main_formula": "参苓白术散",
                "base_herbs": ["党参", "白术"],
                "note": "健脾益气",
                "rag_additions": {"纳差": [], "便溏": []},
            },
        })
        pipeline = ParallelRAGPipeline(
            kb,
            reranker=lambda payload: {"weights": {"肝胆湿热": 0.8, "脾胃虚弱": 0.2}},
        )
        result = pipeline.run(PatientInput(symptoms=["口苦", "纳差"]))
        self.assertEqual(result["reranked_candidates"][0]["syndrome"], "肝胆湿热")
        self.assertEqual(result["reranked_candidates"][0]["rerank_weight"], 0.8)

    def test_char_ngram_jaccard(self):
        self.assertGreater(char_ngram_jaccard("鼻塞流涕", "鼻塞流鼻涕"), 0)


if __name__ == "__main__":
    unittest.main()
