#!/usr/bin/env python3
"""Book of Business Transfer Agent.

Plans the handoff of a departing rep's book: accounts, open deals, open tasks
and active sequence enrollments. Resolves messy owner values, ranks accounts
by how much a bad handoff would hurt, matches each one to a receiving rep on
region, segment, industry, relationship history, account family and capacity
(not round robin), and splits the plan into a safe owner-update import, a
review queue, a sequence re-enrol plan and handoff briefs.

Runs on rules with no API key. With ANTHROPIC_API_KEY set, Claude writes the
summary and the customer intro drafts from the plan only. It never writes to
your CRM.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional
    pass

HERE = Path(__file__).parent


# ---------- loading and normalising ----------

def load_csv(path: Path | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def parse_date(value: str) -> date | None:
    """Accepts ISO dates (with or without a time) and slash dates. A slash date
    with a part above 12 is unambiguous (28/09 is UK, 10/30 is US). When both
    parts are 12 or below it is read day first, the UK default."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    day, month = (b, a) if b > 12 else (a, b)
    try:
        return date(y, month, day)
    except ValueError:
        return None


def norm_key(value: str) -> str:
    return " ".join((value or "").casefold().split())


def norm_list(value: str, aliases: dict) -> list[str]:
    out = []
    for part in re.split(r"[;,|]", value or ""):
        k = norm_key(part)
        if k:
            out.append(aliases.get(k, part.strip()))
    return out


@dataclass
class Rep:
    rep_id: str
    name: str
    email: str
    status: str
    region: str
    segment: str
    industries: list[str]
    max_accounts: int
    current_accounts: int
    manager_email: str
    crm_user_id: str = ""
    assigned: int = 0

    @property
    def headroom(self) -> int:
        return self.max_accounts - self.current_accounts - self.assigned


@dataclass
class Account:
    account_id: str
    name: str
    raw_owner: str
    owner_id: str | None
    region: str | None
    segment: str
    industry: str
    tier: str
    parent_id: str
    previous_owner_id: str | None
    last_activity: date | None
    renewal: date | None
    named: bool
    deals: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    sequences: list[dict] = field(default_factory=list)
    score: int = 0
    priority: str = "P3"
    reasons: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    new_owner: Rep | None = None
    match_note: str = ""


def build_reps(rows: list[dict], policy: dict) -> list[Rep]:
    reps = []
    for r in rows:
        reps.append(Rep(
            rep_id=r["rep_id"], name=" ".join(r["name"].split()), email=r["email"].lower(),
            status=norm_key(r["status"]),
            region=policy["region_aliases"].get(norm_key(r["region"]), r["region"].strip()),
            segment=policy["segment_aliases"].get(norm_key(r["segment"]), r["segment"].strip()),
            industries=norm_list(r.get("industries", ""), policy["industry_aliases"]),
            max_accounts=int(r.get("max_accounts") or 0), current_accounts=int(r.get("current_accounts") or 0),
            manager_email=r.get("manager_email", "").lower(), crm_user_id=r.get("crm_user_id", "")))
    return reps


class OwnerResolver:
    """Maps the many ways a CRM export writes an owner (name, email, email
    local part, initials alias, odd casing) to one rep_id."""

    def __init__(self, reps: list[Rep], aliases: dict):
        self.map = {}
        for r in reps:
            self.map[norm_key(r.name)] = r.rep_id
            self.map[r.email] = r.rep_id
            self.map[r.email.split("@")[0]] = r.rep_id
            self.map[r.rep_id.casefold()] = r.rep_id
            if r.crm_user_id:
                self.map[r.crm_user_id] = r.rep_id
        for k, v in aliases.items():
            self.map[norm_key(k)] = v

    def __call__(self, raw: str) -> str | None:
        return self.map.get(norm_key(raw))


def segment_for(employees: str, policy: dict) -> str:
    try:
        n = int(float(employees))
    except ValueError:
        n = 0
    for floor, seg in policy["segment_by_employees"]:
        if n >= floor:
            return seg
    return policy["segment_by_employees"][-1][1]


