# Deploying on Google Cloud Run

The agent runs as a **Cloud Run Job**. Every 10 minutes a Cloud Scheduler trigger starts one execution. Each
execution checks the marketplace once and finishes any hire or change request it finds, then exits.
Executions get full CPU for as long as they run, with a limit of 3 hours. You pay only while one is running.
Job state and clips live in a Cloud Storage bucket mounted at `/data`, so change requests days later still find
the first delivery. If one execution is still busy when the next starts, the next one skips that assignment.

This setup avoids the alternatives for a few reasons:
- A request-billed Cloud Run *service* throttles the CPU as soon as the response is sent, and the marketplace
  wants an answer within seconds.
- An always-on instance or worker pool would cost about $40 or more a month.

## What it costs

| Item | Free each month | This agent |
|---|---|---|
| Cloud Run Jobs CPU | 240,000 vCPU-seconds | about 170,000 for the checks every 10 min (2 vCPU, ~20 s each) + ~2,400 per 20-min job |
| Cloud Run Jobs memory | 450,000 GiB-seconds | about 345,000 for the checks + ~4,800 per job |
| Cloud Scheduler | 3 triggers | 1 |
| Cloud Storage (us-central1) | 5 GB | source videos + clips, deleted after 10 days |
| Secret Manager | 6 secrets, 10k reads | 3-4 secrets, ~17k reads (≈ $0.03) |

That's roughly **20 jobs a month free**. After that, each job costs about **$0.05**. You need a billing account
with a card. New accounts get $300 of credit for 90 days. Sources: [Cloud Run pricing](https://cloud.google.com/run/pricing).
To cut idle cost, set `EVERY_MINUTES=15`, or pause the trigger when you don't need it.

**Use a separate project, not your "Default Gemini Project".** Turning on billing in the project that owns your
Gemini key moves that key to paid pricing.

## YouTube from Google's servers

Downloads from Cloud Run come from Google datacenter IPs. YouTube often answers those with "confirm you're not a
bot". When that happens, the agent asks the buyer to upload the file instead, but one acceptance test is a
YouTube link. To improve the odds:
1. Export a `cookies.txt` from a browser where a spare Google account is logged into YouTube, for example with
   the "Get cookies.txt LOCALLY" extension.
2. Deploy with `COOKIES_FILE=cookies.txt ./deploy/cloudrun.sh`.

Even with cookies it isn't guaranteed. A computer on a home connection is still the most reliable option for
YouTube.

## Steps

1. Create a project at https://console.cloud.google.com/projectcreate, for example `clipsmith-agent`, and link a
   billing account to it.
2. Open **Cloud Shell**, the `>_` icon at the top right of the console, and run:
   ```bash
   git clone https://github.com/amalnathsathyan/near-clipping-agent && cd near-clipping-agent
   PROJECT_ID=clipsmith-agent ./deploy/cloudrun.sh
   ```
   Paste the marketplace token, Groq key and Gemini key when asked. They go to Secret Manager, never to the repo.
   The first build takes about 5 minutes.
3. Test one run: `gcloud run jobs execute clipper --region us-central1 --wait`. The logs should show the worker
   polling with no errors.
4. In market.near.ai, keep the agent **Self-hosted** with an empty **Webhook URL**.
5. To update after code changes: `git pull && PROJECT_ID=clipsmith-agent ./deploy/cloudrun.sh`, then answer "N"
   to keep the stored keys.

You don't need the "Create service" screen in the console. The script creates a Job, which appears under
**Cloud Run → Jobs**.
