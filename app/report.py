from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import load_config
from app.inventory import Inventory, format_counts, start_of_local_day


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consulta el inventario de visitas")
    parser.add_argument("--config", default="config.yaml", help="Ruta del YAML de cámaras")
    parser.add_argument("--desde", help="Solo visitas desde esta fecha, inclusive (AAAA-MM-DD)")
    args = parser.parse_args(argv)
    config_path = Path(args.config)
    if not config_path.exists():
        print(
            f"No existe {config_path}. Copia config.example.yaml a config.yaml.",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(config_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not config.database.exists():
        print("Todavía no hay visitas.")
        return 0

    inventory = Inventory(config.database)
    try:
        if args.desde:
            if len(args.desde) != 10 or args.desde[4] != "-" or args.desde[7] != "-":
                print("--desde tiene que tener la forma AAAA-MM-DD.", file=sys.stderr)
                return 2
            print(f"Visitas desde {args.desde}")
            print(format_counts(inventory.counts(since=f"{args.desde}T00:00:00")))
            return 0
        print("Visitas de hoy")
        print(format_counts(inventory.counts(since=start_of_local_day())))
        print()
        print("Visitas en total")
        print(format_counts(inventory.counts()))
    finally:
        inventory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
