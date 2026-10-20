#!/usr/bin/env python3
"""Summarize Sewa Setu access, application, and scheduler logs."""

import argparse
import gzip
import io
import re
import tarfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator, TextIO


ACCESS_LOG_PATTERN = re.compile(
    r'^\S+ \S+ \S+ \[[^\]]+\] "(?P<method>\S+) (?P<target>\S+) '
    r'HTTP/[^"]+" (?P<status>\d{3})(?:\s|$)'
)
APPLICATION_ID_ROUTE = re.compile(r"^/(application|ack)/\d+(?:\.pdf)?$")
ADMIN_APPLICATION_ROUTE = re.compile(r"^/admin/(application|approve|reject)/\d+$")
FORM_ROUTE = re.compile(r"^/form/\d+$")
DATABASE_TIMEOUT_PATTERN = re.compile(
    r"(?i)(?:(?:database|db|postgres(?:ql)?|psycopg2).{0,120}"
    r"(?:timed?\s*out|timeout)|(?:timed?\s*out|timeout).{0,120}"
    r"(?:database|db|postgres(?:ql)?|psycopg2))"
)
DATABASE_EXHAUSTION_PATTERN = re.compile(
    r"(?i)(?:psycopg2|postgres(?:ql)?|database).{0,120}"
    r"(?:too many clients|remaining connection slots are reserved)"
)
SMS_REQUEST_ERROR_PATTERN = re.compile(
    r"(?i)(?:sms.{0,100}(?:gateway error|returned status [45]\d\d)|"
    r"failed to send.{0,100}sms)"
)
SMS_DELIVERY_FAILURE_PATTERN = re.compile(
    r"(?i)sms.{0,100}(?:delivery failed|failed to deliver|undelivered)"
)
MAX_EXAMPLES = 3


def open_log_text(path: Path) -> TextIO:
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def iter_log_files(paths: Iterable[Path]) -> Iterator[tuple[str, TextIO]]:
    """Yield named log streams from files, directories, and tar archives."""
    for path in paths:
        if path.is_dir():
            candidates = sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and (
                    candidate.name.endswith(".log")
                    or candidate.name.endswith(".log.gz")
                    or candidate.name.endswith(".tar.gz")
                )
                and not candidate.name.startswith("._")
            )
        else:
            candidates = [path]

        for candidate in candidates:
            if candidate.name.endswith(".tar.gz") or tarfile.is_tarfile(candidate):
                with tarfile.open(candidate, "r:*") as archive:
                    for member in archive.getmembers():
                        name = Path(member.name).name
                        if (
                            not member.isfile()
                            or name.startswith("._")
                            or not (name.endswith(".log") or name.endswith(".log.gz"))
                        ):
                            continue
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            print(f"Warning: unable to read {member.name} in {candidate}")
                            continue
                        if name.endswith(".gz"):
                            stream = io.TextIOWrapper(
                                gzip.GzipFile(fileobj=extracted),
                                encoding="utf-8",
                                errors="replace",
                            )
                        else:
                            stream = io.TextIOWrapper(extracted, encoding="utf-8", errors="replace")
                        with stream:
                            yield f"{candidate.name}:{member.name}", stream
            elif candidate.name.endswith(".log") or candidate.name.endswith(".log.gz"):
                with open_log_text(candidate) as stream:
                    yield str(candidate), stream
            else:
                print(f"Warning: skipping unsupported input {candidate}")


def normalize_endpoint(target: str) -> str:
    path = target.split("?", 1)[0]
    if APPLICATION_ID_ROUTE.fullmatch(path):
        if path.startswith("/ack/"):
            return "/ack/<id>.pdf"
        return "/application/<id>"
    if ADMIN_APPLICATION_ROUTE.fullmatch(path):
        prefix = path.rsplit("/", 1)[0]
        return f"{prefix}/<id>"
    if FORM_ROUTE.fullmatch(path):
        return "/form/<step>"
    return path or "/"


def add_example(examples: dict[str, list[str]], category: str, source: str, line: str) -> None:
    if len(examples[category]) < MAX_EXAMPLES:
        examples[category].append(f"{source}: {line.rstrip()}")


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="log files, directories, or .tar.gz archives (default: repository logs/)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    paths = args.paths or [root / "logs"]

    http_500: Counter[str] = Counter()
    timeouts = 0
    db_exhaustion = 0
    sms_request_errors = 0
    sms_delivery_failures = 0
    examples: dict[str, list[str]] = defaultdict(list)
    scanned = 0
    malformed_access = 0

    for source, stream in iter_log_files(paths):
        scanned += 1
        basename = Path(source.split(":", 1)[-1]).name.lower()
        for line in stream:
            if "access" in basename:
                match = ACCESS_LOG_PATTERN.match(line)
                if not match:
                    if line.strip():
                        malformed_access += 1
                    continue
                if match.group("status") == "500":
                    endpoint = normalize_endpoint(match.group("target"))
                    http_500[endpoint] += 1
                    add_example(examples, f"http:{endpoint}", source, line)
            else:
                if DATABASE_TIMEOUT_PATTERN.search(line):
                    timeouts += 1
                    add_example(examples, "database", source, line)
                if DATABASE_EXHAUSTION_PATTERN.search(line):
                    db_exhaustion += 1
                    add_example(examples, "db_exhaustion", source, line)
                if SMS_REQUEST_ERROR_PATTERN.search(line):
                    sms_request_errors += 1
                    add_example(examples, "sms_request", source, line)
                if SMS_DELIVERY_FAILURE_PATTERN.search(line):
                    sms_delivery_failures += 1
                    add_example(examples, "sms_delivery", source, line)

    print(f"Scanned {scanned} log file(s) from: {', '.join(str(path) for path in paths)}")
    print(f"Malformed non-empty access-log lines: {malformed_access}")
    print("\n### HTTP 500 responses by endpoint")
    print(markdown_table(
        ["Endpoint", "Count", "Representative log line"],
        [
            [endpoint, str(count), examples[f"http:{endpoint}"][0].replace("|", "\\|")]
            for endpoint, count in http_500.most_common()
        ] or [["None found", "0", "—"]],
    ))
    print("\n### Database connection timeouts")
    print(markdown_table(
        ["Event", "Count", "Representative log lines"],
        [[
            "Explicit database/connection timeout",
            str(timeouts),
            "<br>".join(line.replace("|", "\\|") for line in examples["database"]) or "—",
        ]],
    ))
    print("\n### Database connection exhaustion (not a timeout)")
    print(markdown_table(
        ["Event", "Count", "Representative log lines"],
        [[
            "Database rejected connections because client capacity was exhausted",
            str(db_exhaustion),
            "<br>".join(
                line.replace("|", "\\|")[:240] for line in examples["db_exhaustion"]
            ) or "—",
        ]],
    ))
    print("\n### SMS gateway errors and delivery evidence")
    print(markdown_table(
        ["Event", "Count", "Representative log lines"],
        [[
            "SMS gateway request errors (delivery outcome unknown)",
            str(sms_request_errors),
            "<br>".join(
                line.replace("|", "\\|")[:240] for line in examples["sms_request"]
            ) or "—",
        ], [
            "Explicit SMS delivery failures",
            str(sms_delivery_failures),
            "<br>".join(
                line.replace("|", "\\|")[:240] for line in examples["sms_delivery"]
            ) or "—",
        ]],
    ))
    print(
        "\nNote: the logs record gateway request errors, but no carrier delivery receipts. "
        "A timed-out request may still have been queued or delivered."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
