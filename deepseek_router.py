"""
DeepSeek Model Router — 自动路由模块
====================================
根据任务复杂度自动选择模型。

路由规则：
- 所有任务 → deepseek-v4-pro（统一默认使用 pro 模型）
- pro + reasoning: 疑难任务（诊断错误、安全边界绕过、多模块链路断裂、同一问题 debug >2 轮）
- flash 路由表保留命名，但实际 model 统一为 deepseek-v4-pro

配置：
  环境变量 DEEPSEEK_API_KEY（必填）
  环境变量 DEEPSEEK_FLASH_MODEL（可选，默认 deepseek-v4-pro）
  环境变量 DEEPSEEK_PRO_MODEL（可选，默认 deepseek-v4-pro）
  环境变量 DEEPSEEK_API_BASE（可选，默认 https://api.deepseek.com）
"""

import os
import json
from typing import Dict, Optional, List


# ── 任务复杂度分级 ──

# 所有任务默认使用 deepseek-v4-pro（flash 路由表保留命名但实际 model 同 pro）
FLASH_TASKS = {
    "file_open",           # 打开文件（仅浏览，不修改）
    "file_search",         # 搜索文件内容
    "list_extract",        # 提取清单（目录、文件列表、测试列表）
    "simple_explain",      # 不涉及逻辑判断的简单解释
}

# 复杂任务 → pro（所有代码修改、逻辑判断、医学推理、架构判断）
PRO_TASKS = {
    "write_code",              # 写代码、改代码
    "fix_test",                # 修复测试
    "multi_file_refactor",     # 多文件重构
    "architecture_judge",      # M1/M2/M3/M4 架构判断
    "prompt_audit",            # prompt / skill 审核
    "diagnosis_debug",         # 诊断链路失败排查
    "medical_judgment",        # 需医学逻辑判断（含外部搜索回源）
    "safety_guard",            # formal_gate / safety guard 判断
    "prescription_review",     # 处方链路、M3 安全审核
    "cross_module_bug",        # 跨模块 bug 调试
    "retry_failed",            # 同一问题第二轮仍不通过
}

# 疑难任务 → pro + reasoning
# 涉及医学诊断链路、Skill 与代码一致性、测试失败根因分析
PRO_REASONING_TASKS = {
    "m1_diagnosis_error",      # M1 诊断错误
    "m2_syndrome_error",       # M2 辨证错误
    "m3_high_risk_miss",       # M3 高风险病例未拦截
    "m4_contraindication",     # M4 禁忌冲突判断
    "legacy_path_bypass",      # legacy path 可能绕过安全边界
    "prompt_code_mismatch",    # prompt/spec 与代码不一致
    "skill_v2_consistency",    # Skill v2.x 与代码一致性审计
    "test_failure_root_cause", # 测试失败根因分析
    "multi_module_chain_broken", # 多模块链路跑不通
    "repeated_debug_2_plus",   # 同一问题 debug 超过 2 轮
    "unclear_test_fail",       # 测试失败原因不明确
}


