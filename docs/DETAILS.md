# Details

## Cost note

With no API key the agent costs nothing to run. With a key it makes one Claude call per run and sends only the plan for the departing book (account names, priorities, deals and next steps), so the cost depends on the size of one rep's book, not your whole CRM. Check current pricing on Anthropic's site before a large run. Nothing here calls the CRM API.

## Stack-swap table

| Piece | In this repo | Swap for |
|---|---|---|
| CRM exports | HubSpot or Salesforce CSVs | Dynamics, Pipedrive, a warehouse table synced by Fivetran |
| Rep roster | `sample_reps.csv` you maintain | An HRIS export, a territory tool, a sheet the sales manager owns |
| Sequences | HubSpot Sequences export | Outreach, Salesloft, Apollo or Salesforce Sales Engagement |
| Rules | `transfer_policy.json` | A dbt model or a territory planning sheet |
| LLM | Claude via Anthropic | Any model that returns JSON |
| Delivery | CSV import | A HubSpot workflow, Salesforce Mass Transfer for the safe rows, an n8n flow calling the batch update API |

## Customise for your stack

Everything opinionated lives in `transfer_policy.json`:

- `departing_statuses`, `eligible_statuses`: which roster statuses leave and which can receive. On Leave is in neither, so those reps are skipped.
- `owner_aliases`: every odd owner value you find in exports, mapped to a rep ID.
- `country_region`, `region_aliases`, `segment_aliases`, `industry_aliases`: the normalising maps. Add your own values here, not in code.
- `segment_by_employees`: employee floors for each segment. Swap for your own tiering field if you have one.
- `priority_weights`, `priority_bands`, `windows_days`: what makes an account P1. Tune these with the sales manager, not alone.
- `match_weights`: how much industry fit, previous ownership and free capacity count. Region and segment are hard rules; these only rank the eligible reps.
- `named_accounts_need_signoff`, `late_stage_close_window_days`: when a human must decide.

To add a hard rule, filter in `candidates()`. To add a soft preference, add a weight in `match_score()`. To add a reason for review, append a code in `review_rules()` and a line to `REVIEW_HELP`.

## How this runs today (and what production would add)

Today it is a script you run by hand when someone hands in notice. It reads five CSVs, decides and writes files. It never writes to the CRM, never sends an email and never touches a sequence. You review, then import.

Production would add:

- A trigger: when a rep's status changes to Departing in the HRIS or roster, the run starts and the brief goes to the manager's channel.
- Live capacity from the CRM (count of owned accounts per rep) instead of a number in a sheet.
- A guarded write-back for the safe rows only, with a before-state backup and a dry-run diff a manager approves.
- Sequence handling through the engagement tool's API, so unenrol and re-enrol at step N happen together.
- A follow-up check two weeks later: did every P1 account get a logged meeting with the new owner, and did any deal slip.
