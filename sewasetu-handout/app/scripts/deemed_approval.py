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

# Configure logging
_logdir = "/var/log/sewasetu"
os.makedirs(_logdir, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_logdir, "sewasetu-scheduler.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s in scheduler: %(message)s"
)

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
    try:
        conn = psycopg2.connect(
            host=config.get("database", "host"),
            port=config.get("database", "port"),
            dbname=config.get("database", "name"),
            user=config.get("database", "user"),
            password=config.get("database", "password"),
        )
    except Exception as e:
        logging.error("Database connection failed: %s", e)
        return 1

    cur = conn.cursor()
    cutoff = datetime.now() - timedelta(days=SLA_DAYS)
    
    try:
        cur.execute(
            """UPDATE applications
               SET status = 'DEEMED_APPROVED', decided_at = %s, decided_by = 'RTPS-AUTO'
               WHERE status = 'PENDING' AND submitted_at < %s
               RETURNING application_no, mobile""",
            (datetime.now(), cutoff))
        rows = cur.fetchall()
        conn.commit()
    except Exception as e:
        logging.error("Failed to update deemed approval records: %s", e)
        conn.rollback()
        cur.close()
        conn.close()
        return 1

    success_count = 0
    for app_no, mobile in rows:
        try:
            res = requests.post(SMS_GATEWAY_URL + "/api/send", json={
                "to": mobile,
                "text": "Sewa Setu: your pension application %s stands approved "
                        "under the RTPS Act." % app_no}, timeout=5)
            if res.status_code == 200:
                success_count += 1
            else:
                logging.warning("SMS gateway returned status %d for app %s", res.status_code, app_no)
        except Exception as e:
            logging.error("Failed to send approval SMS for app %s to mobile %s: %s", app_no, mobile, e)

    msg = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} deemed approval: {len(rows)} applications approved, {success_count} SMS sent successfully."
    print(msg)
    logging.info(msg)
    
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())