import os
import uuid

basedir = os.path.abspath(os.path.dirname(__file__))
_key_file = os.path.join(basedir, '.secret_key')


def _get_or_create_secret():
    """SECRET_KEY 持久化：环境变量 > 本地文件 > 随机生成并写入文件"""
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    if os.path.exists(_key_file):
        return open(_key_file).read().strip()
    key = 'plm-' + uuid.uuid4().hex
    with open(_key_file, 'w') as f:
        f.write(key)
    return key


class Config:
    SECRET_KEY = _get_or_create_secret()
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'plm3.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(basedir, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    TEMPLATES_AUTO_RELOAD = True  # 模板修改后自动重新加载
