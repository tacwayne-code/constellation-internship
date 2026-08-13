# -*- coding: utf-8 -*-
"""v2 → v3 数据库迁移脚本（archive_type 档案分流）

背景
----
v3 的 `plm_document` 模型新增了 `archive_type` 字段（new=新图号档案 / old=旧图号档案），
但 SQLAlchemy 的 `db.create_all()` 只创建不存在的表，**不会给已存在的旧表加列**。
如果直接把 v2 的旧库（无 archive_type 列）交给 v3 代码跑，任何查询 Document 的地方
都会抛 `OperationalError: no such column: plm_document.archive_type`。

本脚本解决两个问题：
1. 给旧库补 `archive_type` 列（幂等，重复执行安全）
2. 按图号格式自动分类：四段八位 `XX-XX-XX-XX` → 'new'，其余 → 'old'
3. 可选：把 v2 库的文档数据复制进 v3 库（按 doc_no 去重）

用法
----
    # 仅对目标库补列 + 分类（原地修复旧库）
    python migrate_v2_to_v3.py --db plm_v2.db

    # 把 v2 文档数据迁入 v3 库（先复制再去重分类）
    python migrate_v2_to_v3.py --from plm_v2.db --to plm3.db

    # 查看帮助
    python migrate_v2_to_v3.py --help
"""
import argparse
import os
import re
import sqlite3
import sys

# 四段八位新图号格式：91-01-01-01
DRAWING_NO_RE = re.compile(r'^\d{2}-\d{2}-\d{2}-\d{2}$')

DOC_COLUMNS = [
    'id', 'doc_no', 'title', 'description', 'category_id', 'status',
    'version', 'file_name', 'file_path', 'file_size', 'name', 'tags',
    'author_id', 'created_at', 'updated_at', 'is_locked', 'locked_by',
]


def connect(db_path):
    if not os.path.isfile(db_path):
        sys.exit(f'错误：数据库文件不存在 {db_path}')
    con = sqlite3.connect(db_path, timeout=30)
    con.execute('PRAGMA busy_timeout=15000')
    return con


def table_columns(con, table):
    rows = con.execute(f'PRAGMA table_info({table})').fetchall()
    return {row[1] for row in rows}


def has_table(con, table):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def ensure_archive_column(con, table='plm_document'):
    """给旧表补 archive_type 列（幂等）。返回是否新加了列。"""
    if not has_table(con, table):
        print(f'  ℹ️  {table} 表不存在，跳过补列')
        return False
    cols = table_columns(con, table)
    if 'archive_type' in cols:
        print(f'  ✓ {table} 已有 archive_type 列，无需补列')
        return False
    con.execute(
        f"ALTER TABLE {table} ADD COLUMN archive_type VARCHAR(8) DEFAULT 'new'"
    )
    con.commit()
    print(f'  ✓ 已为 {table} 补加 archive_type 列（默认 new）')
    return True


def classify_documents(con, table='plm_document'):
    """按图号格式自动分类：四段八位 → new，其余 → old。幂等。"""
    if not has_table(con, table):
        print(f'  ℹ️  {table} 表不存在，跳过分类')
        return
    rows = con.execute(f'SELECT id, title FROM {table}').fetchall()
    updated = 0
    for doc_id, title in rows:
        archive = 'new' if title and DRAWING_NO_RE.match(title.strip()) else 'old'
        con.execute(
            f'UPDATE {table} SET archive_type=? WHERE id=? AND archive_type IS DISTINCT FROM ?',
            (archive, doc_id, archive),
        )
        updated += 1
    con.commit()
    print(f'  ✓ 已按图号格式分类 {updated} 条文档（new=四段八位，其余=old）')


def copy_documents(src_con, dst_con, table='plm_document'):
    """把源库文档复制到目标库（按 doc_no 去重，保留业务字段、重建 id）。"""
    if not has_table(src_con, table):
        print(f'  ℹ️  源库无 {table} 表，跳过复制')
        return 0
    # 目标库列集（可能缺 archive_type 时先补）
    ensure_archive_column(dst_con, table)
    src_cols = table_columns(src_con, table)
    dst_cols = table_columns(dst_con, table)
    common = [c for c in DOC_COLUMNS if c in src_cols and c in dst_cols]
    if 'archive_type' not in common:
        # 源库无 archive_type：复制时自动填 new，随后再统一分类
        common = [c for c in common if c != 'archive_type']

    existing = {
        row[0] for row in dst_con.execute(f'SELECT doc_no FROM {table} WHERE doc_no IS NOT NULL')
    }
    src_rows = src_con.execute(
        f'SELECT {", ".join(common)} FROM {table}'
    ).fetchall()
    copied = skipped = 0
    for row in src_rows:
        rec = dict(zip(common, row))
        doc_no = rec.get('doc_no')
        if doc_no and doc_no in existing:
            skipped += 1
            continue
        cols = [c for c in common if c != 'id']  # 让目标库自增 id
        vals = [rec[c] for c in cols]
        if 'archive_type' not in cols:
            # 源无该列 → 补默认 new，稍后统一分类
            cols.append('archive_type')
            vals.append('new')
        placeholders = ', '.join('?' * len(cols))
        dst_con.execute(
            f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({placeholders})',
            vals,
        )
        if doc_no:
            existing.add(doc_no)
        copied += 1
    dst_con.commit()
    print(f'  ✓ 复制文档 {copied} 条（按 doc_no 去重跳过 {skipped} 条）')
    return copied


def main():
    parser = argparse.ArgumentParser(description='v2 → v3 PLM 数据库迁移')
    parser.add_argument('--db', help='目标库路径（原地补列+分类）')
    parser.add_argument('--from', dest='src', help='源库路径（v2，如 plm_v2.db）')
    parser.add_argument('--to', dest='dst', help='目标库路径（v3，如 plm3.db）')
    args = parser.parse_args()

    if args.db:
        print(f'== 原地修复库: {args.db} ==')
        con = connect(args.db)
        ensure_archive_column(con)
        classify_documents(con)
        con.close()
        print('完成。')
        return

    if args.src and args.dst:
        print(f'== 数据迁移: {args.src} → {args.dst} ==')
        src_con = connect(args.src)
        dst_con = connect(args.dst)
        ensure_archive_column(dst_con)
        n = copy_documents(src_con, dst_con)
        classify_documents(dst_con)
        src_con.close()
        dst_con.close()
        print(f'完成，共复制 {n} 条文档。')
        return

    parser.print_help()


if __name__ == '__main__':
    main()
