#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PLM系统2 — 产品生命周期管理系统 v2 - 启动脚本
运行方式: python run.py
访问地址: http://localhost:5001
图号规范: XX-XX-XX-XX (机型-唯一识别编号-模块-顺序)
配件编码: XX-XX-XX-XX (大类-产品族-部件-流水)
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
        users[0].set_password(secrets.token_urlsafe(10))  # 管理员随机密码，仅首次打印
        for u in users:
            db.session.add(u)

        # 创建文档分类（对齐老 PLM 体系）
        cats = [
            DocumentCategory(name='设计图纸'),
            DocumentCategory(name='技术规范'),
            DocumentCategory(name='工艺文件'),
            DocumentCategory(name='质量报告'),
            DocumentCategory(name='项目文档'),
            DocumentCategory(name='合同与协议'),
            DocumentCategory(name='五金件加工图纸'),
            DocumentCategory(name='钣金加工图纸'),
            DocumentCategory(name='标准件图纸'),
            DocumentCategory(name='BOM清单'),
        ]
        for c in cats:
            db.session.add(c)

        db.session.commit()
        print(f'  初始化完成: {len(users)} 个用户, {len(cats)} 个文档分类')
        print('  管理员: admin / <控制台打印的密码>')  # 密码仅初始化时打印一次，请妥善保存
        pass  # 密码已在初始化时打印，此处不再重复


if __name__ == '__main__':
    print('=' * 50)
    print('  PLM系统2 — 产品生命周期管理系统')
    print('  正在启动...')
    init_sample_data()
    print(f'  访问地址: http://localhost:5001')
    print('  管理员账号: admin（密码仅首次初始化时显示，请查看启动输出）')
    print(f'  图号规范: XX-XX-XX-XX (机型-识别号-模块-顺序)')
    print(f'  配件编码: XX-XX-XX-XX (大类-产品族-部件-流水)')
    print('=' * 50)
    import logging
    logging.basicConfig(filename='plm_v2.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    # 默认仅绑定 localhost；设置 PLM_HOST=0.0.0.0 可对外暴露（仅安全内网使用）
    host = os.environ.get('PLM_HOST', '127.0.0.1')
    port = int(os.environ.get('PLM_PORT', '5001'))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
