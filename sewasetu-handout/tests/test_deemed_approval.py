import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCHEDULER_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "scripts"
    / "deemed_approval.py"
)
spec = importlib.util.spec_from_file_location("deemed_approval", SCHEDULER_PATH)
deemed_approval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deemed_approval)


class FakeCursor:
    def __init__(self, rows=None, execute_error=None):
        self.rows = rows or []
        self.execute_error = execute_error
        self.closed = False

    def execute(self, *_):
        if self.execute_error:
            raise self.execute_error

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def scheduler_log(monkeypatch, tmp_path):
    monkeypatch.setattr(deemed_approval, "LOG_DIR", str(tmp_path))
    deemed_approval.configure_logging()
    log_path = tmp_path / "sewasetu-scheduler.log"
    yield log_path
    for handler in list(deemed_approval.logger.handlers):
        if getattr(handler, "baseFilename", None) == str(log_path.resolve()):
            deemed_approval.logger.removeHandler(handler)
            handler.close()


def test_main_logs_sms_dispatch_results(monkeypatch, scheduler_log, capsys):
    log_path = scheduler_log
    cursor = FakeCursor(rows=[("SSP26123456", "9876543210"), ("SSP26765432", "9876543211")])
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        deemed_approval.psycopg2, "connect", lambda **_: connection, raising=False
    )
    responses = iter([
        SimpleNamespace(status_code=200, text='{"status":"queued"}',
                        json=lambda: {"status": "queued"}),
        SimpleNamespace(status_code=503, text="gateway unavailable",
                        json=lambda: {"status": "error"}),
    ])
    monkeypatch.setattr(
        deemed_approval.requests,
        "post",
        lambda *_, **__: next(responses),
    )

    assert deemed_approval.main() == 1

    output = capsys.readouterr().out
    assert "2 applications approved, 1 SMS queued, 1 SMS dispatch failures" in output
    assert connection.committed
    assert cursor.closed
    assert connection.closed
    log = log_path.read_text(encoding="utf-8")
    assert "SMS dispatch failed for app SSP26765432" in log
    assert "1 SMS queued, 1 SMS dispatch failures" in log
    assert "9876543211" not in log


def test_main_rolls_back_and_closes_on_database_failure(monkeypatch, scheduler_log):
    cursor = FakeCursor(execute_error=RuntimeError("database unavailable"))
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        deemed_approval.psycopg2, "connect", lambda **_: connection, raising=False
    )
    sms_called = []
    monkeypatch.setattr(
        deemed_approval.requests,
        "post",
        lambda *_, **__: sms_called.append(True),
    )

    assert deemed_approval.main() == 1

    assert connection.rolled_back
    assert not connection.committed
    assert cursor.closed
    assert connection.closed
    assert sms_called == []
