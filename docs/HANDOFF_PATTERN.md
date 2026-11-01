# The pattern: plan the change, then make it

A rep leaving looks like a field update. It is actually a change to live work, and the GTM Engineering pattern for live changes has five steps. You can reuse them for territory carves, team mergers, segment changes and CRM migrations.

## 1. Scope with resolved identities

Before you can move a book, you have to know what is in it. Exports write the same owner as a name, an email, an email prefix or an initial, so the first job is an identity map (`OwnerResolver`). Anything the map cannot place goes to review. Guessing is how accounts vanish from a book.

Scope also includes work that does not sit on the departing rep's accounts: a deal they own on someone else's account. That work follows the account owner, and the account does not move.

## 2. Rank by cost of a bad handoff

Not every account needs the same care. The score asks one question: if this handoff goes badly this month, how much does it hurt? A named account with a deal in Negotiation and a call on Thursday scores high. A tier C account with no activity since March scores low. Priority decides two things: who gets matched first, and who gets a written brief.

## 3. Match under constraints, not in rotation

Round robin is fair to reps and blind to customers. The matcher separates hard rules (region, segment, active status, free capacity) from soft preferences (industry fit, previous ownership). Hard rules filter, soft ones rank. Accounts are matched highest priority first so the best fit goes where it matters most, and account families stay together because a customer with two owners gets two different stories.

## 4. Split safe from risky

Every change lands in one of two files. The import file holds only changes a sales manager would approve without reading them. The review queue holds the rest with a reason code and what to check: named accounts, late-stage deals closing inside the window, no eligible rep, family over capacity, unknown owner. The rule of thumb: if getting it wrong would be noticed by a customer, a human decides.

## 5. Sequence the change itself

Order matters. Sequences send from the enroller's inbox, so changing the owner first leaves emails going out from a mailbox nobody reads. The plan says: clear review, handle sequences, import companies then deals then tasks, send intros, and deactivate the user last. Each sequence row gets an action and a deadline the day before its next send.

## Why each review rule exists

| Code | Why a human decides |
|---|---|
| NAMED_ACCOUNT_SIGNOFF | Named accounts carry relationships the CRM does not record |
| LATE_STAGE_DEAL_IN_FLIGHT | A new face in the last two weeks of a deal can lose it; the manager stays on until close |
| NO_ELIGIBLE_REP | Coverage gap; a script should not pick someone outside the region |
| FAMILY_OVER_CAPACITY | Keeping a family together versus protecting a rep's load is a trade-off |
| UNRESOLVED_OWNER | The record might belong to the departing book, or might not |
