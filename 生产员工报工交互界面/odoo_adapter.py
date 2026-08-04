"""
Odoo 适配器 - 统一接口
根据 ODOO_MOCK_MODE 环境变量选择真实或 Mock 客户端
"""
import os
import logging

logger = logging.getLogger("odoo_adapter")


def create_odoo_client(real_client_cls=None, real_error_cls=None):
    """
    工厂函数：根据环境变量创建 Odoo 客户端
    
    ODOO_MOCK_MODE=true → FakeOdooClient (Mock模式)
    ODOO_MOCK_MODE=false/未设置 → OdooClient (真实模式)
    
    Args:
        real_client_cls: 可选的真实客户端类，避免 server.py 直接运行时重复导入自身。
        real_error_cls: 可选的真实客户端异常类。

    Returns:
        (client, mode, error_class)
    """
    mock_mode = os.getenv("ODOO_MOCK_MODE", "false").lower() == "true"

    if mock_mode:
        logger.warning("ODOO MOCK MODE ENABLED - NO REAL ODOO WRITES")
        from fake_odoo_client import FakeOdooClient, FakeOdooError
        return FakeOdooClient(), "mock", FakeOdooError
    else:
        if real_client_cls is None or real_error_cls is None:
            from server import OdooClient, OdooError
            real_client_cls = real_client_cls or OdooClient
            real_error_cls = real_error_cls or OdooError
        return real_client_cls(), "real", real_error_cls


def get_mode():
    """获取当前运行模式"""
    return "mock" if os.getenv("ODOO_MOCK_MODE", "false").lower() == "true" else "real"


def is_mock_mode():
    """判断是否为 Mock 模式"""
    return os.getenv("ODOO_MOCK_MODE", "false").lower() == "true"
