# Book of Business Transfer Agent

An agent that plans the handoff when a rep leaves: it ranks their accounts, matches each one to the right receiving rep, and writes a safe owner-update import, a review queue, a sequence re-enrol plan and handoff briefs.

Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).

## The pain point

When a rep leaves, the book usually moves in one bulk owner change or a round robin. Strategic accounts land with whoever is next, account families get split, deals closing this month lose their context, and sequences keep sending from an inbox nobody is watching. Changing the owner field is the easy part. Everything attached to it is what breaks.

> "export a spreadsheet, manually match accounts to reps, send a bunch of "hey, meet your new rep" emails that all sound the same... the high-value ones get whatever context the departing rep remembers to write down (usually not much)."
> [r/revops: "How do you handle account transitions when a rep leaves?"](https://www.reddit.com/r/revops/comments/1rxh4kq/how_do_you_handle_account_transitions_when_a_rep/)

> "There is no way I know of to change who the sequence emails are coming from (or who tasks are getting assigned to) for a contact in the middle of a sequence."
> [r/hubspot: "Re-assigning a contact, already in a sequence, and keeping their place?"](https://www.reddit.com/r/hubspot/comments/tg8z4q/reassigning_a_contact_already_in_a_sequence_and/)

## What it does

- Reads five exports (reps, accounts, open deals, open tasks, sequence enrollments) and resolves messy owner values (names, emails, odd casing, initials) to one rep. Owners it cannot match go to review instead of being guessed.
- Scores every account in the departing book on tier, named status, late-stage deals, close dates, renewals, booked meetings and recent activity, then bands it P1, P2 or P3.
- Matches each account to an eligible rep on region, segment, industry fit, previous ownership and free capacity, highest priority first. Child accounts follow their parent. No round robin.
- Sends anything a human should decide to a review queue: named accounts, late-stage deals closing this month, no eligible rep, an account family that would push a rep over capacity, and unmatched owners.
- Works out what to do with each sequence enrollment (re-enrol at the next step, let the last step finish, unenrol, or hold), because changing the owner does not change the sender.
- Writes a CRM-ready owner import, the review queue, the sequence plan, a transition brief and per-account handoff briefs with intro drafts. Rules run with no API key. With an Anthropic key, Claude writes the summary and intros from the plan only.

## Where each step runs

1. **GitHub** holds the code. You copy it to your laptop with `git clone`.
2. **Your CRM** is where you export the five input files. [SETUP.md](SETUP.md) shows the HubSpot and Salesforce views to build.
3. **Terminal** (Mac Terminal or the terminal inside Cursor) runs the script. It reads the CSVs and writes everything to `out/`.
4. **Claude is optional.** With no key it uses built-in rules and templates. With a key in `.env` it writes sharper intro drafts and the summary. It still runs in the terminal.
5. **Back in the CRM**, you clear the review queue with the sales manager, work through the sequence plan, then import `out/owner_update_import.csv` after testing five rows. The agent never writes to your CRM itself.

## Quick start

```bash
git clone https://github.com/rubianaseem/book-of-business-transfer-agent.git
cd book-of-business-transfer-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python transfer_agent.py
python -m unittest discover tests
```

On your own data: `python transfer_agent.py --reps reps.csv --accounts accounts.csv --deals deals.csv --tasks tasks.csv --sequences enrollments.csv --departing dan@yourco.com --as-of now`

## Example output

```
Book loaded: 6 accounts. Mode: rules only (no API key)
  P1  A100  Brightwave Ltd            -> Tom Becker   NAMED_ACCOUNT_SIGNOFF, LATE_STAGE_DEAL_IN_FLIGHT
  P2  A102  Ledgerly                  -> Priya Shah   ready
  P2  A103  Medcore Health            -> Sam Ortiz    ready
  P2  A107  Halcyon Pay               -> -            NO_ELIGIBLE_REP
  P3  A101  Brightwave GmbH           -> Tom Becker   FAMILY_OVER_CAPACITY
  P3  A104  Forgeline Manufacturing   -> Priya Shah   ready
  ??  A105  Quillstack                -> -            UNRESOLVED_OWNER
Sequence plan: HOLD 2, LET_FINISH 1, UNENROL 1, UNENROL_AND_REENROL 1
Owner updates ready to import: 10 rows. Accounts in review queue: 4.
Outputs written to out/
```

Brightwave Ltd is the account a round robin would hurt most: a named account with a deal in Negotiation closing on 20 November and a legal call booked for 6 November. The agent proposes Tom, who knows SaaS, but holds it for manager sign-off. Its German subsidiary would follow Tom too, which puts him over capacity, so that is a decision for a human, not the script.

## More

- [SETUP.md](SETUP.md): install, API key, exporting the five files from HubSpot or Salesforce, extending with Cursor, Claude Code, Codex CLI or Grok, and troubleshooting.
- [docs/DETAILS.md](docs/DETAILS.md): cost note, stack-swap table, customising the policy file, and how this runs today versus what production would add.
- [docs/HANDOFF_PATTERN.md](docs/HANDOFF_PATTERN.md): the transferable GTM Engineering pattern behind it (scope, rank, constrained match, split safe from risky, sequence the change) and why each rule exists.
