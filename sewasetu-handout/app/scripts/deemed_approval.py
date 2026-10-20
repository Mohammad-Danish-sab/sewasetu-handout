# -*- coding: utf-8 -*-
"""
Nightly deemed-approval job.

Under the Purvanchal Right to Public Services Act, an application not processed
within the statutory SLA stands approved. This job marks such applications
DEEMED_APPROVED and notifies the applicant by SMS.

Scheduled via cron, 02:00 daily. See deploy/crontab.
"""

import os
import sys
import logging
import configparser
from datetime import datetime, timedelta

import psycopg2
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_DIR = "/var/log/sewasetu"
logger = logging.getLogger("sewasetu.scheduler")


def configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.abspath(os.path.join(LOG_DIR, "sewasetu-scheduler.log"))
    for existing in list(logger.handlers):
        if not isinstance(existing, logging.FileHandler):
            continue
        if existing.baseFilename == log_path:
            return
        logger.removeHandler(existing)
        existing.close()
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s in scheduler: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

config = configparser.ConfigParser()
# FIXED: Aligned with standard app.ini filename or check both
CONFIG_PATH = os.path.join(BASE_DIR, "config", "app.ini")
if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.ini")

with open(CONFIG_PATH) as fh:
    config.read_file(fh)

SLA_DAYS = config.getint("pension", "sla_days")
SMS_GATEWAY_URL = config.get("app", "sms_gateway_url")


def main():
    configure_logging()
    try:
        conn = psycopg2.connect(
            host=config.get("database", "host"),
            port=config.get("database", "port"),
            dbname=config.get("database", "name"),
            user=config.get("database", "user"),
            password=config.get("database", "password"),
        )
    except Exception:
        logger.exception("Database connection failed")
        return 1

    cur = None
    transaction_failed = False
    try:
        cur = conn.cursor()
        now = datetime.now()
        cutoff = now - timedelta(days=SLA_DAYS)
        cur.execute(
            """UPDATE applications
               SET status = 'DEEMED_APPROVED', decided_at = %s, decided_by = 'RTPS-AUTO'
               WHERE status = 'PENDING' AND submitted_at < %s
               RETURNING application_no, mobile""",
            (now, cutoff))
        rows = cur.fetchall()
        conn.commit()
    except Exception:
        logger.exception("Failed to update deemed approval records; rolling back")
        transaction_failed = True
        try:
            conn.rollback()
        except Exception:
            logger.exception("Failed to roll back deemed-approval transaction")
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                logger.exception("Failed to close database cursor")
    if transaction_failed:
        try:
            conn.close()
        except Exception:
            logger.exception("Failed to close database connection after transaction error")
        return 1

    success_count = 0
    failure_count = 0
    for app_no, mobile in rows:
        try:
            res = requests.post(SMS_GATEWAY_URL + "/api/send", json={
                "to": mobile,
                "text": "Sewa Setu: your pension application %s stands approved "
                        "under the RTPS Act." % app_no}, timeout=5)
            if res.status_code == 200 and res.json().get("status") == "queued":
                success_count += 1
            else:
                failure_count += 1
                logger.error(
                    "SMS dispatch failed for app %s: gateway status=%d response=%s",
                    app_no, res.status_code, res.text[:300],
                )
        except Exception:
            failure_count += 1
            logger.exception("Failed to dispatch approval SMS for app %s", app_no)

    msg = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} deemed approval: "
        f"{len(rows)} applications approved, {success_count} SMS queued, "
        f"{failure_count} SMS dispatch failures."
    )
    print(msg)
    logger.info(msg)

    try:
        conn.close()
    except Exception:
        logger.exception("Failed to close database connection")
        return 1
    return 1 if failure_count else 0


if __name__ == "__main__":
    sys.exit(main())