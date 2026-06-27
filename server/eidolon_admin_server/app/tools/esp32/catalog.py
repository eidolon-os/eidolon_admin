from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from .schemas import Esp32BoardProfile, Esp32Capability, Esp32Partition


ADMIN_ROOT = Path(__file__).resolve().parents[5]
MONOREPO_ROOT = ADMIN_ROOT.parent
CLIENT_ROOT = MONOREPO_ROOT / "eidolon-client-esp32"
DEFAULT_CATALOG_FILE = ADMIN_ROOT / "config" / "esp32_tools.yaml"


def _cap(
    action: str,
    label: str,
    *,
    requires_port: bool = False,
    dangerous: bool = False,
    confirm_token: str | None = None,
) -> Esp32Capability:
    return Esp32Capability(
        action=action,  # type: ignore[arg-type]
        label=label,
        requires_port=requires_port,
        dangerous=dangerous,
        confirm_token=confirm_token,
    )


COMMON_CAPABILITIES: list[Esp32Capability] = [
    _cap("build", "编译"),
    _cap("build_clean", "清理后编译"),
    _cap("flash", "完整烧录", requires_port=True),
    _cap("flash_app", "仅烧录 app", requires_port=True),
    _cap("flash_assets", "仅烧录 assets", requires_port=True),
    _cap("run", "编译 + 烧录 + 监控", requires_port=True),
    _cap("monitor", "串口监控", requires_port=True),
    _cap("clean", "清理 build"),
    _cap("erase_nvs", "擦除 NVS / 长期记忆", requires_port=True, dangerous=True, confirm_token="ERASE NVS"),
    _cap("erase_config", "擦除配置分区", requires_port=True, dangerous=True, confirm_token="ERASE CONFIG"),
    _cap("erase_assets", "擦除 assets", requires_port=True, dangerous=True, confirm_token="ERASE ASSETS"),
    _cap("erase_flash", "擦除整片 Flash", requires_port=True, dangerous=True, confirm_token="ERASE FLASH"),
    _cap("chip_id", "读取 chip_id", requires_port=True),
    _cap("flash_id", "读取 flash_id", requires_port=True),
    _cap("diagnose", "环境诊断"),
]


def board_profiles(catalog_file: Path | None = None) -> list[Esp32BoardProfile]:
    raw = load_catalog(catalog_file)
    client_root = catalog_client_root(raw)
    boards = raw.get("boards")
    if not isinstance(boards, list):
        raise ValueError(f"ESP32 tools catalog has no boards list: {_catalog_path(catalog_file)}")

    profiles: list[Esp32BoardProfile] = []
    for entry in boards:
        if not isinstance(entry, dict):
            raise ValueError("ESP32 board catalog entries must be mappings")
        normalized = dict(entry)
        for key in ("script_path", "build_dir", "sdkconfig", "partition_csv"):
            normalized[key] = _expand_path(str(normalized.get(key, "")), client_root)
        normalized["capabilities"] = COMMON_CAPABILITIES
        profiles.append(Esp32BoardProfile.model_validate(normalized))
    return profiles


def find_board(board_id: str, catalog_file: Path | None = None) -> Esp32BoardProfile | None:
    return next((board for board in board_profiles(catalog_file) if board.id == board_id), None)


def load_catalog(catalog_file: Path | None = None) -> dict[str, Any]:
    path = _catalog_path(catalog_file)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"ESP32 tools catalog must be a mapping: {path}")
    return data


def catalog_client_root(raw: dict[str, Any] | None = None, catalog_file: Path | None = None) -> Path:
    data = raw if raw is not None else load_catalog(catalog_file)
    configured = str(data.get("client_root") or "$EIDOLON_ROOT/eidolon-client-esp32")
    return Path(_expand_path(configured, CLIENT_ROOT)).expanduser().resolve()


def toolchain(raw: dict[str, Any] | None = None, catalog_file: Path | None = None) -> dict[str, str | None]:
    data = raw if raw is not None else load_catalog(catalog_file)
    client_root = catalog_client_root(data)
    configured = data.get("toolchain") if isinstance(data.get("toolchain"), dict) else {}

    idf_export = _configured_tool_path(configured, "idf_export", client_root) or _find_idf_export(client_root)
    idf_path = _configured_tool_path(configured, "idf_path", client_root)
    if not idf_path and idf_export:
        idf_path = str(Path(idf_export).resolve().parent)
    idf_py = _configured_tool_path(configured, "idf_py", client_root)
    if not idf_py and idf_path:
        candidate = Path(idf_path) / "tools/idf.py"
        if candidate.exists():
            idf_py = str(candidate)
    esptool = _configured_tool_path(configured, "esptool", client_root) or shutil.which("esptool.py") or shutil.which("esptool")

    return {
        "idf_export": idf_export,
        "idf_path": idf_path,
        "idf_py": idf_py,
        "esptool": esptool,
    }


