# Full run: 100,000 contacts, 14 simulated days

Command: `python scripts/full_run.py --n 100000 --days 14 --no-ai`  
Machine: Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 186 Stepping 2, GenuineIntel, 12 logical CPUs, Python 3.12.7, SQLite 3.45.3  
AI: off (static/committed templates + rules)

## Time taken

| Phase | Wall time |
|---|---|
| Seed 100,000 contacts with history | 33.3s |
| Advance 14 days (336 hourly cycles) | 486.5s (34.75s per simulated day) |
| Verify (12 invariant checks) | 4.0s |

## Final counts

| Status | Contacts | Share |
|---|---:|---:|
| New | 40,000 | 40.0% |
| First sent | 9,112 | 9.1% |
| Follow-up 1 sent | 4,395 | 4.4% |
| Follow-up 2 sent | 5,573 | 5.6% |
| Follow-up 3 sent | 5,617 | 5.6% |
| Replied | 8,551 | 8.6% |
| Bounced | 4,597 | 4.6% |
| Unsubscribed | 3,308 | 3.3% |
| Deactivated | 18,847 | 18.8% |
| **Total** | **100,000** | |

- Emails sent during the run: **29,504** (plus 133,537 imported history sends)
- Replies 551 · out-of-office 248 (sequence continued) · unsubscribed 308 · hard bounces 597 · soft bounces 465 · put on hold for review 160 · deactivated 13,847
- Reply rate 16.9% (9,568 of 56,609 contacted, OOO excluded) · bounce rate 2.0% over the run
- Open review items: 160 · on hold: 160 · suppression list: 12,213
- Paused mailboxes at the end: 7 of 20
- Classifier: rule 9,729, llm 0, fallback 87, human 0, pending 0; accuracy vs ground truth 98.4% of 937
- LLM calls 0: ok 0, 429s 0, answered by a fallback model 0

## Verify: PASS (12/12)

- ✅ Status matches the event history
- ✅ Step equals emails actually sent
- ✅ No step sent twice
- ✅ Nothing sent after suppression
- ✅ Stopped contacts have no next action
- ✅ Follow-ups respect the waits
- ✅ Sends inside the prospect's window
- ✅ Mailboxes within warm-up limits
- ✅ Recipient domains within daily cap
- ✅ Nothing sent while on hold
- ✅ Every arrived reply/bounce was processed
- ✅ No contact lost or stuck

## Sends per simulated day

| Day (IST) | Sent | Replies | OOO | Bounces | Deactivated | Wall time |
|---|---:|---:|---:|---:|---:|---:|
| 2026-10-02 | 2,672 | 6 | 3 | 60 | 2,534 | 12.7s |
| 2026-10-03 | 1,458 | 15 | 9 | 36 | 799 | 11.9s |
| 2026-10-04 | 1,864 | 14 | 10 | 41 | 767 | 16.1s |
| 2026-10-05 | 1,780 | 31 | 10 | 36 | 798 | 16.4s |
| 2026-10-06 | 2,031 | 39 | 15 | 34 | 750 | 19.9s |
| 2026-10-07 | 2,098 | 32 | 19 | 47 | 785 | 25.8s |
| 2026-10-08 | 2,782 | 51 | 30 | 46 | 567 | 42.4s |
| 2026-10-09 | 1,560 | 48 | 23 | 42 | 2,008 | 37.1s |
| 2026-10-10 | 2,128 | 48 | 20 | 40 | 752 | 44.9s |
| 2026-10-11 | 2,208 | 50 | 27 | 47 | 877 | 52.1s |
| 2026-10-12 | 2,232 | 61 | 18 | 39 | 847 | 53.6s |
| 2026-10-13 | 2,340 | 53 | 25 | 43 | 925 | 49.0s |
| 2026-10-14 | 2,097 | 46 | 15 | 34 | 810 | 48.4s |
| 2026-10-15 | 2,254 | 57 | 24 | 52 | 628 | 56.0s |
