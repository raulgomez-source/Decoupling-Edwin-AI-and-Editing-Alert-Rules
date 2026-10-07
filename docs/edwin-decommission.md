# Final step: turn off Edwin AI

Once the alert rules route straight to the right escalation chains (README steps 1–6), Edwin AI
is no longer in the notification path. This last phase turns off what's left of it: the Edwin
**actions**, **rules** and **models**, and then (probably) **event ingestion** from the
LogicMonitor portal into Edwin.

> This step is manual and the script in this repo does not do it.
> **Disable** everything; don't delete anything until the client signs off. That way, rolling
> back just means turning things back on.

## Before you start

Don't begin until all of these are true:

- [ ] README step 6 (verify) shows `rows with changes: 0` and `errors: 0`.
- [ ] A soak period agreed with the client (for example, 1–2 weeks) has passed with the new
      escalation chains in place.
- [ ] During the soak, every expected incident or notification arrived through the LogicMonitor
      escalation chains, with nothing missing.
- [ ] Any duplicates (the same issue raised by both LogicMonitor and Edwin) were found and
      explained. Duplicates are expected until Edwin's actions are turned off.
- [ ] The client approved turning off Edwin. Write down who approved it and when.

## 1. Record the current state

Before changing anything, export or screenshot the Edwin configuration so you can restore it
exactly. Save everything under `logs/edwin_<yyyyMMdd>/`:

- Every **action** and **integration** (ServiceNow, email, webhook, etc.): name, target, and
  whether it's enabled.
- Every **rule** (correlation, enrichment, suppression/filter, routing): name, order, and whether
  it's enabled.
- Every **model** (correlation/clustering): name, settings, and whether it's enabled.
- How **ingestion** is set up on the LogicMonitor side: the integration, connector or webhook
  that sends alerts to Edwin, plus any escalation chain or alert rule that points at it.
- How many Edwin insights/incidents are still **open**, and which ServiceNow tickets they link to.

## 2. Turn off Edwin actions

Do this first. Actions are what create tickets and send notifications, so turning them off
removes duplicate incidents right away and has the largest visible effect.

- [ ] Disable every outbound Edwin action/integration for the portal (for example, ServiceNow
      incident creation).
- [ ] Make sure no new Edwin-created tickets show up in ServiceNow for at least one normal
      alert cycle.
- [ ] Make sure the tickets coming from the LogicMonitor escalation chains keep arriving.

## 3. Turn off Edwin rules

- [ ] Disable the Edwin rules (correlation, enrichment, suppression, routing).
- [ ] Note any rule that was enriching data the client still relies on (for example, CMDB or
      assignment-group lookups). If LogicMonitor's escalation chains need that data, add it on
      the LogicMonitor/ServiceNow side before going further.

## 4. Turn off Edwin models

- [ ] Disable the correlation/clustering models.
- [ ] Make sure no new insights are being created in Edwin.

## 5. Stop event ingestion from the portal (recommended)

With nothing downstream using the events, stop sending them to Edwin.

- [ ] Before cutting ingestion, close or hand off any Edwin insights/incidents that are still
      open. Once ingestion stops, Edwin never receives the "alert cleared" events, so any tickets
      it opened will not auto-resolve.
- [ ] In the LogicMonitor portal, disable the Edwin AI integration/connector or
      webhook that sends alerts to Edwin.
- [ ] If an escalation chain or alert rule exists only to forward alerts to Edwin, disable it
      too. Don't touch the 7 out-of-scope rules in `input/alert_rules_out_of_scope.txt`.
- [ ] Check in Edwin that no new events are coming in from the portal.
- [ ] Run README step 3 (dry run) one more time to confirm the alert rules haven't changed:
      `rows with changes: 0`.

## 6. Wrap up

- [ ] Tell the client that Edwin is turned off, and give them the date and a short summary of
      what was disabled.
- [ ] Agree on how long to keep the disabled configuration before deleting it, or whether to
      keep it at all.
- [ ] If Edwin licensing for the portal is no longer needed, raise it with the account owner.

## Rollback

Re-enable things in the reverse order: **ingestion → models → rules → actions**. Turn actions
back on last so Edwin doesn't open tickets before correlation is working again. Use the
snapshot from step 1 as the reference. If the alert rules also need to be reverted, follow the
README's Rollback section.
