"""PII 脱敏切面的单元测试：手机号 / 身份证 / 邮箱 / 银行卡。"""

from act_agent.middleware.pii import PiiMiddleware, redact_pii, redact_value


def test_redact_phone():
    assert redact_pii("电话13812345678联系") == "电话138****5678联系"


def test_redact_id_card():
    assert redact_pii("身份证110101199001011234") == "身份证110101********1234"


def test_redact_email():
    assert redact_pii("邮箱 zhangsan@example.com 收") == "邮箱 z***@example.com 收"


def test_redact_bank_card():
    assert redact_pii("卡号 6222020200112233") == "卡号 6222 **** **** 2233"


def test_redact_non_string_passthrough():
    assert redact_pii(None) is None
    assert redact_pii(123) == 123


def test_redact_value_recursive():
    payload = {
        "name": "张三",
        "phone": "13812345678",
        "items": [{"id": "110101199001011234"}],
    }
    out = redact_value(payload)
    assert out["name"] == "张三"  # 非敏感字段原样保留
    assert out["phone"] == "138****5678"
    assert out["items"][0]["id"] == "110101********1234"


def test_pii_middleware_redact_input_output():
    mw = PiiMiddleware()
    assert mw.redact_input("13812345678") == "138****5678"
    assert mw.redact_output("邮箱 a@b.com") == "邮箱 a***@b.com"
