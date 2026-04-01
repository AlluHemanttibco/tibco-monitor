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
except Exception as e:
    logging.error(f"Failed to load config file: {e}")
    sys.exit(1)

# --- 2. ENVIRONMENT VARIABLES ---
LOG_LINES = 500
CONCURRENCY_LIMIT = 5
MAX_RETRIES = 3

SSH_USER = os.environ.get("SSH_USER")
SSH_PASS = os.environ.get("SSH_PASS")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.urbanout.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "ven-hallu@urbn.com")

TARGET_EARS = [e.strip() for e in os.environ.get("TARGET_EARS", "").split(",")] if os.environ.get("TARGET_EARS") else []

# CHANGED: Now expects a list of environments like "STAGE,DEV"
TARGET_ENVS = [e.strip().upper() for e in os.environ.get("TARGET_ENV", "ALL").split(",")]

def run_ssh_command(host, command, retries=MAX_RETRIES):
    attempt = 0
    while attempt < retries:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, username=SSH_USER, password=SSH_PASS, timeout=10)
            
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

def check_latest_log(env_name, host, app_name, log_dir, log_prefix, filters):
    ps_cmd = f"pgrep -f '{log_prefix}.*tra'"
    ps_res = run_ssh_command(host, ps_cmd)

    if ps_res["unreachable"]:
        return {"env": env_name, "host": host, "app": app_name, "state": "UNREACHABLE", "errors": []}
    if ps_res["status"] != 0:
        return {"env": env_name, "host": host, "app": app_name, "state": "STOPPED", "errors": []}

    full_log_path = f"{log_dir}/{app_name}"
    log_cmd = f"cd {full_log_path} && LATEST_LOG=$(ls -1t {log_prefix}*.log 2>/dev/null | head -n 1) && if [ -z \"$LATEST_LOG\" ]; then echo 'LOG_NOT_FOUND'; else tail -n {LOG_LINES} \"$LATEST_LOG\"; fi"

    log_res = run_ssh_command(host, log_cmd)

    if "LOG_NOT_FOUND" in log_res["out"]:
        return {"env": env_name, "host": host, "app": app_name, "state": "MISSING_LOG", "errors": [f"No logs matching '{log_prefix}*.log' found."]}

    raw_lines = log_res["out"].split('\n')
    found_errors = []
    alert_patterns = [re.compile(p, re.IGNORECASE) for p in filters.get("alert_on", ["ERROR"])]
    ignore_patterns = [re.compile(p, re.IGNORECASE) for p in filters.get("ignore_patterns", [])]

    for line in raw_lines:
        if not line.strip(): continue
        is_alert = any(p.search(line) for p in alert_patterns)
        is_ignored = any(p.search(line) for p in ignore_patterns)
        if is_alert and not is_ignored:
            found_errors.append(line.strip())

    state = "ERROR" if found_errors else "HEALTHY"
    return {"env": env_name, "host": host, "app": app_name, "state": state, "errors": found_errors[:3]}

def generate_report(results):
    report_data = {}
    for r in results:
        env = r["env"]
        if env not in report_data:
            report_data[env] = {"critical": [], "info": []}
            
        if r["state"] == "ERROR":
            report_data[env]["critical"].append(f"<b>{r['app']}</b> on {r['host']}: {r['errors'][0]}")
        elif r["state"] in ["STOPPED", "UNREACHABLE", "MISSING_LOG"]:
            report_data[env]["info"].append(f"<b>{r['app']}</b> on {r['host']} is {r['state']}")
            
    return report_data

def notify(report_data):
    if not report_data:
        logging.info("No environments were scanned. No emails to send.")
        return

    # Loop through each environment and send a SEPARATE email for each
    for env, data in report_data.items():
        critical = data["critical"]
        info = data["info"]
        
        # Build the HTML Email for THIS specific environment
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif;">
            <h2>TIBCO EAR Status Report ({env})</h2>
            <hr>
        """
        
        # If both lists are empty, the environment is completely healthy
        if not critical and not info:
            logging.info(f"[{env}] is fully healthy. Sending 'All Clear' email.")
            html += f"""
            <div style="background-color: #d4edda; color: #155724; padding: 15px; border-radius: 5px; border: 1px solid #c3e6cb;">
                <h3 style="margin-top: 0;">✅ All Monitored EARs are HEALTHY</h3>
                <p style="margin-bottom: 0;">No critical errors, stopped processes, or missing logs were found in the <b>{env}</b> environment.</p>
            </div>
            """
        else:
            # Otherwise, list the errors and info as usual
            logging.info(f"[{env}] has issues. Sending alert email.")
            html += f"""
            <h3 style="background-color: #f2f2f2; padding: 5px;">[ {env} ENVIRONMENT ISSUES ]</h3>
            """
            
            if critical:
                html += f"""
                <h4 style="color: red; margin-bottom: 2px;">Critical Errors Found</h4>
                <ul style="margin-top: 5px;">{''.join([f"<li>{c}</li>" for c in critical])}</ul>
                """
                
            if info:
                html += f"""
                <h4 style="color: gray; margin-bottom: 2px;">Info / Process Stopped / Unreachable</h4>
                <ul style="margin-top: 5px;">{''.join([f"<li>{i}</li>" for i in info])}</ul>
                """
        
        html += "</body></html>"

        # Prepare the email message for THIS environment
        msg = MIMEMultipart()
        msg['Subject'] = f"TIBCO EAR Report [{env}] - {'HEALTHY' if not critical and not info else 'ACTION REQUIRED'}"
        msg['From'] = "jenkins@urbanout.com"
        msg['To'] = ALERT_EMAIL
        msg.attach(MIMEText(html, 'html'))
        
        # Send the email
        try:
            with smtplib.SMTP(SMTP_SERVER) as server:
                server.send_message(msg)
                logging.info(f"Email report successfully sent for {env} to {ALERT_EMAIL}")
        except Exception as e:
            logging.error(f"Failed to send email for {env}: {e}")

if __name__ == "__main__":
    logging.info(f"Starting checks for Envs: {TARGET_ENVS}, EARs: {TARGET_EARS if TARGET_EARS else 'ALL'}")

    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT) as executor:
        futures = []

        for app_name, config in APP_CONFIG.items():
            if TARGET_EARS and app_name not in TARGET_EARS: continue

            deployments = config.get("deployments", {})
            for env_name, env_details in deployments.items():
                
                # CHANGED: Now checks if the environment is in the list of checked boxes
                if "ALL" not in TARGET_ENVS and env_name.upper() not in TARGET_ENVS: 
                    continue

                log_dir = env_details["log_dir"]
                machines = env_details["machines"]
                log_prefix = config["log_prefix"]
                filters = config.get("filters", {"alert_on": ["ERROR"], "ignore_patterns": []})

                for host in machines:
                    futures.append(executor.submit(check_latest_log, env_name, host, app_name, log_dir, log_prefix, filters))

        for future in as_completed(futures):
            results.append(future.result())

    report_data = generate_report(results)
    notify(report_data)
    logging.info("Checks completed.")
