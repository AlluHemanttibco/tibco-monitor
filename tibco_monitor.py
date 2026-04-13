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

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- 1. LOAD EXTERNAL CONFIGURATION & MEMORY ---
CONFIG_FILE = os.environ.get("CONFIG_FILE_PATH", "config.json")
STATE_FILE = "ear_state.json" # Acts as the script's memory

try:
    with open(CONFIG_FILE, 'r') as f:
        APP_CONFIG = json.load(f)
except Exception as e:
    logging.error(f"Failed to load config file: {e}")
    sys.exit(1)

try:
    with open(STATE_FILE, 'r') as sf:
        PREV_STATE = json.load(sf)
except Exception:
    PREV_STATE = {}

NEW_STATE = {}

# --- 2. ENVIRONMENT VARIABLES ---
LOG_LINES = 500
CONCURRENCY_LIMIT = 5
MAX_RETRIES = 3

SSH_USER = os.environ.get("SSH_USER")
SSH_PASS = os.environ.get("SSH_PASS")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.urbanout.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "ven-hallu@urbn.com")

TARGET_EARS = [e.strip() for e in os.environ.get("TARGET_EARS", "").split(",")] if os.environ.get("TARGET_EARS") else []
TARGET_ENV = os.environ.get("TARGET_ENV", "ALL")

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

def check_latest_log(env_name, host, app_name, log_dir, log_prefix, filters, expected_stopped):
    ps_cmd = f"pgrep -f '{log_prefix}.*tra'"
    ps_res = run_ssh_command(host, ps_cmd)

    if host in expected_stopped and (ps_res["unreachable"] or ps_res["status"] != 0):
        return {"env": env_name, "host": host, "app": app_name, "state": "EXPECTED_STOPPED", "errors": []}

    if ps_res["unreachable"]:
        return {"env": env_name, "host": host, "app": app_name, "state": "UNREACHABLE", "errors": ["Failed to connect via SSH."]}
    if ps_res["status"] != 0:
        return {"env": env_name, "host": host, "app": app_name, "state": "STOPPED", "errors": ["Process is not running."]}

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
    return {"env": env_name, "host": host, "app": app_name, "state": state, "errors": found_errors[:1]} # Keep the primary error to save space

def generate_unified_report(results):
    report = {
        "healthy_envs": set(),
        "problem_envs": set(),
        "recoveries": [],
        "issues": []
    }
    
    # Process results and compare with memory
    for r in results:
        env = r["env"]
        host = r["host"]
        app = r["app"]
        state = r["state"]
        
        # Unique ID for memory tracking
        key = f"{env}|{host}|{app}"
        prev_state = PREV_STATE.get(key, "HEALTHY")
        
        # Update Memory
        NEW_STATE[key] = state

        # Check for Recovery
        if state in ["HEALTHY", "EXPECTED_STOPPED"]:
            report["healthy_envs"].add(env)
            if prev_state not in ["HEALTHY", "EXPECTED_STOPPED", "UNKNOWN"]:
                report["recoveries"].append(r)
        else:
            report["problem_envs"].add(env)
            report["issues"].append(r)

    # Clean up healthy envs if they actually have a problem
    report["healthy_envs"] = report["healthy_envs"] - report["problem_envs"]
    return report

