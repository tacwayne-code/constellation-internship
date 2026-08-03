#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PLM 产品生命周期管理系统 - 启动脚本
运行方式: python run.py
访问地址: http://localhost:5000
默认管理员: admin / admin123
首次启动会自动创建示例用户，密码随机生成并打印到控制台。
"""
import os, sys

# 确保在项目根目录
basedir = os.path.dirname(os.path.abspath(__file__))
os.chdir(basedir)
sys.path.insert(0, basedir)

from app import create_app, db
from app.models import User, DocumentCategory

app = create_app()


def init_sample_data():
    """初始化示例数据和默认管理员"""
    with app.app_context():
        if User.query.filter_by(username='admin').first():
            return  # already initialized

        # 为非管理员用户生成随机密码
        import secrets
        user_pwd = secrets.token_urlsafe(8)

        # 创建默认用户（生产环境请修改密码或删除此初始化逻辑）
        users = [
            User(username='admin', display_name='系统管理员', role='admin', department='IT部'),
            User(username='manager1', display_name='经理A', role='manager', department='技术部'),
            User(username='engineer1', display_name='工程师A', role='user', department='研发部'),
        ]
        for u in users:
            u.set_password(user_pwd)
        users[0].set_password('admin123')  # admin/admin123 — 生产环境务必修改
        for u in users:
            db.session.add(u)

        # 创建文档分类
        cats = [
            DocumentCategory(name='设计图纸'),
            DocumentCategory(name='技术规范'),
            DocumentCategory(name='工艺文件'),
            DocumentCategory(name='质量报告'),
            DocumentCategory(name='项目文档'),
            DocumentCategory(name='合同与协议'),
        ]
        for c in cats:
            db.session.add(c)

        db.session.commit()
        print(f'  初始化完成: {len(users)} 个用户, {len(cats)} 个文档分类')
        print(f'  管理员: admin / admin123')
        print(f'  其他用户密码: {user_pwd}')


if __name__ == '__main__':
    print('=' * 50)
    print('  PLM 产品生命周期管理系统')
    print('  正在启动...')
    init_sample_data()
    print(f'  访问地址: http://localhost:5000')
    print(f'  管理员账号: admin / admin123')
    print('=' * 50)
    import logging
    logging.basicConfig(filename='plm.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    # 默认仅绑定 localhost；设置 PLM_HOST=0.0.0.0 可对外暴露（仅安全内网使用）
    host = os.environ.get('PLM_HOST', '127.0.0.1')
    port = int(os.environ.get('PLM_PORT', '5000'))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
