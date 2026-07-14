"""Shared sensitive-data checks for test-design ledgers and deliverables."""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit


SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(r"\bxox(?:a|b|p|r|s)-[A-Za-z0-9-]{10,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+(?!<)[A-Za-z0-9._~+/\-]{8,}={0,2}\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r'''(?<!\w)["']?(?:secret|password|passwd|pwd|token|api[_ -]?key|access[_ -]?token|client[_ -]?secret)["']?'''
        r'''\s*[:=：]\s*(?:"(?!<)[^"\s;,，]+"|'(?!<)[^'\s;,，]+'|(?!<)[^<"'\s;,，]+)''',
        re.IGNORECASE,
    ),
    re.compile(
        r'''["']?密钥["']?\s*[:=：]\s*(?:"(?!<)[^"\s;,，]+"|'(?!<)[^'\s;,，]+'|(?!<)[^<"'\s;,，]+)''',
        re.IGNORECASE,
    ),
)

ENVIRONMENT_VALUE_PATTERNS = (
    re.compile(
        r"\b(?!(?:[A-Za-z0-9-]+\.)*example\.(?:com|org|net)\b)(?!localhost\b)"
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"(?:com|net|org|cn|io|ai|dev|app|cloud|tech|xyz|site|top|me|cc|biz|info|co|"
        r"uk|de|jp|sg|us|online|store|live|pro|name|mobi|tv|club|work|company|systems|"
        r"network|lan|home|intra|corp|internal|local)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r'''(?<!\w)["']?(?:host(?:name)?|server|domain|endpoint|主机(?:名)?|服务器|域名)["']?'''
        r'''\s*[:=：]\s*(?:"(?!<)[^"\s;,，]+"|'(?!<)[^'\s;,，]+'|(?!<)[^<"'\s;,，]+)''',
        re.IGNORECASE,
    ),
    re.compile(
        r'''(?<!\w)["']?(?:user(?:name)?|account|login[_ -]?account|账号|用户名)["']?'''
        r'''\s*[:=：]\s*(?:"(?!<)[^"\s;,，]+"|'(?!<)[^'\s;,，]+'|(?!<)[^<"'\s;,，]+)''',
        re.IGNORECASE,
    ),
    re.compile(r"/hub/hub(?:/[^\s\"'<>，,；;]*)?", re.IGNORECASE),
    re.compile(r"\badmin@\d+\b", re.IGNORECASE),
)

URL_CANDIDATE_RE = re.compile(r"https?://[^\s\"'<>，,；;]+", re.IGNORECASE)
IPV4_CANDIDATE_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])\[?(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\]?(?![0-9A-Fa-f:])"
)
SAFE_DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)
UNMASKED_VALUE_PATTERNS = (
    (
        "secret",
        SENSITIVE_VALUE_PATTERNS,
        "Use placeholders such as <valid_api_key>, <test_token>, or <test_service_url>.",
    ),
    (
        "environment address/account",
        ENVIRONMENT_VALUE_PATTERNS,
        "Use placeholders such as <product_login_url>, <test_env_base_url>, "
        "<test_user_account>, or <test_user_password>.",
    ),
)

