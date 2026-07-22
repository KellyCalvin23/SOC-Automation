# Deployment Guide

Step-by-step setup for the SOC-Automation pipeline. Written assuming three separate Linux hosts reachable over SSH (Wazuh manager, n8n, Ollama) — if you're running everything on one box, just substitute `localhost`/the same IP everywhere.

Placeholders used throughout: `<WAZUH_MANAGER_IP>`, `<WAZUH_SSH_USER>`, `<N8N_HOST_IP>`, `<N8N_SSH_USER>`, `<OLLAMA_HOST_IP>`, `<OLLAMA_SSH_USER>`.

## 0. Prerequisites

- A Wazuh manager already installed and running (v4.x). If you don't have one yet, follow the [official Wazuh quickstart](https://documentation.wazuh.com/current/quickstart.html) first — this guide starts from "Wazuh is running."
- A host with Docker for n8n.
- A host with Docker (or Ollama installed natively) for the LLM — 4+ CPU cores and 8GB+ RAM recommended for an 8B model; more if you want faster responses.
- SSH access with `sudo` to all hosts involved.
- A [Discord](https://discord.com) server you can add a webhook to.
- A free [AbuseIPDB](https://www.abuseipdb.com/account/api) account and API key.

Clone this repo somewhere you'll run commands from (your own workstation is fine — you don't need to clone it onto the target hosts):

```bash
git clone https://github.com/<your-username>/SOC-Automation.git
cd SOC-Automation
```

## 1. Stand up n8n

On `<N8N_HOST_IP>`:

```bash
sudo docker run -d --name n8n \
  -v n8n_data:/home/node/.n8n \
  -p 5678:5678 \
  --restart unless-stopped \
  docker.n8n.io/n8nio/n8n
```

Confirm it's up:

```bash
curl -s http://localhost:5678/healthz
```

## 2. Stand up Ollama

On `<OLLAMA_HOST_IP>`:

```bash
sudo docker run -d --name ollama \
  -v ollama_data:/root/.ollama \
  -p 11434:11434 \
  --restart unless-stopped \
  ollama/ollama

sudo docker exec ollama ollama pull llama3.1:8b
```

Sanity check (expect a JSON response, may take 10-30s the first time):

```bash
curl -s http://localhost:11434/api/generate -d '{"model":"llama3.1:8b","prompt":"Reply with exactly one word: OK","stream":false}'
```

If your host has 16GB+ RAM and more CPU cores, larger models (`llama3.1:70b`, etc.) will give better analysis at the cost of latency. Smaller ones (`llama3.2:3b`, `llama3.2:1b`) trade quality for speed.

## 3. Create your Discord webhook

In Discord: **Server Settings → Integrations → Webhooks → New Webhook**. Pick the channel you want alerts in, copy the webhook URL (looks like `https://discord.com/api/webhooks/<id>/<token>`).

## 4. Get an AbuseIPDB API key

Sign up at [abuseipdb.com](https://www.abuseipdb.com/account/api), create a key (free tier: 1,000 lookups/day).

## 5. Fill in the workflow placeholders

Edit `n8n-workflow-wazuh-soc.json` and replace:

| Placeholder | Replace with |
|---|---|
| `YOUR_OLLAMA_HOST_IP` (2 occurrences) | `<OLLAMA_HOST_IP>` from step 2 |
| `https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN` | your real webhook URL from step 3 |
| `YOUR_ABUSEIPDB_API_KEY` | your real key from step 4 |

You can do this with `sed`, e.g.:

```bash
sed -i '' \
  -e 's|YOUR_OLLAMA_HOST_IP|100.x.x.x|g' \
  -e 's|https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN|https://discord.com/api/webhooks/REAL_ID/REAL_TOKEN|' \
  -e 's|YOUR_ABUSEIPDB_API_KEY|REAL_KEY|' \
  n8n-workflow-wazuh-soc.json
```

(drop the `''` after `-i` on Linux — that empty-string argument is macOS/BSD `sed`'s way of saying "no backup file"). **Do not commit this edited file** — it now contains live secrets.

If you'd rather not edit raw JSON, import the workflow as-is (next step) and then update the three fields directly in the n8n editor: the "AI Analysis (Ollama)" node's URL, the "AbuseIPDB Lookup" node's `Key` header, and the "Discord Notify" node's URL.

## 6. Import the workflow into n8n

```bash
scp n8n-workflow-wazuh-soc.json <N8N_SSH_USER>@<N8N_HOST_IP>:/tmp/
ssh <N8N_SSH_USER>@<N8N_HOST_IP> '
  sudo docker cp /tmp/n8n-workflow-wazuh-soc.json n8n:/tmp/n8n-workflow-wazuh-soc.json
  sudo docker exec n8n n8n import:workflow --input=/tmp/n8n-workflow-wazuh-soc.json
'
```

Find the imported workflow's ID, then publish and restart to activate it:

```bash
ssh <N8N_SSH_USER>@<N8N_HOST_IP> 'sudo docker exec n8n n8n list:workflow'
# note the ID printed next to "Wazuh SOC Triage"

ssh <N8N_SSH_USER>@<N8N_HOST_IP> '
  sudo docker exec n8n n8n publish:workflow --id=<WORKFLOW_ID>
  sudo docker restart n8n
'
```

> If your n8n version doesn't have `publish:workflow` (older/newer CLI), just open the workflow in the n8n web UI and toggle it **Active**.

Confirm the webhook is live:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://<N8N_HOST_IP>:5678/webhook/wazuh-alert \
  -H "Content-Type: application/json" \
  -d '{"rule":{"level":10,"description":"test","id":"1","groups":["authentication_failed"]},"agent":{"name":"test","ip":"10.0.0.1"},"data":{"srcip":"1.2.3.4"},"full_log":"test","timestamp":"2026-01-01T00:00:00Z"}'
```

Expect `200`. Check Discord for the test alert — it'll take 30-90s for the AI Analysis step to finish.

## 7. Install the Wazuh integration

On `<WAZUH_MANAGER_IP>`:

```bash
scp wazuh-integration/custom-n8n wazuh-integration/custom-n8n.py <WAZUH_SSH_USER>@<WAZUH_MANAGER_IP>:/tmp/
ssh <WAZUH_SSH_USER>@<WAZUH_MANAGER_IP> '
  sudo cp /tmp/custom-n8n /tmp/custom-n8n.py /var/ossec/integrations/
  sudo chown root:wazuh /var/ossec/integrations/custom-n8n /var/ossec/integrations/custom-n8n.py
  sudo chmod 750 /var/ossec/integrations/custom-n8n /var/ossec/integrations/custom-n8n.py
'
```

The Wazuh-bundled Python needs the `requests` module (usually already present):

```bash
ssh <WAZUH_SSH_USER>@<WAZUH_MANAGER_IP> '
  sudo /var/ossec/framework/python/bin/python3 -c "import requests" || \
  sudo /var/ossec/framework/python/bin/pip3 install requests
'
```

## 8. Wire Wazuh to n8n

Add the block from `wazuh-integration/ossec-conf-snippet.xml` to `/var/ossec/etc/ossec.conf` on the manager, replacing `<N8N_HOST>` with `<N8N_HOST_IP>`:

```bash
ssh <WAZUH_SSH_USER>@<WAZUH_MANAGER_IP> '
  sudo cp /var/ossec/etc/ossec.conf /var/ossec/etc/ossec.conf.bak.$(date +%Y%m%d%H%M%S)
  cat <<EOF | sudo tee -a /var/ossec/etc/ossec.conf > /dev/null

<ossec_config>
  <integration>
    <name>custom-n8n</name>
    <hook_url>http://<N8N_HOST_IP>:5678/webhook/wazuh-alert</hook_url>
    <level>7</level>
    <alert_format>json</alert_format>
  </integration>
</ossec_config>
EOF
  sudo /var/ossec/bin/wazuh-analysisd -t
'
```

`wazuh-analysisd -t` should print nothing and exit 0 if the config is valid. Then restart to load it:

```bash
ssh <WAZUH_SSH_USER>@<WAZUH_MANAGER_IP> 'sudo systemctl restart wazuh-manager'
```

The `<level>` here **must match** the "Severity gate" node's threshold in the n8n workflow (default: `7`). Change both together if you adjust it.

## 9. End-to-end test

Trigger a real alert — e.g. a few failed SSH logins against the Wazuh manager with an invalid username:

```bash
for i in 1 2 3; do ssh -o BatchMode=yes -o ConnectTimeout=5 nonexistent_test_user@<WAZUH_MANAGER_IP> exit 2>/dev/null; done
```

Within a minute or two (mostly waiting on the AI step), you should see a Discord alert with a real level, agent, source IP, AbuseIPDB reputation, AI summary, and suggested next step.

Directly test the integration script (bypasses waiting on a natural alert):

```bash
ssh <WAZUH_SSH_USER>@<WAZUH_MANAGER_IP> '
cat > /tmp/fake_alert.json << "EOF"
{
  "timestamp": "2026-01-01T00:00:00.000+0000",
  "rule": {"level": 10, "description": "test alert", "id": "1", "groups": ["authentication_failed"]},
  "agent": {"id": "000", "name": "test-agent", "ip": "127.0.0.1"},
  "data": {"srcip": "8.8.8.8"},
  "full_log": "test log line",
  "location": "/var/log/auth.log"
}
EOF
sudo /var/ossec/integrations/custom-n8n /tmp/fake_alert.json "" "http://<N8N_HOST_IP>:5678/webhook/wazuh-alert"
echo "exit code: $?"
rm /tmp/fake_alert.json
'
```

Exit code `0` with no output means success (the script only logs on failure, to `/var/ossec/logs/integrations.log`).

## 10. Adding more monitored hosts

Only the Wazuh manager itself generates alerts until you enroll more agents. Install a Wazuh agent on any host you want monitored and enroll it against `<WAZUH_MANAGER_IP>` — no n8n-side changes needed, alerts from any enrolled agent flow through automatically.

---

## Troubleshooting

**Symptom: n8n execution shows `status: success` but Discord shows `n/a`/`undefined` fields.**
Almost always means a node between the alert's origin and its use replaced `$json` instead of extending it. Two culprits in this workflow's history:
- **HTTP Request nodes replace `$json` with their own response**, not the original input. Any node after "AbuseIPDB Lookup" or "AI Analysis" that needs the original alert data reads it via `$('Node Name').item.json` rather than `$input.item.json` for exactly this reason.
- **n8n's Set/Edit Fields node in "manual" mode drops every field except the ones it explicitly assigns.** This workflow avoids Set nodes for that reason and uses Code nodes everywhere data needs to pass through.

**Symptom: alerts never reach n8n / `/var/ossec/logs/integrations.log` shows errors.**
- Check the wrapper script quotes `"$@"` (not bare `$@`) — an unquoted `$@` silently drops Wazuh's empty `api_key` argument and shifts the hook URL into the wrong slot.
- Confirm `/var/ossec/integrations/custom-n8n{,.py}` are owned `root:wazuh`, mode `750`.
- Confirm the Wazuh manager can reach `<N8N_HOST_IP>:5678` (firewall/VPN routing).

**Symptom: AI Analysis step times out or errors.**
- CPU-only inference is slow. The "AI Analysis (Ollama)" HTTP Request node's `options.timeout` is set to 180000ms (3 minutes) — raise it if your hardware is slower, or switch to a smaller model.
- Confirm the n8n host can reach `<OLLAMA_HOST_IP>:11434` directly (`curl http://<OLLAMA_HOST_IP>:11434/`).

**Symptom: AbuseIPDB lookups fail or return nothing.**
- Check you haven't exceeded the free tier's 1,000/day limit.
- The node is configured with `onError: continueRegularOutput` so a failed lookup won't break the rest of the pipeline — "Build AI Prompt" treats missing AbuseIPDB data as `null` gracefully either way.

**Config test command for Wazuh** (`wazuh-analysisd -t`) doesn't exist on your version: try `/var/ossec/bin/wazuh-control configtest` or consult your version's docs — the flag has moved between Wazuh releases.
