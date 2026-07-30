import os
import unittest
from unittest.mock import Mock, patch

import bili_task
import netease_task


class FakeResponse:
    def __init__(self, payload=None, *, text="", status_code=200, json_error=False):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.ok = 200 <= status_code < 400
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class TaskHelpersTest(unittest.TestCase):
    def test_bili_non_json_response_is_diagnosed(self):
        messages = []
        response = FakeResponse(text="<html>WAF blocked</html>", json_error=True)
        with patch.object(bili_task.requests, "request", return_value=response):
            result = bili_task.request_json(
                "GET", "https://example.test/nav", messages.append
            )

        self.assertIsNone(result)
        self.assertIn("接口返回非 JSON", messages[0])
        self.assertIn("WAF blocked", messages[0])
        self.assertNotIn("Expecting value: line 1 column 1", messages[0])

    def test_bili_network_error_is_diagnosed(self):
        messages = []
        with patch.object(
            bili_task.requests,
            "request",
            side_effect=bili_task.requests.ConnectionError("connection refused"),
        ):
            result = bili_task.request_json(
                "GET", "https://example.test/nav", messages.append
            )

        self.assertIsNone(result)
        self.assertIn("请求失败", messages[0])
        self.assertIn("ConnectionError", messages[0])

    def test_bili_experience_supports_level_info_field(self):
        self.assertEqual(
            bili_task.get_bili_experience({"level_info": {"current_exp": 3210}}),
            3210,
        )

    def test_bili_task_uses_safe_request_path_for_all_endpoints(self):
        responses = [
            FakeResponse({"code": 0, "data": {"uname": "tester", "level_info": {"current_level": 5}, "level_exp": {"current_exp": 100}, "money": 0}}),
            FakeResponse({"code": 0, "data": {"watch": False, "share": False, "coins": 0}}),
            FakeResponse({"code": 0, "data": {"list": [{"bvid": "BV1", "aid": 1, "title": "test video"}]}}),
            FakeResponse({"code": 0}),
            FakeResponse({"code": 0}),
            FakeResponse({"code": 0, "data": {"level_exp": {"current_exp": 120}}}),
        ]
        with patch.object(bili_task.requests, "request", side_effect=responses), patch.object(
            bili_task, "setup_logger", return_value=Mock()
        ), patch.object(bili_task.time, "sleep"):
            result = bili_task.do_bili_task("DedeUserID=1; bili_jct=csrf;", 1)

        self.assertIn("观看任务：✅ 已完成", result)
        self.assertIn("分享任务：✅ 已完成", result)
        self.assertIn("硬币余额：0枚", result)
        self.assertIn("经验值：执行前 100 → 执行后 120，本次增加 +20", result)

    def test_netease_zero_values_are_not_reported_as_unknown(self):
        payload = {"data": {"point": 0, "score": 0}}
        self.assertEqual(netease_task.first_value(payload, ("data", "point")), 0)
        self.assertEqual(netease_task.first_value(payload, ("data", "score")), 0)

    def test_netease_full_cookie_with_ampersand_is_one_account(self):
        cookie = "P_INFO=user@example.com|x&y; __csrf=csrf; MUSIC_U=music;"
        with patch.dict(os.environ, {"NETEASE_COOKIE": cookie}, clear=False):
            self.assertEqual(netease_task.get_cookies(), [cookie])

    def test_netease_login_failure_is_kept_in_account_log(self):
        logger = Mock()
        with patch.object(
            netease_task.requests,
            "post",
            side_effect=netease_task.requests.ConnectionError("node unavailable"),
        ), patch.object(netease_task, "setup_logger", return_value=logger):
            result = netease_task.do_netease_task("MUSIC_U=test; __csrf=test;", 1)

        self.assertIn("请求失败 [/login/status]", result)
        self.assertIn("Cookie已失效或格式不正确", result)
        self.assertTrue(logger.info.called)

    def test_netease_task_keeps_running_when_points_are_zero(self):
        responses = [
            FakeResponse({"code": 200, "data": {"profile": {"nickname": "tester", "vipType": 1}}}),
            FakeResponse({"code": 200, "point": 0}),
            FakeResponse({"code": 200}),
            FakeResponse({"code": 200, "data": {"point": 0}}),
            FakeResponse({"code": 200, "data": [{"taskStatus": 1, "userTaskId": 99, "depositCode": 0, "reward": 5}]}),
            FakeResponse({"code": 200, "data": {"point": 5}}),
            FakeResponse({"code": 200, "data": {"unGetIds": []}}),
            FakeResponse({"code": 200, "data": {"growthPoint": 0}}),
            FakeResponse({"code": 200, "data": {"level": 7, "nowPlayCount": 0}}),
        ]
        messages = []

        with patch.object(netease_task.requests, "post", side_effect=responses) as mock_post, patch.object(
            netease_task, "setup_logger", return_value=Mock()
        ), patch.object(netease_task, "get_300_random_songs", return_value=[]):
            result = netease_task.do_netease_task("MUSIC_U=test; __csrf=test;", 1)
            messages.extend(result.splitlines())

        joined = "\n".join(messages)
        self.assertIn("手机端签到：✅ 已检查，本次新增 0 经验", joined)
        self.assertIn("云贝签到：✅ 已检查，本次新增 0 云贝", joined)
        self.assertIn("VIP成长值：✅ 已检查，本次没有新增成长值", joined)
        self.assertIn("云贝任务：✅ 成功领取 1 个任务奖励 (+5 云贝)", joined)
        self.assertIn("获取歌单失败", joined)
        self.assertIn("当前等级：Lv.7", joined)
        urls = [call.args[0] for call in mock_post.call_args_list]
        self.assertTrue(any(url.endswith("/yunbei/task/finish") for url in urls))
        self.assertTrue(any(url.endswith("/vip/growthpoint/getall") for url in urls))


if __name__ == "__main__":
    unittest.main()
