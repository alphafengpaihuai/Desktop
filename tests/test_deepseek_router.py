"""
Test: DeepSeek Model Auto-Router
================================"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deepseek_router import (
    DeepSeekModelRouter,
    FLASH_TASKS,
    PRO_TASKS,
    PRO_REASONING_TASKS,
)


class TestDeepSeekRouter(unittest.TestCase):
    """验证自动路由规则"""

    def setUp(self):
        self.router = DeepSeekModelRouter()
        # 默认值
        self.flash_default = "deepseek-v4-pro"
        self.pro_default = "deepseek-v4-pro"

    # ── 路由规则测试 ──

    def test_flash_tasks_route_to_pro(self):
        """简单任务 → pro（统一模型后 flash 路由同 pro）"""
        for task in FLASH_TASKS:
            model, reasoning = self.router.route(task)
            self.assertEqual(
                model, self.flash_default,
                f"{task}: expected flash, got {model}"
            )
            self.assertFalse(reasoning, f"{task}: expected no reasoning")

    def test_pro_tasks_route_to_pro(self):
        """复杂任务 → pro"""
        for task in PRO_TASKS:
            model, reasoning = self.router.route(task)
            self.assertEqual(
                model, self.pro_default,
                f"{task}: expected pro, got {model}"
            )
            self.assertFalse(reasoning, f"{task}: expected no reasoning")

    def test_pro_reasoning_tasks_route_to_pro_with_reasoning(self):
        """疑难任务 → pro + reasoning"""
        for task in PRO_REASONING_TASKS:
            model, reasoning = self.router.route(task)
            self.assertEqual(
                model, self.pro_default,
                f"{task}: expected pro, got {model}"
            )
            self.assertTrue(reasoning, f"{task}: expected reasoning=True")

    def test_unknown_task_defaults_to_pro(self):
        """未知任务 → pro（默认安全）"""
        model, reasoning = self.router.route("unknown_task_type")
        self.assertEqual(model, self.flash_default)
        self.assertFalse(reasoning)

    # ── Payload 构建测试 ──

    def test_build_payload_flash_tasks(self):
        """flash 任务 payload 不含 reasoning"""
        payload = self.router.build_payload("test prompt", "file_search")
        self.assertEqual(payload["model"], self.flash_default)
        self.assertNotIn("reasoning_effort", payload)

    def test_build_payload_pro(self):
        """pro 任务 payload 不含 reasoning"""
        payload = self.router.build_payload("test prompt", "architecture_judge")
        self.assertEqual(payload["model"], self.pro_default)
        self.assertNotIn("reasoning_effort", payload)

    def test_build_payload_pro_reasoning(self):
        """pro+reasoning 任务 payload 含 reasoning 参数"""
        payload = self.router.build_payload("test prompt", "m2_syndrome_error")
        self.assertEqual(payload["model"], self.pro_default)
        self.assertEqual(payload.get("reasoning_effort"), "high")
        self.assertEqual(payload.get("thinking", {}).get("type"), "enabled")

    def test_build_payload_with_system_prompt(self):
        """system_prompt 正确传递"""
        payload = self.router.build_payload(
            "test prompt", "single_file_edit",
            system_prompt="你是助手。",
        )
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][0]["content"], "你是助手。")

    # ── get_model_info 测试 ──

    def test_get_model_info(self):
        """get_model_info 返回正确路由信息"""
        info = self.router.get_model_info("m2_syndrome_error")
        self.assertEqual(info["model"], self.pro_default)
        self.assertEqual(info["complexity"], "pro+reasoning")
        self.assertTrue(info["use_reasoning"])

        info = self.router.get_model_info("run_test")
        self.assertEqual(info["model"], self.flash_default)
        self.assertEqual(info["complexity"], "pro")
        self.assertFalse(info["use_reasoning"])

        info = self.router.get_model_info("architecture_judge")
        self.assertEqual(info["model"], self.pro_default)
        self.assertEqual(info["complexity"], "pro")
        self.assertFalse(info["use_reasoning"])

    # ── 验收路由表完整性 ──

    def test_no_overlap_between_task_sets(self):
        """三个任务集互不重叠"""
        all_tasks = FLASH_TASKS | PRO_TASKS | PRO_REASONING_TASKS
        total = len(FLASH_TASKS) + len(PRO_TASKS) + len(PRO_REASONING_TASKS)
        self.assertEqual(len(all_tasks), total)

    # ── Flash 禁用验收（P0）──

    def test_no_task_returns_flash_model(self):
        """任何 task_type 都不得返回 deepseek-v4-flash"""
        all_types = FLASH_TASKS | PRO_TASKS | PRO_REASONING_TASKS | {"unknown", "default", "summary", "quick", "cheap"}
        for task in all_types:
            model, _ = self.router.route(task)
            self.assertNotIn(
                "flash", model.lower(),
                f"task={task!r}: model={model!r} 不应包含 'flash'",
            )
            self.assertEqual(model, "deepseek-v4-pro",
                             f"task={task!r}: 期望 deepseek-v4-pro, 实际 {model}")

    def test_env_var_cannot_inject_flash(self):
        """即使 DEEPSEEK_FLASH_MODEL / DEEPSEEK_MODEL 设了 flash，路由结果也不得使用 flash"""
        with patch.dict(os.environ, {
            "DEEPSEEK_FLASH_MODEL": "deepseek-v4-flash",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        }):
            router = DeepSeekModelRouter()
        all_types = FLASH_TASKS | PRO_TASKS | PRO_REASONING_TASKS | {"unknown"}
        for task in all_types:
            model, _ = router.route(task)
            self.assertNotIn(
                "flash", model.lower(),
                f"task={task!r}: 环境变量注入 flash 后 model={model!r} 仍不应包含 'flash'",
            )


if __name__ == "__main__":
    unittest.main()