def build_accounts(rows, resolve, policy) -> dict[str, Account]:
    accts = {}
    for r in rows:
        ind = norm_list(r.get("industry", ""), policy["industry_aliases"])
        accts[r["account_id"]] = Account(
            account_id=r["account_id"], name=r["account_name"], raw_owner=r["owner"], owner_id=resolve(r["owner"]),
            region=policy["country_region"].get(norm_key(r.get("country", ""))),
            segment=segment_for(r.get("employees", ""), policy), industry=ind[0] if ind else "",
            tier=(r.get("tier") or "C").strip().upper()[:1], parent_id=r.get("parent_account_id", ""),
            previous_owner_id=resolve(r.get("previous_owner", "")) if r.get("previous_owner") else None,
            last_activity=parse_date(r.get("last_activity_date", "")), renewal=parse_date(r.get("renewal_date", "")),
            named=norm_key(r.get("named_account", "")) in {"yes", "y", "true", "1"})
    return accts


# ---------- scoping the book ----------

def departing_ids(reps: list[Rep], policy: dict, explicit: str | None, resolve) -> set[str]:
    if explicit:
        rid = resolve(explicit)
        if not rid:
            raise SystemExit(f"Could not match --departing '{explicit}' to any rep.")
        return {rid}
    return {r.rep_id for r in reps if r.status in policy["departing_statuses"]}


def attach(accts, deals, tasks, seqs, resolve, leaving: set[str]):
    """Attach the departing rep's open work to accounts. Work owned by the
    departing rep on an account someone else owns is a cross-owned item: it
    follows the account owner, the account itself does not move."""
    cross = []
    for kind, rows, owner_col in (("deal", deals, "owner"), ("task", tasks, "owner"), ("sequence", seqs, "enrolled_by")):
        for row in rows:
            row = dict(row)
            row["_owner_id"] = resolve(row.get(owner_col, ""))
            if row["_owner_id"] not in leaving:
                continue
            a = accts.get(row.get("account_id", ""))
            if a is None:
                cross.append((kind, row, None))
                continue
            getattr(a, kind + "s").append(row)
            if a.owner_id not in leaving and a.owner_id is not None:
                cross.append((kind, row, a))
    return cross


# ---------- priority ----------

def prioritise(a: Account, policy: dict, as_of: date) -> None:
    w, win = policy["priority_weights"], policy["windows_days"]
    late = set(policy["late_stages"])
    s, why = w["tier"].get(a.tier, 1), [f"tier {a.tier}"]
    if a.named:
        s += w["named_account"]; why.append("named account")
    if any(norm_key(d["stage"]) in late for d in a.deals):
        s += w["late_stage_deal"]; why.append("late-stage deal")
    elif a.deals:
        s += w["any_open_deal"]; why.append("open deal")
    if any((cd := parse_date(d.get("close_date", ""))) and as_of <= cd <= as_of + timedelta(days=win["deal_closing_soon"]) for d in a.deals):
        s += w["deal_closing_soon"]; why.append("deal closes within %d days" % win["deal_closing_soon"])
    if a.renewal and as_of <= a.renewal <= as_of + timedelta(days=win["renewal_soon"]):
        s += w["renewal_soon"]; why.append("renewal within %d days" % win["renewal_soon"])
    if any(norm_key(t.get("type", "")) == "meeting" and (dd := parse_date(t.get("due_date", ""))) and as_of <= dd <= as_of + timedelta(days=win["meeting_soon"]) for t in a.tasks):
        s += w["meeting_soon"]; why.append("customer meeting booked")
    if any(norm_key(q.get("status", "")) == "active" for q in a.sequences):
        s += w["active_sequence"]; why.append("active sequence")
    if a.last_activity and (as_of - a.last_activity).days > win["gone_quiet"]:
        s += w["gone_quiet"]; why.append("no activity in %d+ days" % win["gone_quiet"])
    a.score, a.reasons = s, why
    bands = policy["priority_bands"]
    a.priority = "P1" if s >= bands["P1"] else "P2" if s >= bands["P2"] else "P3"


# ---------- matching ----------

def candidates(a: Account, reps: list[Rep], policy: dict, leaving: set[str]) -> list[Rep]:
    return [r for r in reps if r.status in policy["eligible_statuses"] and r.rep_id not in leaving
            and r.region == a.region and r.segment == a.segment]


def match_score(a: Account, r: Rep, policy: dict) -> tuple[float, list[str]]:
    w = policy["match_weights"]
    s, why = 0.0, []
    if a.industry and a.industry in r.industries:
        s += w["industry_fit"]; why.append(f"{a.industry} experience")
    if a.previous_owner_id == r.rep_id:
        s += w["previous_owner"]; why.append("owned this account before")
    s += w["headroom"] * max(r.headroom, 0) / max(r.max_accounts, 1)
    why.append(f"free slots before this: {r.headroom}")
    return s, why


