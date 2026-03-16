#!/usr/bin/env python3
import os
import sys
import time
import json
import smtplib
import logging
import paramiko
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- 1. LOAD EXTERNAL CONFIGURATION ---
CONFIG_FILE = os.environ.get("CONFIG_FILE_PATH", "config.json")

try:
    with open(CONFIG_FILE, 'r') as f:
        APP_CONFIG = json.load(f)
except FileNotFoundError:
    logging.error(f"Config file '{CONFIG_FILE}' not found!")
    sys.exit(1)
except json.JSONDecodeError as e:
    logging.error(f"Invalid JSON in '{CONFIG_FILE}': {e}")
    sys.exit(1)

# --- 2. ENVIRONMENT VARIABLES ---
LOG_LINES = 500
CONCURRENCY_LIMIT = 5
MAX_RETRIES = 3

SSH_KEY_PATH = os.environ.get("SSH_KEY_PATH")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "localhost")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "sre-team@domain.com")

TARGET_EARS = [e.strip() for e in os.environ.get("TARGET_EARS", "").split(",")] if os.environ.get("TARGET_EARS") else []
TARGET_ENV = os.environ.get("TARGET_ENV", "STAGE")


def run_ssh_command(host, command, retries=MAX_RETRIES):
    """Executes an SSH command safely."""
    attempt = 0
    while attempt < retries:
        try:
            key = paramiko.Ed25519Key.from_private_key_file(
                SSH_KEY_PATH) if "ed25519" in SSH_KEY_PATH else paramiko.RSAKey.from_private_key_file(SSH_KEY_PATH)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, username="ITApps", pkey=key, timeout=10)

            stdin, stdout, stderr = client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode('utf-8').strip()
            err = stderr.read().decode('utf-8').strip()
            client.close()
            return {"status": exit_status, "out": out, "err": err, "unreachable": False}
        except Exception as e:
            attempt += 1
            logging.warning(f"SSH to {host} failed (Attempt {attempt}/{retries}): {e}")
            time.sleep(2 ** attempt)
    return {"status": -1, "out": "", "err": "Connection failed", "unreachable": True}


def check_latest_log(host, app_name, log_dir, log_prefix, filters):
    """Pulls the latest log file and filters it using Python Regex."""

    ps_cmd = f"pgrep -f '{log_prefix}.*tra'"
    ps_res = run_ssh_command(host, ps_cmd)

    if ps_res["unreachable"]:
        return {"host": host, "app": app_name, "state": "UNREACHABLE", "errors": []}
    if ps_res["status"] != 0:
        return {"host": host, "app": app_name, "state": "STOPPED", "errors": []}

    full_log_path = f"{log_dir}/{app_name}"

    # Notice we removed the egrep from bash. We just run 'tail' now to bring the text to Python.
    log_cmd = f"cd {full_log_path} && LATEST_LOG=$(ls -1t {log_prefix}*.log 2>/dev/null | head -n 1) && if [ -z \"$LATEST_LOG\" ]; then echo 'LOG_NOT_FOUND'; else tail -n {LOG_LINES} \"$LATEST_LOG\"; fi"

    log_res = run_ssh_command(host, log_cmd)

    if "LOG_NOT_FOUND" in log_res["out"]:
        return {"host": host, "app": app_name, "state": "MISSING_LOG",
                "errors": [f"No logs matching '{log_prefix}*.log' found."]}

    # --- NEW PYTHON FILTERING LOGIC ---
    raw_lines = log_res["out"].split('\n')
    found_errors = []

    alert_patterns = [re.compile(p, re.IGNORECASE) for p in filters.get("alert_on", ["ERROR"])]
    ignore_patterns = [re.compile(p, re.IGNORECASE) for p in filters.get("ignore_patterns", [])]

    for line in raw_lines:
        if not line.strip(): continue

        # Check if line matches an alert pattern
        is_alert = any(p.search(line) for p in alert_patterns)

        # Check if line matches an ignore pattern
        is_ignored = any(p.search(line) for p in ignore_patterns)

        if is_alert and not is_ignored:
            found_errors.append(line.strip())

    state = "ERROR" if found_errors else "HEALTHY"
    return {"host": host, "app": app_name, "state": state, "errors": found_errors[:3]}  # Return top 3 unique errors


def generate_report(results):
    critical, info = [], []
    for r in results:
        if r["state"] == "ERROR":
            critical.append(f"{r['app']} on {r['host']} has errors: {r['errors'][0]}")
        elif r["state"] in ["STOPPED", "UNREACHABLE", "MISSING_LOG"]:
            info.append(f"{r['app']} on {r['host']} is {r['state']}. Manual check recommended.")
    return critical, info


def notify(critical, info):
    if not critical and not info:
        logging.info("Everything is healthy. No alerts to send.")
        return

    slack_blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"🚨 TIBCO EAR Report ({TARGET_ENV})"}}]
    if critical:
        slack_blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*CRITICAL ERRORS:*\n" + "\n".join(
            [f"• {c}" for c in critical])}})
    if info:
        slack_blocks.append({"type": "section", "text": {"type": "mrkdwn",
                                                         "text": "*INFO (Stopped/Missing):*\n" + "\n".join(
                                                             [f"• {i}" for i in info])}})

    if SLACK_WEBHOOK: requests.post(SLACK_WEBHOOK, json={"blocks": slack_blocks})


if __name__ == "__main__":
    logging.info(f"Starting checks for Env: {TARGET_ENV}, EARs: {TARGET_EARS if TARGET_EARS else 'ALL'}")

    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT) as executor:
        futures = []

        for app_name, config in APP_CONFIG.items():
            if TARGET_EARS and app_name not in TARGET_EARS: continue

            deployments = config.get("deployments", {})
            if TARGET_ENV not in deployments: continue

            env_details = deployments[TARGET_ENV]
            log_dir = env_details["log_dir"]
            machines = env_details["machines"]
            log_prefix = config["log_prefix"]
            filters = config.get("filters", {"alert_on": ["ERROR"], "ignore_patterns": []})

            for host in machines:
                futures.append(executor.submit(check_latest_log, host, app_name, log_dir, log_prefix, filters))

        for future in as_completed(futures):
            results.append(future.result())

    critical, info = generate_report(results)
    notify(critical, info)
    logging.info("Checks completed.")
