#!/usr/bin/env python3
"""
Wazuh -> n8n webhook forwarder.

Wazuh invokes this as: custom-n8n <alert_file_path> <api_key> <hook_url> [options]
It reads the JSON alert Wazuh wrote to disk and POSTs it verbatim to the n8n webhook.

Install location: /var/ossec/integrations/custom-n8n.py
"""

import json
import sys
import time

try:
    import requests
except ImportError:
    print("custom-n8n: missing dependency 'requests'. Install with the Wazuh-bundled pip:"
          " /var/ossec/framework/python/bin/pip3 install requests")
    sys.exit(1)

LOG_FILE = "/var/ossec/logs/integrations.log"


def log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} custom-n8n: {msg}\n")
    except OSError:
        pass


def main(args):
    if len(args) < 4:
        log(f"missing arguments: {args}")
        sys.exit(1)

    alert_file_path = args[1]
    # args[2] is the api_key slot from ossec.conf <api_key> — unused here, kept for interface compatibility
    hook_url = args[3]

    try:
        with open(alert_file_path) as f:
            alert_json = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"failed to read/parse alert file {alert_file_path}: {e}")
        sys.exit(1)

    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(hook_url, data=json.dumps(alert_json), headers=headers, timeout=10)
        if response.status_code >= 300:
            log(f"n8n returned HTTP {response.status_code}: {response.text[:300]}")
    except requests.exceptions.RequestException as e:
        log(f"failed to POST alert to n8n at {hook_url}: {e}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv)