_BINARY_EVIDENCE_SUFFIXES = frozenset(
    {
        ".7z",
        ".avi",
        ".bmp",
        ".doc",
        ".docx",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rar",
        ".tar",
        ".tif",
        ".tiff",
        ".wav",
        ".webm",
        ".webp",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
BINARY_EVIDENCE_AUDIT_SUFFIX = ".sensitive-audit.json"
BINARY_EVIDENCE_AUDIT_FIELDS = {
    "schema_version",
    "evidence_sha256",
    "inspection_method",
    "visible_text",
    "address_bar_cropped_or_masked",
    "environment_identifiers_masked",
    "credentials_masked",
    "status",
    "notes",
}
BINARY_EVIDENCE_INSPECTION_METHODS = {
    "model_visual_review",
    "ocr_and_visual_review",
    "manual_visual_review",
}


class SensitiveDataError(ValueError):
    """Raised when an unmasked environment or credential value is found."""


def _is_safe_documentation_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_loopback or address.is_unspecified or any(
        address.version == network.version and address in network
        for network in SAFE_DOCUMENTATION_NETWORKS
    )


def _is_safe_example_host(host: str | None) -> bool:
    normalized = str(host or "").strip(".[]").lower()
    if not normalized:
        return False
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized in {"example.com", "example.org", "example.net"}:
        return True
    if normalized.endswith((".example.com", ".example.org", ".example.net")):
        return True
    if normalized.endswith((".example", ".invalid", ".test")):
        return True
    try:
        return _is_safe_documentation_ip(ipaddress.ip_address(normalized))
    except ValueError:
        return False


def _unsafe_url(text: str) -> str | None:
    for match in URL_CANDIDATE_RE.finditer(text):
        try:
            parsed = urlsplit(match.group(0))
            host = parsed.hostname
        except ValueError:
            parsed = None
            host = None
        if parsed is None or parsed.username or parsed.password or not _is_safe_example_host(host):
            return match.group(0)
    return None


def _unsafe_ip(text: str) -> str | None:
    candidates = [match.group(0) for match in IPV4_CANDIDATE_RE.finditer(text)]
    candidates.extend(match.group(0).strip("[]") for match in IPV6_CANDIDATE_RE.finditer(text))
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not _is_safe_documentation_ip(address):
            return candidate
    return None


def sensitive_value_violation(value: object, label: str) -> str | None:
    """Return the deterministic violation message for *value*, if any."""

    text = str(value or "")
    # Public SVG/XML namespace identifiers are format constants, not product
    # environment addresses. Remove only these exact constants so SVG evidence
    # remains scannable for real URLs and credentials.
    for public_namespace in (
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/1999/xlink",
        "http://www.w3.org/XML/1998/namespace",
    ):
        text = text.replace(public_namespace, "")
    if _unsafe_url(text) or _unsafe_ip(text):
        return (
            f"{label} contains a possible unmasked environment address/account. "
            "Use placeholders such as <product_login_url>, <test_env_base_url>, "
            "<test_user_account>, or <test_user_password>."
        )
    for kind, patterns, guidance in UNMASKED_VALUE_PATTERNS:
        for pattern in patterns:
            if pattern.search(text):
                return f"{label} contains a possible unmasked {kind}. {guidance}"
    return None


def assert_no_unmasked_value(value: object, label: str) -> None:
    message = sensitive_value_violation(value, label)
    if message:
        raise SensitiveDataError(message)


def assert_no_sensitive_csv_rows(rows: Iterable[Mapping[str, object]], label: str) -> None:
    for index, row in enumerate(rows, start=2):
        for field, value in row.items():
            if value is None or value == "":
                continue
            assert_no_unmasked_value(value, f"{label} row {index} field {field}")


def _text_encoding_candidates(path: Path) -> tuple[str, ...]:
    """Return strict streaming decoders, or no candidates for binary evidence."""

    if path.suffix.lower() in _BINARY_EVIDENCE_SUFFIXES:
        return ()
    with path.open("rb") as stream:
        prefix = stream.read(4096)
    if (
        prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM"))
        or prefix.startswith((b"II*\x00", b"MM\x00*", b"%PDF-", b"PK\x03\x04"))
        or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
    ):
        return ()
    if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ("utf-16",)
    if b"\x00" in prefix:
        return ()
    return ("utf-8-sig", "gb18030")


def assert_no_sensitive_text_file(path: Path, label: str | None = None) -> bool:
    """Scan a decodable text file; return ``False`` when it is binary."""

    source_label = label or path.as_posix()
    try:
        encodings = _text_encoding_candidates(path)
    except OSError as exc:
        raise SensitiveDataError(f"cannot scan sensitive values in {source_label}: {exc}") from exc
    if not encodings:
        return False
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, errors="strict") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if line.strip():
                        assert_no_unmasked_value(line, f"{source_label} line {line_number}")
            return True
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise SensitiveDataError(f"cannot scan sensitive values in {source_label}: {exc}") from exc
    return False


def binary_evidence_audit_path(path: Path) -> Path:
    return path.with_name(path.name + BINARY_EVIDENCE_AUDIT_SUFFIX)


def _is_binary_artifact(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_EVIDENCE_SUFFIXES:
        return True
    return not _text_encoding_candidates(path)


def _scan_binary_and_sha256(path: Path, label: str) -> str:
    """Hash binary evidence while scanning uncompressed metadata/plaintext.

    Pixel/video content still requires the hash-bound visual audit sidecar below;
    this pass catches common image metadata, PDF literals, archive labels, URLs,
    accounts, and credentials that are present as ordinary byte strings.
    """

    printable_run = re.compile(rb"[\x09\x20-\x7e]{6,}")
    trailing_run = re.compile(rb"[\x09\x20-\x7e]+$")
    pending = b""

    def inspect(raw: bytes) -> None:
        if raw:
            assert_no_unmasked_value(raw.decode("ascii", errors="ignore"), f"{label} embedded metadata")

    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                data = pending + chunk
                pending = b""
                trailing_match = trailing_run.search(data)
                scan_end = len(data)
                if trailing_match:
                    trailing = trailing_match.group(0)
                    scan_end = trailing_match.start()
                    if len(trailing) > 64 * 1024:
                        inspect(trailing[:-64 * 1024])
                        trailing = trailing[-64 * 1024:]
                    pending = trailing
                for match in printable_run.finditer(data, 0, scan_end):
                    inspect(match.group(0))
        inspect(pending)
        return digest.hexdigest()
    except OSError as exc:
        raise SensitiveDataError(f"cannot scan binary evidence {label}: {exc}") from exc


def assert_binary_evidence_audited(path: Path, label: str | None = None) -> None:
    """Fail closed unless binary evidence has a hash-bound visual privacy audit."""

    source_label = label or path.as_posix()
    audit_path = binary_evidence_audit_path(path)
    if not audit_path.is_file() or audit_path.stat().st_size == 0:
        raise SensitiveDataError(
            f"{source_label} is binary evidence and requires adjacent visual privacy audit "
            f"{audit_path.name}; inspect/crop/redact visible URL/IP, host, account, and credentials first"
        )
    assert_no_sensitive_text_file(audit_path, f"{source_label} visual privacy audit")
    try:
        value = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SensitiveDataError(f"invalid binary evidence visual privacy audit for {source_label}: {exc}") from exc
    if not isinstance(value, dict) or set(value) != BINARY_EVIDENCE_AUDIT_FIELDS:
        raise SensitiveDataError(
            f"{source_label} visual privacy audit fields must be exactly {sorted(BINARY_EVIDENCE_AUDIT_FIELDS)}"
        )
    if value.get("schema_version") != "1.0.0":
        raise SensitiveDataError(f"{source_label} visual privacy audit schema_version must be 1.0.0")
    if value.get("evidence_sha256") != _scan_binary_and_sha256(path, source_label):
        raise SensitiveDataError(f"{source_label} visual privacy audit hash does not match current evidence bytes")
    if value.get("inspection_method") not in BINARY_EVIDENCE_INSPECTION_METHODS:
        raise SensitiveDataError(
            f"{source_label} visual privacy audit inspection_method must be one of "
            f"{sorted(BINARY_EVIDENCE_INSPECTION_METHODS)}"
        )
    for field in (
        "address_bar_cropped_or_masked",
        "environment_identifiers_masked",
        "credentials_masked",
    ):
        if value.get(field) is not True:
            raise SensitiveDataError(f"{source_label} visual privacy audit must declare {field}=true")
    if value.get("status") != "PASSED":
        raise SensitiveDataError(f"{source_label} visual privacy audit status must be PASSED")
    if not isinstance(value.get("visible_text"), str) or not value["visible_text"].strip():
        raise SensitiveDataError(
            f"{source_label} visual privacy audit visible_text must transcribe the sanitized visible text "
            "or use <no_visible_text>"
        )
    if not isinstance(value.get("notes"), str) or not value["notes"].strip():
        raise SensitiveDataError(f"{source_label} visual privacy audit notes must describe inspection/redaction")


def assert_no_sensitive_artifact(
    path: Path,
    label: str | None = None,
    *,
    require_binary_audit: bool = True,
) -> bool:
    """Scan text directly and enforce hash-bound visual review for binary evidence."""

    if assert_no_sensitive_text_file(path, label):
        return True
    if require_binary_audit:
        assert_binary_evidence_audited(path, label)
    return False


def _scan_csv_file(path: Path, label: str) -> tuple[str, ...]:
    """Scan every CSV cell and return evidence references declared by its header."""

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            headers = next(reader, [])
            evidence_columns = {
                index for index, header in enumerate(headers) if "证据路径" in str(header)
            }
            references: list[str] = []
            for row_number, row in enumerate(reader, start=2):
                for column_number, value in enumerate(row, start=1):
                    if value:
                        assert_no_unmasked_value(
                            value,
                            f"{label} row {row_number} column {column_number}",
                        )
                for index in evidence_columns:
                    if index < len(row) and row[index].strip():
                        references.append(row[index].strip())
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SensitiveDataError(f"cannot scan sensitive values in {label}: {exc}") from exc
    return tuple(references)


def _resolve_evidence_reference(run_dir: Path, raw: str) -> Path | None:
    artifacts_root = (run_dir / "artifacts").resolve()
    value = Path(raw)
    candidates = (
        [value]
        if value.is_absolute()
        else [ancestor / value for ancestor in (run_dir, *run_dir.parents)]
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(artifacts_root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def sensitive_batch_paths(run_dir: Path) -> tuple[Path, ...]:
    """Return every mutable run artifact that can carry generated text."""

    root = run_dir.resolve()
    paths: set[Path] = set()
    for pattern in ("*.csv", "*.md", "*.json", "*.jsonl", "*.txt"):
        paths.update(path.resolve() for path in root.glob(pattern) if path.is_file())
    for directory_name in ("artifacts", "orchestration"):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.name == ".validation-cache.json" or path.name.endswith(".lock"):
                continue
            paths.add(path.resolve())
    return tuple(sorted(paths, key=lambda item: item.as_posix()))


def assert_no_sensitive_batch_files(run_dir: Path) -> None:
    """Scan all ledgers, Agent artifacts, orchestration metadata, and evidence."""

    root = run_dir.resolve()
    scan_files = set(sensitive_batch_paths(root))
    binary_audit_candidates: set[Path] = set()
    for csv_path in sorted(root.glob("*.csv"), key=lambda item: item.name):
        for raw in _scan_csv_file(csv_path, csv_path.name):
            resolved = _resolve_evidence_reference(root, raw)
            if resolved is not None:
                scan_files.add(resolved)
                if _is_binary_artifact(resolved):
                    binary_audit_candidates.add(resolved)
        scan_files.discard(csv_path.resolve())  # already scanned cell-by-cell above

    for path in scan_files:
        if not _is_binary_artifact(path):
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        parts = relative.parts
        if (
            len(parts) >= 3
            and parts[0] == "artifacts"
            and parts[1] in {"evidence", "screenshots", "page-probe-evidence"}
        ):
            binary_audit_candidates.add(path)
        elif (
            len(parts) >= 5
            and parts[0] == "artifacts"
            and parts[1] == "agent-work"
            and "output" in parts[4:]
        ):
            binary_audit_candidates.add(path)
        elif len(parts) >= 3 and parts[:2] == ("orchestration", "accepted"):
            binary_audit_candidates.add(path)

    for path in sorted(binary_audit_candidates, key=lambda item: item.as_posix()):
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:
            label = path.as_posix()
        assert_binary_evidence_audited(path, label)

    for path in sorted(scan_files, key=lambda item: item.as_posix()):
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:
            label = path.as_posix()
        assert_no_sensitive_text_file(path, label)
