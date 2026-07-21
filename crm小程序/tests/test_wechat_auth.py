import tempfile
import unittest
from pathlib import Path

from server.wechat_auth import AuthError, AuthManager


class FakeWeChatClient:
    def exchange_login_code(self, code):
        return {"openid": f"openid-{code}"}

    def phone_by_code(self, code):
        return {
            "phone-code": "13812345678",
            "second-phone-code": "13912345678",
        }.get(code, "13812345678")


class WeChatEmployeeApplicationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="crm-wechat-auth-")
        self.manager = AuthManager(
            Path(self.temporary.name) / "employees.json",
            mode="WECHAT",
            cookie_secure=False,
            wechat_client=FakeWeChatClient(),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_first_account_becomes_manager_then_approves_later_employee(self):
        login = self.manager.wechat_login("first")
        self.assertEqual(login["status"], "PHONE_BINDING_REQUIRED")

        phone = self.manager.bind_phone(login["bindToken"], "phone-code")
        self.assertEqual(phone["status"], "PROFILE_REQUIRED")
        self.assertEqual(phone["maskedPhone"], "138****5678")
        self.assertTrue(phone["isFirstAccount"])

        first_account = self.manager.submit_application(
            phone["applicationToken"], "张经理", "销售人员"
        )
        self.assertEqual(first_account["status"], "AUTHORIZED")
        manager_employee = self.manager.employees.find_phone("13812345678")
        self.assertEqual(manager_employee["status"], "ACTIVE")
        self.assertEqual(manager_employee["role"], "销售经理")
        self.assertTrue(manager_employee["active"])
        manager_session, _ = self.manager.handoff(first_account["ticket"])
        self.assertEqual(manager_session["user"]["role"], "销售经理")

        second_login = self.manager.wechat_login("second")
        second_phone = self.manager.bind_phone(
            second_login["bindToken"], "second-phone-code"
        )
        self.assertFalse(second_phone["isFirstAccount"])
        application = self.manager.submit_application(
            second_phone["applicationToken"], "李销售", "销售人员"
        )
        self.assertEqual(application["status"], "APPROVAL_PENDING")
        pending_employee = self.manager.employees.find_phone("13912345678")
        self.assertEqual(pending_employee["status"], "PENDING")
        self.assertFalse(pending_employee["active"])

        approved = self.manager.employees.review_application(
            "13912345678",
            "APPROVED",
            "销售人员",
            "13812345678",
            "确认是公司销售",
        )
        self.assertTrue(approved["active"])
        self.assertEqual(approved["role"], "销售人员")

        authorized = self.manager.wechat_login("second")
        self.assertEqual(authorized["status"], "AUTHORIZED")
        session, _ = self.manager.handoff(authorized["ticket"])
        self.assertEqual(session["user"]["id"], "13912345678")
        self.assertEqual(session["user"]["phone"], "13912345678")
        self.assertEqual(session["user"]["role"], "销售人员")

    def test_one_manager_and_ten_sales_limits_are_enforced(self):
        self.manager.employees.upsert(
            {
                "id": "13900000000",
                "name": "初始经理",
                "phone": "13900000000",
                "role": "销售经理",
                "active": True,
            }
        )
        manager_application = self.manager.employees.create_application(
            "13900000001", "openid-manager-2", "第二经理", "销售经理"
        )
        with self.assertRaises(AuthError) as manager_error:
            self.manager.employees.review_application(
                manager_application["phone"],
                "APPROVED",
                "销售经理",
                "13900000000",
            )
        self.assertEqual(manager_error.exception.code, "MANAGER_LIMIT_REACHED")

        for index in range(10):
            phone = f"1380000{index:04d}"
            self.manager.employees.upsert(
                {
                    "id": phone,
                    "name": f"销售{index + 1}",
                    "phone": phone,
                    "role": "销售人员",
                    "active": True,
                }
            )
        sales_application = self.manager.employees.create_application(
            "13899999999", "openid-sales-11", "第十一名销售", "销售人员"
        )
        with self.assertRaises(AuthError) as sales_error:
            self.manager.employees.review_application(
                sales_application["phone"],
                "APPROVED",
                "销售人员",
                "13900000000",
            )
        self.assertEqual(sales_error.exception.code, "SALES_LIMIT_REACHED")


if __name__ == "__main__":
    unittest.main()
