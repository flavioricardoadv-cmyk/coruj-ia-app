from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from app.motor.models import SessionLocal, init_db
from app.motor.router import _criar_documento_motor


SUPPORTED_EXTENSIONS = {".rtf", ".txt", ".pdf", ".docx", ".xls", ".xlsx"}
ZIP_EXTENSIONS = {".zip"}
DEFAULT_SOURCE = Path(r"C:\Users\flavi\OneDrive\Área de Trabalho\banco de manifestações")
DEFAULT_REPORT = Path("modelo_db_export") / "motor_import_report.jsonl"
DEFAULT_EXCLUDE_PARTS = {
    "__pycache__",
    ".git",
    "assets",
    "public",
    "static",
    "node_modules",
    "coruj_ia_projeto",
    "coruj_ia_site_kit",
    "mp_assistente_corujia_visual_refinado",
}
DEFAULT_EXCLUDE_FILE_HINTS = {
    "requirements",
    "readme",
    "leia_me",
    "conceito_coruj",
    "docker-compose",
}


def _normalized(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")


def should_skip_path(path: Path, root: Path, exclude_projects: bool = True) -> bool:
    if not exclude_projects:
        return False
    relative_parts = [_normalized(part) for part in path.relative_to(root).parts]
    if any(part in DEFAULT_EXCLUDE_PARTS for part in relative_parts):
        return True
    name = _normalized(path.name)
    return any(hint in name for hint in DEFAULT_EXCLUDE_FILE_HINTS)


def iter_source_files(root: Path, include_zips: bool = True, extensions: set[str] | None = None, exclude_projects: bool = True):
    extensions = extensions or SUPPORTED_EXTENSIONS
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip_path(path, root, exclude_projects=exclude_projects):
            continue
        suffix = path.suffix.lower()
        if suffix in extensions:
            yield {"kind": "file", "path": path, "name": str(path.relative_to(root))}
        elif include_zips and suffix in ZIP_EXTENSIONS:
            yield {"kind": "zip", "path": path, "name": str(path.relative_to(root))}


def iter_zip_members(path: Path, extensions: set[str] | None = None, exclude_projects: bool = True):
    extensions = extensions or SUPPORTED_EXTENSIONS
    try:
        with ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                pseudo = Path(info.filename)
                if exclude_projects:
                    parts = [_normalized(part) for part in pseudo.parts]
                    name = _normalized(pseudo.name)
                    if any(part in DEFAULT_EXCLUDE_PARTS for part in parts):
                        continue
                    if any(hint in name for hint in DEFAULT_EXCLUDE_FILE_HINTS):
                        continue
                suffix = Path(info.filename).suffix.lower()
                if suffix not in extensions:
                    continue
                yield info.filename, archive.read(info)
    except BadZipFile:
        yield None, None


def write_report(report_path: Path, row: dict):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def import_folder(
    root: Path,
    categoria: str,
    dry_run: bool,
    limit: int | None,
    include_zips: bool,
    extensions: set[str],
    exclude_projects: bool,
    report_path: Path,
) -> dict:
    init_db()
    root = root.resolve()
    if not root.exists():
        raise SystemExit(f"Pasta nao encontrada: {root}")

    stats = {
        "root": str(root),
        "dry_run_mode": dry_run,
        "seen": 0,
        "enfileirado": 0,
        "duplicata": 0,
        "ignorado": 0,
        "erro": 0,
        "zip": 0,
        "dry_run": 0,
        "started_at": datetime.utcnow().isoformat(),
    }
    db = SessionLocal()
    try:
        for entry in iter_source_files(root, include_zips=include_zips, extensions=extensions, exclude_projects=exclude_projects):
            if limit is not None and stats["seen"] >= limit:
                break
            path = entry["path"]
            if entry["kind"] == "zip":
                stats["zip"] += 1
                for member_name, member_bytes in iter_zip_members(path, extensions=extensions, exclude_projects=exclude_projects):
                    if limit is not None and stats["seen"] >= limit:
                        break
                    stats["seen"] += 1
                    if member_name is None:
                        stats["erro"] += 1
                        write_report(report_path, {"status": "erro", "arquivo": str(path), "motivo": "zip invalido"})
                        continue
                    logical_name = f"{entry['name']}::{member_name}"
                    if dry_run:
                        result = {"status": "dry_run", "arquivo": logical_name}
                    else:
                        try:
                            result = _criar_documento_motor(db, logical_name, member_bytes, categoria)
                            db.commit()
                        except Exception as exc:
                            db.rollback()
                            result = {"status": "erro", "arquivo": logical_name, "motivo": str(exc)}
                    stats[result.get("status", "erro")] = stats.get(result.get("status", "erro"), 0) + 1
                    write_report(report_path, {"source": str(path), **result})
                continue

            stats["seen"] += 1
            if dry_run:
                result = {"status": "dry_run", "arquivo": entry["name"], "bytes": path.stat().st_size}
            else:
                try:
                    result = _criar_documento_motor(db, entry["name"], path.read_bytes(), categoria)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    result = {"status": "erro", "arquivo": entry["name"], "motivo": str(exc)}
            stats[result.get("status", "erro")] = stats.get(result.get("status", "erro"), 0) + 1
            write_report(report_path, {"source": str(path), **result})
    finally:
        db.close()

    stats["finished_at"] = datetime.utcnow().isoformat()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa uma pasta inteira para o motor de aprendizado.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Pasta raiz do corpus.")
    parser.add_argument("--categoria", default="AUTO", help="Categoria fixa ou AUTO.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula e gera relatorio.")
    parser.add_argument("--limit", type=int, default=None, help="Limite de arquivos para teste.")
    parser.add_argument("--no-zips", action="store_true", help="Ignora arquivos ZIP.")
    parser.add_argument("--extensions", default="rtf,docx,pdf,xls,xlsx", help="Extensoes treinaveis, separadas por virgula. Padrao: rtf,docx,pdf,xls,xlsx.")
    parser.add_argument("--include-projects", action="store_true", help="Nao filtra nomes de projetos/README/requirements.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Arquivo JSONL de relatorio.")
    args = parser.parse_args(argv)
    extensions = {("." + item.strip().lower().lstrip(".")) for item in args.extensions.split(",") if item.strip()}

    stats = import_folder(
        root=Path(args.source),
        categoria=args.categoria,
        dry_run=args.dry_run,
        limit=args.limit,
        include_zips=not args.no_zips,
        extensions=extensions,
        exclude_projects=not args.include_projects,
        report_path=Path(args.report),
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
