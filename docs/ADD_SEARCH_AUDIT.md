# Add/Search payload audit operations

When enabled, EviEpoch records only authenticated raw HTTP request and response bodies for `POST /add` and `POST /search`, plus UTC timestamp, route, status, duration, and truncation flags. Requests without a valid bearer/token or `X-API-Key` credential are not captured. Authorization headers, client IPs, and other routes are never recorded. Request and response bodies pass through unchanged; capture truncates at 1 MiB per body by default and does not reject a larger AML request.

On the ECS the JSONL files live under `/var/lib/eviepoch/audit`, owned by the service account. The directory mode is `0700`, files are `0600`. Total retained JSONL is capped at 2 GiB and records older than 30 days are pruned when Add/Search traffic arrives. If the cap is reached, `AUDIT_CAP_REACHED.txt` marks the pause; the API remains available. Capture resumes after retention or an operator export frees enough space. Do not commit exported JSONL or include it in GitHub issues.

Export from the Windows laptop after the service has received traffic:

```powershell
.\deploy\export-audit.ps1
```

The script connects to the already verified ECS IP by default, downloads matching JSONL files to `Downloads\EviEpoch-audit`, and leaves the server copies intact. Use `-ServerHost bubblevan.de5.net` only after that hostname has been added to your verified SSH `known_hosts`. Raw records may contain private user memories; keep the local export private and use only the dataset scope authorized by the competition announcement.
