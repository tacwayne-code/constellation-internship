#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PLM3 — 统一图文档案管理系统（新/旧图纸双档案）
运行方式: python run.py
访问地址: http://localhost:5002
新图号规范: XX-XX-XX-XX (机型-唯一识别编号-模块-顺序)
旧图号: 保留原图号（历史图纸）
默认管理员账号: admin
首次启动会生成随机管理员密码并打印到控制台，请妥善保存。
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
    """初始化示例数据和默认管理员；非管理员账号固定密码 123456"""
    with app.app_context():
        import secrets
        DEFAULT_USER_PWD = '123456'  # 非管理员默认密码

        # 检查是否首次初始化
        if not User.query.filter_by(username='admin').first():
            admin_pwd = secrets.token_urlsafe(10)  # 管理员密码仅首次打印到控制台

            # 创建默认用户（生产环境请修改密码或删除此初始化逻辑）
            users = [
                User(username='admin', display_name='系统管理员', role='admin', department='IT部'),
                User(username='manager1', display_name='经理A', role='manager', department='技术部'),
                User(username='engineer1', display_name='工程师A', role='user', department='研发部'),
            ]
            for u in users[1:]:
                u.set_password(DEFAULT_USER_PWD)
            users[0].set_password(admin_pwd)  # 管理员随机密码
            for u in users:
                db.session.add(u)
            db.session.commit()
            print(f'  初始化完成: {len(users)} 个用户')
            print(f'  【管理员密码】admin / {admin_pwd}（仅本次启动可见，请立即妥善保存）')
            print(f'  【普通账号】manager1 / engineer1  默认密码: {DEFAULT_USER_PWD}')

        else:
            # 已初始化：强制将所有非管理员账号密码重置为 123456（用户要求）
            non_admin = User.query.filter(User.role != 'admin').all()
            reset_count = 0
            for u in non_admin:
                u.set_password(DEFAULT_USER_PWD)
                reset_count += 1
            if reset_count:
                db.session.commit()
                print(f'  已重置 {reset_count} 个非管理员账号密码为 {DEFAULT_USER_PWD}')

        # 创建文档分类（已存在则跳过）
        if DocumentCategory.query.count() == 0:
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
            print(f'  初始化完成: {len(cats)} 个文档分类')


if __name__ == '__main__':
    print('=' * 50)
    print('  PLM3 — 统一图文档案管理系统')
    print('  正在启动...')
    init_sample_data()
    print(f'  访问地址: http://localhost:5002')
    print('  管理员账号: admin（密码仅首次初始化时显示，请查看启动输出）')
    print(f'  新图号规范: XX-XX-XX-XX (机型-识别号-模块-顺序)')
    print('=' * 50)
    import logging
    logging.basicConfig(filename='plm3.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    # 默认仅绑定 localhost；设置 PLM_HOST=0.0.0.0 可对外暴露（仅安全内网使用）
    host = os.environ.get('PLM_HOST', '127.0.0.1')
    port = int(os.environ.get('PLM_PORT', '5002'))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
