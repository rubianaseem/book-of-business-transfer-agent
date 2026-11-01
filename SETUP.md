# Setup

## 1. Install

You need Python 3.10 or newer.

```bash
git clone https://github.com/rubianaseem/book-of-business-transfer-agent.git
cd book-of-business-transfer-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run on the sample data

```bash
python transfer_agent.py
python -m unittest discover tests
```

The sample is a made-up company, Northbeam, where Dan Okafor is leaving the Mid-Market EMEA team. Each row is built to show one problem: owner values written five different ways, a named account with a deal closing this month, a German subsidiary that should follow its UK parent, an account Priya owned before, a 2,500-person account that belongs with the Enterprise team, a Brazilian account nobody covers, a rep on leave who must not receive anything, a deal Dan owns on Priya's account, an overdue task, a meeting on Dan's calendar and five sequence enrollments at different steps. The policy file pins the date to 2 November 2026 so the sample output is repeatable.

Everything lands in `out/`:

| File | What it is |
|---|---|
| `owner_update_import.csv` | Only the safe owner changes (companies, deals, tasks) with the new owner email and CRM user ID, plus flags like "overdue" or "close date in the past" |
| `review_queue.csv` | Accounts a human must decide on, with the proposed owner and what to check |
| `sequence_plan.csv` | One row per enrollment: re-enrol at step N, let finish, unenrol or hold, with a deadline |
| `transition_brief.md` | Summary, load added to each receiving rep, review list and the order of operations |
| `handoff_briefs.md` | One brief per P1 and P2 account (and anything in review) with deals, tasks and an intro draft |

## 3. Add an Anthropic key (optional)

```bash
cp .env.example .env
# then set ANTHROPIC_API_KEY=sk-ant-...
python transfer_agent.py
```

The rules, matching and files are identical with or without a key. The key only changes who writes the summary and the intro drafts. Claude gets the plan (account names, priorities, deals and next steps), never the raw exports. If the call fails, the agent falls back to templates and carries on.

## 4. Use your own data

You need five CSVs. Column names are listed in the sample files; rename your export headers to match.

**Reps** (`reps.csv`) is a sheet you keep, not an export: one row per rep with status (Active, Departing, On Leave), region, segment, industries, a capacity number and the CRM user ID. In HubSpot the user ID is the owner ID under Settings > Users & Teams. In Salesforce it is the 18-character User Id.

**HubSpot**

1. Companies: filter Company owner is the departing rep, plus a second view of every company in the same parent families. Export with record ID, name, domain, owner, country, industry, employees, your tier property, parent company ID, last activity date and renewal date.
2. Deals: filter Deal owner is the departing rep and deal stage is not closed. Include associated company ID, stage, close date and your next step property.
3. Tasks: filter Assigned to the departing rep and status not completed. Include the associated company and deal.
4. Sequences: in Automation > Sequences, filter enrollments by Enrolled by. Export contact email, sequence, status, current step and next step date. Add the company ID with a lookup if the export does not include it.

**Salesforce**

1. Accounts report filtered on Account Owner, with ParentId, BillingCountry, Industry, NumberOfEmployees and LastActivityDate.
2. Opportunities report with IsClosed = false and Opportunity Owner = departing rep.
3. Activities report for open tasks and future events owned by the rep.
4. Cadence or sequence enrollments from Sales Engagement, Outreach or Salesloft, filtered on the rep.

Then run:

```bash
python transfer_agent.py --reps reps.csv --accounts accounts.csv --deals deals.csv \
  --tasks tasks.csv --sequences enrollments.csv --departing dan@yourco.com --as-of now
```

Importing: in HubSpot, import `owner_update_import.csv` once per object type (filter the `object_type` column), matching on record ID and mapping `new_owner_email` to the owner property. In Salesforce, use Data Loader update with Id and `new_owner_id` mapped to OwnerId. Test five rows first.

## 5. Extend it with an AI coding tool

Open the folder in Cursor, Claude Code, Codex CLI or Grok and ask for one change at a time. Prompts that work well:

- "Add a language column to reps and accounts and make it a hard match rule in `candidates`."
- "Add a `max_p1_per_rep` limit so one rep does not inherit every P1 account."
- "Read contacts from a sixth CSV and add contact owner rows that follow the company's new owner."
- "Write a test for an account whose parent is owned by an active rep, not the departing one."

Ask the tool to run `python -m unittest discover tests` after each change.

## 6. Troubleshooting

- **"No departing rep found"**: set the rep's status to Departing in the reps file or pass `--departing` with their email.
- **Everything lands in UNRESOLVED_OWNER**: the owner column holds a value the reps file does not know. Add it to `owner_aliases` in `transfer_policy.json`.
- **NO_ELIGIBLE_REP on accounts that look fine**: the country is missing from `country_region`, or the segment from `segment_aliases`. The match note says which region and segment it looked for.
- **Dates look wrong**: slash dates where both parts are 12 or below are read day first. Export ISO dates (YYYY-MM-DD) if your CRM allows.
- **Claude step skipped**: check the key in `.env` and that `anthropic` installed. The run still completes with templates.