def read_partitions(partition_csv: str) -> list[Esp32Partition]:
    path = Path(partition_csv)
    if not path.exists():
        return []
    rows: list[Esp32Partition] = []
    with path.open(encoding="utf-8") as fh:
        for raw in csv.reader(line for line in fh if not line.lstrip().startswith("#")):
            if len(raw) < 5:
                continue
            name = raw[0].strip()
            if not name or name.lower() == "name":
                continue
            offset = raw[3].strip()
            size = raw[4].strip()
            rows.append(Esp32Partition(name=name, offset=offset, size=size))
    return _fill_offsets(rows)


def _fill_offsets(rows: list[Esp32Partition]) -> list[Esp32Partition]:
    """Fill the simple blank-offset OTA row used by the known partition CSVs."""
    previous_offset: int | None = None
    previous_size: int | None = None
    out: list[Esp32Partition] = []
    for row in rows:
        offset = row.offset
        if not offset and previous_offset is not None and previous_size is not None:
            offset = hex(previous_offset + previous_size)
        size_int = _parse_size(row.size)
        offset_int = _parse_size(offset)
        out.append(row.model_copy(update={"offset": offset or row.offset}))
        if offset_int is not None:
            previous_offset = offset_int
            previous_size = size_int
    return out


def _parse_size(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        upper = text.upper()
        if upper.endswith("KB"):
            return int(float(upper[:-2]) * 1024)
        if upper.endswith("K"):
            return int(float(upper[:-1]) * 1024)
        if upper.endswith("MB"):
            return int(float(upper[:-2]) * 1024 * 1024)
        if upper.endswith("M"):
            return int(float(upper[:-1]) * 1024 * 1024)
        return int(text)
    except ValueError:
        return None


def configured_idf_path(catalog_file: Path | None = None) -> str | None:
    for key in ("EIDOLON_IDF_PATH", "IDF_PATH"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    tc = toolchain(catalog_file=catalog_file)
    if tc.get("idf_path"):
        return tc["idf_path"]
    idf_path = catalog_client_root(catalog_file=catalog_file) / "scripts/eidolon/idf.path"
    if idf_path.exists():
        for line in idf_path.read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if clean:
                return clean
    return None


def configured_idf_export(catalog_file: Path | None = None) -> str | None:
    value = os.environ.get("EIDOLON_IDF_EXPORT", "").strip()
    if value:
        return value
    return toolchain(catalog_file=catalog_file).get("idf_export")


def configured_idf_py(catalog_file: Path | None = None) -> str | None:
    return toolchain(catalog_file=catalog_file).get("idf_py") or shutil.which("idf.py")


def configured_esptool(catalog_file: Path | None = None) -> str | None:
    return toolchain(catalog_file=catalog_file).get("esptool")


def _catalog_path(catalog_file: Path | None = None) -> Path:
    return (catalog_file or DEFAULT_CATALOG_FILE).expanduser().resolve()


def _expand_path(value: str, client_root: Path) -> str:
    root = os.environ.get("EIDOLON_ROOT", str(MONOREPO_ROOT))
    text = value.replace("$EIDOLON_ROOT", root).replace("${EIDOLON_ROOT}", root)
    text = text.replace("$CLIENT_ROOT", str(client_root)).replace("${CLIENT_ROOT}", str(client_root))
    text = os.path.expandvars(text)
    return str(Path(text).expanduser().resolve())


def _configured_tool_path(configured: dict[str, Any], key: str, client_root: Path) -> str | None:
    value = str(configured.get(key) or "").strip()
    if not value:
        return None
    path = _expand_path(value, client_root)
    return path if Path(path).exists() else path


def _find_idf_export(client_root: Path) -> str | None:
    candidates: list[Path] = []
    idf_path_file = client_root / "scripts/eidolon/idf.path"
    if idf_path_file.exists():
        for line in idf_path_file.read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if clean:
                candidates.append(Path(clean).expanduser() / "export.sh")
    for key in ("EIDOLON_IDF_PATH", "IDF_PATH"):
        value = os.environ.get(key, "").strip()
        if value:
            candidates.append(Path(value).expanduser() / "export.sh")
    home = Path.home()
    candidates.extend(sorted(home.glob(".espressif/v*/esp-idf/export.sh"), reverse=True))
    candidates.extend([home / "esp/esp-idf/export.sh", home / "esp-idf/export.sh"])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return None
