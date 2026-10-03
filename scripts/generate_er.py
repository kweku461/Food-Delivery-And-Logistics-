"""Generate docs/er_diagram.mmd (Mermaid) straight from the SQLAlchemy metadata, so it never drifts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: F401
from app.database import Base



def type_name(col) -> str:
    t = str(col.type).split("(")[0].replace(" ", "_").lower()
    return {"varchar": "string", "numeric": "decimal", "datetime": "datetime"}.get(t, t)


def build() -> str:
    lines = ["erDiagram"]
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        lines.append(f"    {table.name} {{")
        for col in table.columns:
            tags = []
            if col.primary_key:
                tags.append("PK")
            if col.foreign_keys:
                tags.append("FK")
            if col.unique and not col.primary_key:
                tags.append("UK")
            lines.append(f"        {type_name(col)} {col.name}" + (f" {','.join(tags)}" if tags else ""))
        lines.append("    }")
    edges = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                parent = fk.column.table.name
                unique = col.unique or col.primary_key
                left = "||" if not col.nullable else "|o"
                right = "o|" if unique else "o{"
                edges.append(f'    {parent} {left}--{right} {table.name} : "{col.name}"')
    lines += sorted(edges)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "docs" / "er_diagram.mmd"
    out.write_text(build())
    print(f"wrote {out} ({len(Base.metadata.tables)} entities)")
