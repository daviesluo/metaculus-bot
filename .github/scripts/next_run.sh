#!/bin/sh
# Start the run that follows this one, unless one is already waiting.
# Called near the start of a run, so that a run that dies still has a successor waiting behind it, and again at its
# end, in case the first call could not. A run started by this script belongs to github-actions[bot]: that is how a
# waiting successor is told from a run someone started by hand or the schedule started.
for attempt in 1 2 3 4 5; do
  waiting=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/run_bot_on_tournament.yaml/runs?per_page=30&event=workflow_dispatch" \
    --jq "[.workflow_runs[] | select(.status != \"completed\" and .id != $GITHUB_RUN_ID and .actor.login == \"github-actions[bot]\")] | length") || waiting=""
  if [ -n "$waiting" ] && [ "$waiting" != "0" ]; then
    echo "The next run is already waiting."
    exit 0
  fi
  if [ "$waiting" = "0" ] && gh workflow run run_bot_on_tournament.yaml --repo "$GITHUB_REPOSITORY" --ref main -f chained=true; then
    echo "The next run is started."
    exit 0
  fi
  sleep 20
done
echo "::error::The next run could not be started. Start this workflow by hand (Run workflow)."
exit 1
