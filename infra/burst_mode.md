# Burst mode: run the VM about an hour a day

The pipeline only needs a machine while it scripts, renders and uploads. In
burst mode the VM starts each morning, does the day's work, uploads every
Short with a scheduled go-live time, and powers off. YouTube publishes them
at 12:30, 18:30 and 21:00 IST.

This has not been run on a real VM yet. Do the first run by hand (step 3) and
watch the log before trusting the schedule.

## What changes compared with the daemon

| | Daemon (`--daemon`) | Burst (`--burst`) |
| :--- | :--- | :--- |
| VM | on 24 hours | on for the run, roughly 30 to 90 minutes |
| Publishing | uploaded at each slot | uploaded in the morning, scheduled for each slot |
| Comment under the Short | posted | not posted (the video is still private at upload time) |
| Review | `python -m agents.review` | in YouTube Studio, before the scheduled time |

With `REVIEW_REQUIRED=true`, burst mode uploads the Shorts as private with no
schedule, and you publish them from YouTube Studio.

## Setup

1. On the VM, stop the daemon for good so the two modes never overlap:

   ```bash
   cd ~/almightyanand_studio
   docker compose stop orchestrator && docker compose rm -f orchestrator
   ```

2. Run the burst script at boot. `crontab -e`, then add:

   ```
   @reboot /home/<your-user>/almightyanand_studio/infra/burst_run.sh
   ```

   The user needs passwordless `sudo shutdown` (the default on GCP images) and
   must be in the `docker` group.

3. Test once without powering off:

   ```bash
   BURST_SHUTDOWN=false ./infra/burst_run.sh
   tail -n 50 logs/burst_*.log
   ```

4. Start the VM every morning with an instance schedule. The stop time is a
   backstop in case the script hangs:

   ```bash
   gcloud compute resource-policies create instance-schedule almighty-burst \
     --project="$GCP_PROJECT_ID" --region=us-central1 \
     --vm-start-schedule="15 6 * * *" \
     --vm-stop-schedule="15 8 * * *" \
     --timezone="Asia/Kolkata"

   gcloud compute instances add-resource-policies almighty-cpu-orchestrator \
     --project="$GCP_PROJECT_ID" --zone=us-central1-a \
     --resource-policies=almighty-burst
   ```

   The Compute Engine service agent needs permission to start and stop the
   instance (`compute.instances.start` and `compute.instances.stop`).

## Notes

- A stopped VM still pays for its disk, a few dollars a month for 30 GB.
- If a run fails before uploading, the rendered Shorts stay in
  `memory/publish_queue.json` and go out with the next day's run.
- The analytics review runs at the start of each burst, so it still happens daily.
