import queue
import unittest
from unittest.mock import patch

from totod_publisher import (
    PublisherWorker,
    ZhihuRiskControlError,
    zhihu_risk_control_reason,
)


class PublisherWorkerTests(unittest.TestCase):
    def setUp(self):
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.worker = PublisherWorker(self.commands, self.events)
        self.worker.server = "https://totod.example"
        self.worker.token = "device-token"

    def test_load_accounts_retains_worker_snapshot(self):
        accounts = [
            {
                "id": "5c3c1549-0000-0000-0000-000000000000",
                "display_name": "qwg2",
                "answer_publish_mode": "local",
            }
        ]

        with patch("totod_publisher.api_request", return_value=accounts):
            self.assertTrue(self.worker.load_accounts())

        self.assertEqual(self.worker.accounts, accounts)

    def test_start_refreshes_accounts_and_enables_polling(self):
        account_id = "5c3c1549-0000-0000-0000-000000000000"
        accounts = [
            {
                "id": account_id,
                "display_name": "qwg2",
                "answer_publish_mode": "local",
            }
        ]
        self.commands.put(("start", account_id))

        with (
            patch("totod_publisher.api_request", return_value=accounts),
            patch.object(self.worker, "ensure_context", return_value=object()),
            patch.object(self.worker, "is_logged_in", return_value=True),
        ):
            self.worker.handle_commands()

        self.assertTrue(self.worker.running)
        self.assertEqual(self.worker.account_id, account_id)
        events = []
        while not self.events.empty():
            events.append(self.events.get_nowait())
        self.assertIn(("status", "运行中：正在等待 qwg2 的发布任务"), events)
        self.assertTrue(
            any(kind == "log" and "开始监听账号：qwg2" in value for kind, value in events)
        )

    def test_login_auto_starts_listener_after_session_is_detected(self):
        account_id = "5c3c1549-0000-0000-0000-000000000000"
        accounts = [
            {
                "id": account_id,
                "display_name": "qwg2",
                "answer_publish_mode": "local",
            }
        ]
        context = object()
        self.worker.contexts[account_id] = context
        self.worker.auto_start_account_id = account_id

        with (
            patch("totod_publisher.api_request", return_value=accounts),
            patch.object(self.worker, "is_logged_in", return_value=True),
        ):
            self.worker.try_auto_start()

        self.assertTrue(self.worker.running)
        self.assertEqual(self.worker.account_id, account_id)
        self.assertEqual(self.worker.auto_start_account_id, "")
        events = []
        while not self.events.empty():
            events.append(self.events.get_nowait())
        self.assertIn(("listening", True), events)
        self.assertTrue(
            any(kind == "log" and "自动开始监听账号：qwg2" in value for kind, value in events)
        )

    def test_risk_control_40362_stops_listener(self):
        task = {
            "id": "task-id",
            "question_title": "测试问题",
        }
        self.worker.running = True
        self.worker.account_id = "account-id"

        with (
            patch("totod_publisher.api_request", side_effect=[task, None]),
            patch.object(
                self.worker,
                "publish",
                side_effect=ZhihuRiskControlError("知乎风控 40362：测试"),
            ),
        ):
            self.worker.poll_once()

        self.assertFalse(self.worker.running)
        self.assertEqual(self.worker.account_id, "")
        events = []
        while not self.events.empty():
            events.append(self.events.get_nowait())
        self.assertIn(("listening", False), events)
        self.assertTrue(
            any(kind == "status" and "40362" in value for kind, value in events)
        )


class RiskControlDetectionTests(unittest.TestCase):
    def test_recognizes_zhihu_40362_json_page(self):
        body = (
            '{"error":{"message":"您当前请求存在异常，暂时限制本次访问",'
            '"code":40362}}'
        )

        reason = zhihu_risk_control_reason(body)

        self.assertIsNotNone(reason)
        self.assertIn("40362", reason)

    def test_allows_normal_question_page(self):
        self.assertIsNone(zhihu_risk_control_reason("问题标题 写回答"))


if __name__ == "__main__":
    unittest.main()