def assign(accts: list[Account], all_accts: dict[str, Account], reps: list[Rep], policy: dict, leaving: set[str]) -> None:
    """Highest priority first, so the strongest fit goes to the accounts where a
    bad handoff costs most. Account families stay with one owner."""
    by_id = {r.rep_id: r for r in reps}
    order = sorted(accts, key=lambda a: (-a.score, bool(a.parent_id), a.account_id))
    for a in order:
        if a.owner_id is None:
            continue
        family_owner = None
        parent = all_accts.get(a.parent_id)
        if parent is not None:
            family_owner = parent.new_owner if parent.owner_id in leaving else by_id.get(parent.owner_id or "")
        if family_owner is not None:
            a.new_owner = family_owner
            if family_owner.headroom <= 0:
                a.review.append("FAMILY_OVER_CAPACITY")
                a.match_note = f"follows parent {parent.name} to {family_owner.name}, who has no free slots"
                continue
            family_owner.assigned += 1
            a.match_note = f"follows parent {parent.name}"
            continue
        pool = [r for r in candidates(a, reps, policy, leaving) if r.headroom > 0]
        if not pool:
            a.review.append("NO_ELIGIBLE_REP")
            a.match_note = f"no active {a.segment} rep with free slots covers region {a.region or 'unknown'}"
            continue
        scored = sorted(((match_score(a, r, policy), r) for r in pool), key=lambda x: (-x[0][0], x[1].rep_id))
        (s, why), best = scored[0]
        best.assigned += 1
        a.new_owner = best
        a.match_note = ", ".join(why)


def review_rules(a: Account, policy: dict, as_of: date) -> None:
    if a.named and policy.get("named_accounts_need_signoff", True):
        a.review.append("NAMED_ACCOUNT_SIGNOFF")
    late = set(policy["late_stages"])
    window = as_of + timedelta(days=policy["late_stage_close_window_days"])
    for d in a.deals:
        cd = parse_date(d.get("close_date", ""))
        if norm_key(d["stage"]) in late and cd and as_of <= cd <= window:
            a.review.append("LATE_STAGE_DEAL_IN_FLIGHT")
            break


def deal_flags(d: dict, as_of: date) -> list[str]:
    flags = []
    cd = parse_date(d.get("close_date", ""))
    if cd and cd < as_of:
        flags.append("close date in the past")
    if not d.get("next_step"):
        flags.append("no next step")
    return flags


def task_flags(t: dict, as_of: date) -> list[str]:
    dd = parse_date(t.get("due_date", ""))
    flags = []
    if dd and dd < as_of:
        flags.append("overdue")
    if norm_key(t.get("type", "")) == "meeting":
        flags.append("calendar invite sits on the departing rep's calendar; re-send from new owner")
    return flags


def sequence_action(q: dict, new_owner: Rep | None, as_of: date) -> dict:
    """Sequences send from the inbox of whoever enrolled the contact, and the
    sender cannot be changed mid-sequence, so changing the owner is not enough."""
    status = norm_key(q.get("status", ""))
    step, total = int(q.get("current_step") or 0), int(q.get("total_steps") or 0)
    nxt = parse_date(q.get("next_step_date", ""))
    base = {"contact_email": q["contact_email"].lower(), "account_id": q["account_id"], "sequence_name": q["sequence_name"],
            "current_step": step, "total_steps": total, "new_owner": new_owner.email if new_owner else ""}
    if new_owner is None:
        return {**base, "action": "HOLD", "restart_step": step + 1 if step < total else "",
                "deadline": ((nxt - timedelta(days=1)) if nxt and nxt > as_of else as_of).isoformat(),
                "why": "account is in the review queue; unenrol before the next send and re-enrol once the owner is confirmed"}
    if status == "paused":
        return {**base, "action": "UNENROL", "restart_step": "", "deadline": as_of.isoformat(),
                "why": "paused; new owner decides whether to restart"}
    if step >= total:
        return {**base, "action": "LET_FINISH", "restart_step": "", "deadline": (nxt or as_of).isoformat(),
                "why": "last step; keep the departing inbox connected and forwarding until it sends"}
    return {**base, "action": "UNENROL_AND_REENROL", "restart_step": step + 1,
            "deadline": ((nxt - timedelta(days=1)) if nxt and nxt > as_of else as_of).isoformat(),
            "why": f"re-enrol under {new_owner.name} at step {step + 1} before the next send"}


# ---------- writing ----------