class DeepSeekModelRouter:
    """
    DeepSeek 模型自动路由器。

    用法：
        router = DeepSeekModelRouter()
        model, use_reasoning = router.route("diagnosis_debug")
        # → ("deepseek-v4-pro", False)

        model, use_reasoning = router.route("m2_syndrome_error")
        # → ("deepseek-v4-pro", True)

        model, use_reasoning = router.route("run_test")
        # → ("deepseek-v4-pro", False)
    """

    def __init__(self):
        # flash_model 固定为 deepseek-v4-pro，不接收环境变量覆盖（彻底禁用 flash 分支）
        self.flash_model = "deepseek-v4-pro"
        # pro_model 可被环境变量覆盖（仅限 pro 级别调整）
        _env_pro = os.environ.get("DEEPSEEK_PRO_MODEL", "")
        _env_fallback = os.environ.get("DEEPSEEK_MODEL", "")
        self.pro_model = _env_pro or _env_fallback or "deepseek-v4-pro"
        self.api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            print("[ROUTER_WARN] ⚠ DEEPSEEK_API_KEY 未设置！所有 LLM 调用将失败"
                  "（生产环境必须先设置该环境变量）")
            print("[ROUTER_WARN] ⚠ export DEEPSEEK_API_KEY=sk-xxx")
        print(f"[ROUTER_INIT] flash_model（已固定为 pro）={self.flash_model} pro_model={self.pro_model}")
        print(f"[ROUTER_INIT] 路由表: flash={sorted(FLASH_TASKS)}（已全部映射到 pro）")
        print(f"[ROUTER_INIT]          pro={sorted(PRO_TASKS)}")
        print(f"[ROUTER_INIT]          pro+reasoning={sorted(PRO_REASONING_TASKS)}")

    def route(self, task_type: str) -> tuple:
        """
        根据任务类型路由到合适的模型。

        Args:
            task_type: 任务类型标识符

        Returns:
            (model_name: str, use_reasoning: bool)
        """
        if task_type in PRO_REASONING_TASKS:
            return (self.pro_model, True)

        if task_type in PRO_TASKS:
            return (self.pro_model, False)

        # 默认或 flash 任务 → pro（统一使用 pro）
        return (self.pro_model, False)

    def build_payload(self, prompt: str, task_type: str,
                      temperature: float = 0.1,
                      max_tokens: int = 500,
                      system_prompt: Optional[str] = None) -> Dict:
        """
        构建 API 请求 payload（含自动路由选择模型和 reasoning 参数）。

        Args:
            prompt: 用户 prompt
            task_type: 任务类型标识符
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 可选系统提示词

        Returns:
            API 请求 payload dict
        """
        model, use_reasoning = self.route(task_type)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if use_reasoning:
            # V4 Preview: thinking 默认开启，显式设置 reasoning 参数
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "high"
        else:
            # 非 thinking 任务：必须显式禁用，否则 V4 Preview 默认 thinking 开启
            # 导致 content 为空，输出全在 reasoning_content
            payload["thinking"] = {"type": "disabled"}

        return payload

    def call(self, prompt: str, task_type: str,
             temperature: float = 0.1,
             max_tokens: int = 500,
             system_prompt: Optional[str] = None,
             timeout: int = 30) -> Optional[str]:
        """
        调用 DeepSeek API，自动路由模型。

        Args:
            prompt: 用户 prompt
            task_type: 任务类型标识符
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 可选系统提示词
            timeout: 超时秒数

        Returns:
            API 响应文本，失败返回 None
        """
        if not self.api_key:
            print("[ROUTER_ERR] DEEPSEEK_API_KEY 未设置")
            return None

        payload = self.build_payload(
            prompt, task_type,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

        model, use_reasoning = self.route(task_type)
        print(f"[ROUTER] task={task_type} model={model} reasoning={use_reasoning}")

        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.api_base}/v1/chat/completions"
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                content = msg.get("content", "").strip()
                if not content:
                    content = msg.get("reasoning_content", "").strip()
                return content if content else None
            else:
                print(f"[ROUTER_ERR] API {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"[ROUTER_ERR] {e}")
            return None

    def get_model_info(self, task_type: str) -> Dict:
        """返回当前路由信息（用于日志/验收）"""
        model, use_reasoning = self.route(task_type)
        complexity = "pro"  # 所有任务统一 pro，flash 路由表保留命名作为兼容
        if task_type in PRO_TASKS:
            complexity = "pro"
        if task_type in PRO_REASONING_TASKS:
            complexity = "pro+reasoning"
        return {
            "task_type": task_type,
            "model": model,
            "use_reasoning": use_reasoning,
            "complexity": complexity,
            "flash_model": self.flash_model,
            "pro_model": self.pro_model,
        }


# ── 单例 ──
_router_instance: Optional[DeepSeekModelRouter] = None


def get_router() -> DeepSeekModelRouter:
    """获取全局单例 router"""
    global _router_instance
    if _router_instance is None:
        _router_instance = DeepSeekModelRouter()
    return _router_instance


def route_task(task_type: str) -> tuple:
    """快捷路由：返回 (model, use_reasoning)"""
    return get_router().route(task_type)


def call_llm_with_router(prompt: str, task_type: str = "simple_explain",
                          temperature: float = 0.1,
                          max_tokens: int = 500,
                          system_prompt: Optional[str] = None) -> Optional[str]:
    """快捷调用：自动路由后调用 API"""
    return get_router().call(prompt, task_type, temperature, max_tokens, system_prompt)
