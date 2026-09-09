from __future__ import annotations

import argparse
import json

from .database import Base, SessionLocal, engine
from .migration import migrate_all


def main() -> None:
    parser = argparse.ArgumentParser(description="统一业务平台迁移工具")
    parser.add_argument("command", choices=["migrate-legacy"])
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if args.command == "migrate-legacy":
            print(json.dumps(migrate_all(db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