def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


REVIEW_HELP = {
    "NAMED_ACCOUNT_SIGNOFF": "Named account: manager confirms the proposed owner and joins the intro call.",
    "LATE_STAGE_DEAL_IN_FLIGHT": "Deal in late stage closes within the window: keep the manager on the deal until it closes and brief the new owner before the next customer meeting.",
    "NO_ELIGIBLE_REP": "No eligible rep for this region and segment: pick an owner by hand or open coverage with the manager.",
    "FAMILY_OVER_CAPACITY": "Keeping the account family together puts the proposed owner over capacity: approve the overload or move the whole family.",
    "UNRESOLVED_OWNER": "Owner value matches no rep: check whether this record belongs to the departing book.",
}


def llm_text(payload: dict) -> dict | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = ("You are a RevOps lead writing a rep-departure handoff. Use ONLY the facts in this JSON. Do not invent "
                  "numbers, names or history. Return JSON with keys 'summary' (3 to 5 plain sentences, no emojis) and "
                  "'intros' (object of account_id to a 4 to 6 sentence intro email from the new owner to the customer that "
                  "references the open work, does not blame anyone and proposes one next step).\n\n" + json.dumps(payload, default=str))
        msg = client.messages.create(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"), max_tokens=1500,
                                     messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception as e:  # never let the optional step break the plan
        print(f"  Claude step skipped ({type(e).__name__}); using templates.")
        return None


def template_intro(a: Account, departing_name: str) -> str:
    o = a.new_owner
    nxt = next((d for d in a.deals if d.get("next_step")), None)
    line = (f"I have read through where things stand on {nxt['deal_name']}, including the next step: "
            f"{nxt['next_step'][0].lower() + nxt['next_step'][1:]}.") if nxt else "I have read through your account history so you will not need to repeat it."
    return (f"Hi, I am {o.name}, and from this month I am your account contact at Northbeam, taking over from {departing_name}. "
            f"{line} Could we take 20 minutes this week so I can confirm priorities and dates with you? "
            f"{departing_name.split()[0]} has shared the context with me, and nothing you agreed changes.")


def run(paths: dict, policy_path: Path, out_dir: Path, as_of: date | None, departing: str | None) -> dict:
    policy = json.loads(Path(policy_path).read_text())
    as_of = as_of or parse_date(policy.get("as_of", "")) or date.today()
    reps = build_reps(load_csv(paths["reps"]), policy)
    resolve = OwnerResolver(reps, policy.get("owner_aliases", {}))
    leaving = departing_ids(reps, policy, departing, resolve)
    if not leaving:
        raise SystemExit("No departing rep found. Set status to Departing in the reps file or pass --departing.")
    by_id = {r.rep_id: r for r in reps}
    accts = build_accounts(load_csv(paths["accounts"]), resolve, policy)
    cross = attach(accts, load_csv(paths["deals"]), load_csv(paths["tasks"]), load_csv(paths["sequences"]), resolve, leaving)

    book = [a for a in accts.values() if a.owner_id in leaving]
    unresolved = [a for a in accts.values() if a.owner_id is None]
    for a in unresolved:
        a.review.append("UNRESOLVED_OWNER")
        a.match_note = f"owner '{a.raw_owner}' matches no rep"
    for a in book:
        prioritise(a, policy, as_of)
        review_rules(a, policy, as_of)
    assign(book, accts, reps, policy, leaving)

    out_dir.mkdir(parents=True, exist_ok=True)
    imports, review, seq_plan = [], [], []
    for a in sorted(book, key=lambda x: (-x.score, x.account_id)):
        safe = not a.review and a.new_owner is not None
        if safe:
            imports.append({"object_type": "company", "record_id": a.account_id, "record_name": a.name, "account_id": a.account_id,
                            "current_owner": a.raw_owner, "new_owner_email": a.new_owner.email, "new_owner_id": a.new_owner.crm_user_id, "priority": a.priority, "flags": ""})
            for d in a.deals:
                imports.append({"object_type": "deal", "record_id": d["deal_id"], "record_name": d["deal_name"], "account_id": a.account_id,
                                "current_owner": d["owner"], "new_owner_email": a.new_owner.email, "new_owner_id": a.new_owner.crm_user_id, "priority": a.priority,
                                "flags": "; ".join(deal_flags(d, as_of))})
            for t in a.tasks:
                imports.append({"object_type": "task", "record_id": t["task_id"], "record_name": t["subject"], "account_id": a.account_id,
                                "current_owner": t["owner"], "new_owner_email": a.new_owner.email, "new_owner_id": a.new_owner.crm_user_id, "priority": a.priority,
                                "flags": "; ".join(task_flags(t, as_of))})
        else:
            review.append({"account_id": a.account_id, "account_name": a.name, "priority": a.priority, "score": a.score,
                           "reason_codes": "; ".join(dict.fromkeys(a.review)),
                           "proposed_owner": a.new_owner.email if a.new_owner else "",
                           "open_deals": len(a.deals), "open_tasks": len(a.tasks), "match_note": a.match_note,
                           "what_to_check": " ".join(REVIEW_HELP[c] for c in dict.fromkeys(a.review))})
        for q in a.sequences:
            seq_plan.append(sequence_action(q, a.new_owner if safe else None, as_of))
    for a in unresolved:
        review.append({"account_id": a.account_id, "account_name": a.name, "priority": "", "score": "",
                       "reason_codes": "UNRESOLVED_OWNER", "proposed_owner": "", "open_deals": len(a.deals), "open_tasks": len(a.tasks),
                       "match_note": a.match_note, "what_to_check": REVIEW_HELP["UNRESOLVED_OWNER"]})
    # Cross-owned work: the departing rep owns the deal or task, someone else owns the account.
    for kind, row, a in cross:
        if a is None or kind == "sequence":
            continue
        owner = by_id.get(a.owner_id)
        flags = deal_flags(row, as_of) if kind == "deal" else task_flags(row, as_of)
        imports.append({"object_type": kind, "record_id": row["deal_id"] if kind == "deal" else row["task_id"],
                        "record_name": row["deal_name"] if kind == "deal" else row["subject"], "account_id": a.account_id,
                        "current_owner": row.get("owner"), "new_owner_email": owner.email if owner else "", "new_owner_id": owner.crm_user_id if owner else "",
                        "priority": "", "flags": "; ".join(["cross-owned: follows account owner"] + flags)})

    fields = ["object_type", "record_id", "record_name", "account_id", "current_owner", "new_owner_email", "new_owner_id", "priority", "flags"]
    write_csv(out_dir / "owner_update_import.csv", imports, fields)
    write_csv(out_dir / "review_queue.csv", review, ["account_id", "account_name", "priority", "score", "reason_codes", "proposed_owner",
                                                     "open_deals", "open_tasks", "match_note", "what_to_check"])
    write_csv(out_dir / "sequence_plan.csv", seq_plan, ["contact_email", "account_id", "sequence_name", "action", "current_step",
                                                        "total_steps", "restart_step", "new_owner", "deadline", "why"])

    departing_name = " and ".join(by_id[r].name for r in sorted(leaving))
    load = Counter(a.new_owner.name for a in book if a.new_owner and not a.review)
    payload = {"departing": departing_name, "as_of": as_of, "accounts": [
        {"account_id": a.account_id, "name": a.name, "priority": a.priority, "why": a.reasons, "new_owner": a.new_owner.name if a.new_owner else None,
         "review": a.review, "deals": [{k: d.get(k) for k in ("deal_name", "stage", "close_date", "next_step")} for d in a.deals]}
        for a in book]}
    llm = llm_text(payload)
    counts = Counter(a.priority for a in book)
    summary = (llm or {}).get("summary") or (
        f"{departing_name}'s book has {len(book)} accounts ({counts['P1']} P1, {counts['P2']} P2, {counts['P3']} P3), "
        f"{sum(len(a.deals) for a in book)} open deals, {sum(len(a.tasks) for a in book)} open tasks and "
        f"{sum(len(a.sequences) for a in book)} sequence enrollments. "
        f"{sum(1 for r in imports if r['object_type'] == 'company')} accounts can move now from the import file; "
        f"{len(review)} need a human decision first. Do not deactivate the user until every sequence row is done.")
    intros = (llm or {}).get("intros") or {}

    lines = [f"# Handoff plan: {departing_name}", "", f"As of {as_of.isoformat()}.", "", summary, "",
             "## Load on receiving reps", "", "| Rep | Accounts added | Slots left |", "|---|---|---|"]
    for r in reps:
        if r.assigned:
            lines.append(f"| {r.name} | {r.assigned} | {r.headroom} |")
    lines += ["", "## Review queue", ""]
    lines += [f"- **{x['account_name']}** ({x['priority'] or 'unknown'}): {x['reason_codes']}. {x['match_note']}." for x in review] or ["- Nothing to review."]
    lines += ["", "## Order of operations", "",
              "1. Clear the review queue with the sales manager.",
              "2. Work through `sequence_plan.csv` before anything else changes, so no email sends from an unwatched inbox.",
              "3. Import `owner_update_import.csv`: companies first, then deals, then tasks. Test five rows first.",
              "4. Send the intro drafts in `handoff_briefs.md` from each new owner, P1 first.",
              "5. Deactivate the user only after the last LET_FINISH sequence step has sent."]
    (out_dir / "transition_brief.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    briefs = [f"# Handoff briefs: {departing_name}", ""]
    for a in sorted(book, key=lambda x: (-x.score, x.account_id)):
        if a.priority == "P3" and not a.review:
            continue
        briefs += [f"## {a.name} ({a.priority}, score {a.score})", "",
                   f"- New owner: {a.new_owner.name if a.new_owner else 'not decided (see review queue)'} ({a.match_note})",
                   f"- Why this priority: {', '.join(a.reasons)}"]
        if a.review:
            briefs.append(f"- Needs review: {', '.join(dict.fromkeys(a.review))}")
        for d in a.deals:
            fl = deal_flags(d, as_of)
            briefs.append(f"- Deal: {d['deal_name']}, {d['stage']}, closes {parse_date(d.get('close_date', '')) or d.get('close_date') or 'no date'}, next step: {d.get('next_step') or 'none recorded'}"
                          + (f" [{', '.join(fl)}]" if fl else ""))
        for t in a.tasks:
            fl = task_flags(t, as_of)
            briefs.append(f"- Task: {t['subject']} due {parse_date(t.get('due_date', '')) or t.get('due_date') or 'no date'}" + (f" [{', '.join(fl)}]" if fl else ""))
        if a.new_owner:
            briefs += ["", "Intro draft (review before sending):", "", "> " + (intros.get(a.account_id) or template_intro(a, departing_name))]
        briefs.append("")
    (out_dir / "handoff_briefs.md").write_text("\n".join(briefs), encoding="utf-8")
    return {"book": book, "imports": imports, "review": review, "sequences": seq_plan, "load": load, "llm": bool(llm)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--reps", default=HERE / "sample_reps.csv", type=Path)
    p.add_argument("--accounts", default=HERE / "sample_accounts.csv", type=Path)
    p.add_argument("--deals", default=HERE / "sample_open_deals.csv", type=Path)
    p.add_argument("--tasks", default=HERE / "sample_open_tasks.csv", type=Path)
    p.add_argument("--sequences", default=HERE / "sample_sequence_enrollments.csv", type=Path)
    p.add_argument("--policy", default=HERE / "transfer_policy.json", type=Path)
    p.add_argument("--departing", help="rep id, name or email (default: reps with status Departing)")
    p.add_argument("--as-of", help="YYYY-MM-DD or 'now' (default: policy as_of, for repeatable sample runs)")
    p.add_argument("--out", default=Path("out"), type=Path)
    a = p.parse_args(argv)
    as_of = date.today() if a.as_of == "now" else parse_date(a.as_of) if a.as_of else None
    mode = "Claude for summary and intros" if os.getenv("ANTHROPIC_API_KEY") else "rules only (no API key)"
    paths = {"reps": a.reps, "accounts": a.accounts, "deals": a.deals, "tasks": a.tasks, "sequences": a.sequences}
    res = run(paths, a.policy, a.out, as_of, a.departing)
    print(f"Book loaded: {len(res['book'])} accounts. Mode: {mode}")
    for acc in sorted(res["book"], key=lambda x: (-x.score, x.account_id)):
        owner = acc.new_owner.name if acc.new_owner else "-"
        status = ", ".join(dict.fromkeys(acc.review)) or "ready"
        print(f"  {acc.priority}  {acc.account_id}  {acc.name[:24]:<24}  -> {owner:<11}  {status}")
    for r in res["review"]:
        if r["reason_codes"] == "UNRESOLVED_OWNER":
            print(f"  ??  {r['account_id']}  {r['account_name'][:24]:<24}  -> -            UNRESOLVED_OWNER")
    acts = Counter(s["action"] for s in res["sequences"])
    print("Sequence plan: " + ", ".join(f"{k} {v}" for k, v in sorted(acts.items())))
    print(f"Owner updates ready to import: {len(res['imports'])} rows. Accounts in review queue: {len(res['review'])}.")
    print(f"Outputs written to {a.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
