"""
M1 诊断推理引擎 — DeepSeek 语义增强版
=====================================
继承 M1DiagnosisEngine，注入 DeepSeek API 做语义增强。

用法：
    from m1_deepseek_engine import M1WithDeepSeek
    m1 = M1WithDeepSeek()
    result = m1.diagnose("Allergic Contact Dermatitis", ["双外眼睑红肿痒痛", ...])
    print(m1.format_diagnosis_result(result))

环境变量：
    DEEPSEEK_API_KEY  — DeepSeek API key（必填）
"""

import json
import os
import urllib.request
import urllib.error
from m1_engine import M1DiagnosisEngine


class M1WithDeepSeek(M1DiagnosisEngine):
    """M1 引擎 + DeepSeek 语义增强"""

    def __init__(
        self,
        db_path=None,
        api_key=None,
        model="deepseek-v4-pro",
        api_base="https://api.deepseek.com",
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model
        self.api_base = api_base.rstrip("/")
        super().__init__(db_path)

    def _call_llm_api(self, prompt: str) -> dict:
        """调用 DeepSeek API 做语义分析"""
        if not self.api_key:
            print("  ⚠ DEEPSEEK_API_KEY 未设置，跳过语义增强")
            return None

        url = f"{self.api_base}/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个临床医学语义分析专家。"
                        "分析患者症状与疾病标准表现的语义匹配关系，只输出 JSON，不要其他文字。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        })

        req = urllib.request.Request(url, data=payload.encode("utf-8"))
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            print(
                f"  ✅ DeepSeek 返回: suggested_score={parsed.get('suggested_overall_score', 'N/A')}"
            )
            return parsed
        except urllib.error.HTTPError as e:
            print(f"  ❌ HTTP {e.code}: {e.read().decode()[:200]}")
            return None
        except Exception as e:
            print(f"  ❌ API 错误: {e}")
            return None


# ── 独立使用 ──────────────────────────────────────────

if __name__ == "__main__":
    m1 = M1WithDeepSeek()

    symptoms = [
        "双外眼睑红肿痒痛",
        "灼热刺痛",
        "睾丸红痒痛",
        "眼睑冰敷无缓解",
        "体温37.2",
        "口干不苦",
        "大便不成型",
        "舌胖大",
        "苔薄黄",
        "装修工作",
    ]

    print("=" * 70)
    print("【病案】梁兴伟 44岁 | 外眼睑红肿痒痛伴睾丸红痒痛2天")
    print("=" * 70)

    print("\n>>> Allergic Contact Dermatitis...")
    result = m1.diagnose("Allergic Contact Dermatitis", symptoms)
    print(m1.format_diagnosis_result(result))

    print("\n>>> Contact Dermatitis...")
    result2 = m1.diagnose("Contact Dermatitis", symptoms)
    print(m1.format_diagnosis_result(result2))

    print(f"\n缓存文件: {m1.semantic_cache_path}")
    print(f"缓存条目: {len(m1.semantic_cache)} 条（永久有效）")
