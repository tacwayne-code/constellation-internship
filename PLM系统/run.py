#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PLM 产品生命周期管理系统 - 启动脚本
运行方式: python run.py
访问地址: http://localhost:5000
默认管理员: admin / admin123
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

        # 创建默认用户
        users = [
            User(username='admin', display_name='系统管理员', role='admin', department='IT部'),
            User(username='manager1', display_name='王经理', role='manager', department='技术部'),
            User(username='manager2', display_name='李经理', role='manager', department='质量部'),
            User(username='engineer1', display_name='张工', role='user', department='研发部'),
            User(username='engineer2', display_name='陈工', role='user', department='工艺部'),
            User(username='viewer1', display_name='赵工', role='viewer', department='生产部'),
        ]
        for u in users:
            u.set_password('123456')
        users[0].set_password('admin123')  # admin/admin123
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


if __name__ == '__main__':
    print('=' * 50)
    print('  PLM 产品生命周期管理系统')
    print('  正在启动...')
    init_sample_data()
    print(f'  访问地址: http://localhost:5000')
    print(f'  管理员账号: admin / admin123')
    print('=' * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