def send_unified_email(report_data):
    healthy_envs = report_data["healthy_envs"]
    problem_envs = report_data["problem_envs"]
    issues = report_data["issues"]
    recoveries = report_data["recoveries"]

    if not issues and not recoveries:
        logging.info("Everything is healthy. No email sent to prevent spam.")
        return

    # Determine Subject
    if not issues and recoveries:
        subject = "TIBCO EAR Report [RESOLVED] - Services have recovered"
    else:
        env_names = ", ".join(problem_envs)
        subject = f"TIBCO EAR Alert [ACTION REQUIRED] - Issues in {env_names}"

    # Build Executive Summary
    exec_summary = ""
    for env in healthy_envs:
        exec_summary += f"<div style='margin-bottom: 5px;'>🟢 <b>{env}:</b> Fully Healthy</div>"
    
    # Aggregate issues by environment for the summary
    env_issues = {}
    for i in issues:
        env = i["env"]
        env_issues[env] = env_issues.get(env, 0) + 1

    for env, count in env_issues.items():
        exec_summary += f"<div style='margin-bottom: 5px; color: red;'>🔴 <b>{env}:</b> {count} Service Issues Detected</div>"

    # --- HTML CSS STYLING ---
    html = f"""
    <html>
    <head>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #333; }}
        .header {{ background-color: #004d99; color: white; padding: 15px; border-radius: 5px 5px 0 0; }}
        .summary-box {{ background-color: #fdfdfd; padding: 15px; border: 1px solid #ccc; border-left: 5px solid #004d99; margin-bottom: 20px; font-size: 16px; }}
        .content {{ padding: 20px; border: 1px solid #ddd; border-top: none; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 25px;}}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; font-size: 14px; }}
        th {{ background-color: #f2f2f2; font-weight: bold; }}
        .critical {{ background-color: #f8d7da; color: #721c24; font-weight: bold; }}
        .warning {{ background-color: #fff3cd; color: #856404; font-weight: bold; }}
        .recovered {{ background-color: #d4edda; color: #155724; padding: 10px; margin-bottom: 15px; border: 1px solid #c3e6cb; font-weight: bold; }}
    </style>
    </head>
    <body>
        <div class="header" style="{'background-color: #cc0000;' if issues else ''}">
            <h2 style="margin: 0;">TIBCO EAR & Engine Status Report</h2>
        </div>
        <div class="content">
            <div class="summary-box">
                <h3 style="margin-top: 0; margin-bottom: 10px;">📊 Executive Summary</h3>
                {exec_summary}
            </div>
    """

    if recoveries:
        html += "<div class='recovered'>✅ RECOVERY NOTIFICATION: The following services have recovered:<br><ul>"
        for r in recoveries: 
            html += f"<li><b>{r['app']}</b> on {r['host']} ({r['env']}) is now HEALTHY.</li>"
        html += "</ul></div>"

    if issues:
        html += """
        <h3 style='background-color: #f2f2f2; padding: 5px;'>[ DETECTED ANOMALIES ]</h3>
        <table>
            <tr>
                <th>Environment</th>
                <th>App Name</th>
                <th>Host</th>
                <th>Status</th>
                <th>Diagnostic / Log Output</th>
            </tr>
        """
        for i in issues:
            row_class = "critical" if i['state'] in ["ERROR", "UNREACHABLE"] else "warning"
            error_text = i['errors'][0] if i['errors'] else i['state']
            # Truncate overly long log lines to keep tables clean
            if len(error_text) > 150: error_text = error_text[:147] + "..."
            
            html += f"""
            <tr class='{row_class}'>
                <td><b>{i['env']}</b></td>
                <td>{i['app']}</td>
                <td>{i['host']}</td>
                <td>{i['state']}</td>
                <td style='font-family: monospace;'>{error_text}</td>
            </tr>
            """
        html += "</table>"
        
        html += """
        <div style="background-color: #e9ecef; padding: 15px; border-left: 5px solid #004d99; margin-top: 25px;">
            <h3 style="margin-top: 0;">🛠️ Recommended Action</h3>
            <p style="margin:0;">Log into TIBCO Administrator for the affected environment to restart crashed instances or investigate ERROR traces in the application logs.</p>
        </div>
        """

    html += "</div></body></html>"

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = "jenkins@urbanout.com"
    msg['To'] = ALERT_EMAIL
    msg.attach(MIMEText(html, 'html'))
    
    try:
        with smtplib.SMTP(SMTP_SERVER) as server:
            server.send_message(msg)
            logging.info("Unified email report successfully sent.")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

if __name__ == "__main__":
    logging.info(f"Starting checks for Envs: {TARGET_ENV.split(',')}, EARs: {TARGET_EARS if TARGET_EARS else 'ALL'}")

    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT) as executor:
        futures = []
        for app_name, config in APP_CONFIG.items():
            if TARGET_EARS and app_name not in TARGET_EARS: continue

            deployments = config.get("deployments", {})
            for env_name, env_details in deployments.items():
                if TARGET_ENV != "ALL" and env_name not in TARGET_ENV.split(','): continue

                log_dir = env_details["log_dir"]
                machines = env_details["machines"]
                expected_stopped = env_details.get("expected_stopped", [])
                log_prefix = config["log_prefix"]
                filters = config.get("filters", {"alert_on": ["ERROR"], "ignore_patterns": []})

                for host in machines:
                    futures.append(executor.submit(check_latest_log, env_name, host, app_name, log_dir, log_prefix, filters, expected_stopped))

        for future in as_completed(futures):
            results.append(future.result())

    # Save the updated memory state for the next run
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(NEW_STATE, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save state file: {e}")

    report_data = generate_unified_report(results)
    send_unified_email(report_data)
    logging.info("Checks completed.")
