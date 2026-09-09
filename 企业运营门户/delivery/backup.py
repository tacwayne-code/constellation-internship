"""Back up or restore into a NEW directory. Never overwrite an existing database."""
import argparse
import sqlite3
from pathlib import Path
from .db import Database

def restore(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if not source.is_file():raise ValueError('备份文件不存在')
    if destination.exists() and any(destination.iterdir()):raise ValueError('恢复目标必须是新的空目录')
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    try:
        if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份文件损坏')
        if src.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]!=1:raise ValueError('备份版本不支持')
        if src.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('备份关联数据不完整')
        destination.mkdir(parents=True,exist_ok=True)
        dst=sqlite3.connect(destination/'delivery.sqlite3')
        try:
            src.backup(dst);dst.execute('DELETE FROM sessions');dst.commit()
        finally:dst.close()
    finally:src.close()
    return destination

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['backup','restore']);p.add_argument('source');p.add_argument('destination');a=p.parse_args()
    result=Database(a.source).backup(a.destination) if a.action=='backup' else restore(a.source,a.destination)
    print(result)
