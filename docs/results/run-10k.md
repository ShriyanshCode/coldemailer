# Full run: 10,000 contacts, 60 simulated days

Command: `python scripts/full_run.py --n 10000 --days 60`  
Machine: Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 186 Stepping 2, GenuineIntel, 12 logical CPUs, Python 3.12.7, SQLite 3.45.3  
AI: OpenRouter recursive/flint-v1.0:free → nvidia/nemotron-3.5-super-vl:free → poolside/laguna-s-2.1:free

## Time taken

| Phase | Wall time |
|---|---|
| Seed 10,000 contacts with history | 2.8s |
| Advance 60 days (1440 hourly cycles) | 221.6s (3.69s per simulated day) |
| Verify (12 invariant checks) | 1.9s |

## Final counts

| Status | Contacts | Share |
|---|---:|---:|
| New | 28 | 0.3% |
| First sent | 30 | 0.3% |
| Follow-up 1 sent | 34 | 0.3% |
| Follow-up 2 sent | 36 | 0.4% |
| Follow-up 3 sent | 0 | 0.0% |
| Replied | 1,364 | 13.6% |
| Bounced | 778 | 7.8% |
| Unsubscribed | 534 | 5.3% |
| Deactivated | 7,196 | 72.0% |
| **Total** | **10,000** | |

- Emails sent during the run: **20,855** (plus 13,387 imported history sends)
- Replies 564 · out-of-office 251 (sequence continued) · unsubscribed 234 · hard bounces 378 · soft bounces 324 · put on hold for review 128 · deactivated 6,696
- Reply rate 14.3% (1,364 of 9,551 contacted, OOO excluded) · bounce rate 1.8% over the run
- Open review items: 130 · on hold: 128 · suppression list: 1,952
- Paused mailboxes at the end: 7 of 20
- Classifier: rule 1,738, llm 35, fallback 66, human 0, pending 2; accuracy vs ground truth 98.7% of 949
- LLM calls 94: ok 41, 429s 53, answered by a fallback model 7

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
| 2026-10-02 | 2,196 | 10 | 6 | 46 | 260 | 4.9s |
| 2026-10-03 | 882 | 14 | 12 | 19 | 74 | 3.7s |
| 2026-10-04 | 859 | 37 | 17 | 12 | 73 | 12.5s |
| 2026-10-05 | 1,280 | 32 | 18 | 24 | 81 | 17.8s |
| 2026-10-06 | 918 | 28 | 10 | 10 | 78 | 16.0s |
| 2026-10-07 | 1,355 | 39 | 9 | 28 | 81 | 17.3s |
| 2026-10-08 | 969 | 35 | 19 | 19 | 53 | 31.5s |
| 2026-10-09 | 1,235 | 28 | 9 | 31 | 283 | 23.9s |
| 2026-10-10 | 1,406 | 32 | 14 | 29 | 71 | 12.5s |
| 2026-10-11 | 1,214 | 39 | 15 | 27 | 110 | 5.7s |
| 2026-10-12 | 968 | 45 | 17 | 13 | 92 | 4.9s |
| 2026-10-13 | 669 | 35 | 15 | 10 | 74 | 3.9s |
| 2026-10-14 | 1,173 | 23 | 16 | 23 | 74 | 6.8s |
| 2026-10-15 | 757 | 26 | 9 | 19 | 49 | 5.1s |
| 2026-10-16 | 537 | 21 | 4 | 6 | 358 | 4.1s |
| 2026-10-17 | 939 | 22 | 11 | 9 | 119 | 6.4s |
| 2026-10-18 | 627 | 11 | 8 | 11 | 149 | 5.0s |
| 2026-10-19 | 536 | 19 | 8 | 13 | 158 | 4.9s |
| 2026-10-20 | 457 | 15 | 10 | 6 | 92 | 4.2s |
| 2026-10-21 | 273 | 11 | 3 | 2 | 583 | 3.1s |
| 2026-10-22 | 265 | 10 | 0 | 0 | 276 | 3.0s |
| 2026-10-23 | 279 | 6 | 4 | 8 | 240 | 2.9s |
| 2026-10-24 | 292 | 4 | 4 | 3 | 617 | 3.2s |
| 2026-10-25 | 259 | 3 | 3 | 3 | 351 | 2.7s |
| 2026-10-26 | 266 | 8 | 3 | 3 | 249 | 3.0s |
| 2026-10-27 | 197 | 6 | 3 | 4 | 237 | 2.2s |
| 2026-10-28 | 15 | 3 | 3 | 0 | 250 | 0.7s |
| 2026-10-29 | 3 | 1 | 1 | 0 | 256 | 0.5s |
| 2026-10-30 | 6 | 1 | 0 | 0 | 269 | 0.4s |
| 2026-10-31 | 9 | 0 | 0 | 0 | 273 | 0.4s |
| 2026-11-01 | 6 | 0 | 0 | 0 | 242 | 0.3s |
| 2026-11-02 | 2 | 0 | 0 | 0 | 265 | 0.3s |
| 2026-11-03 | 2 | 0 | 0 | 0 | 210 | 0.3s |
| 2026-11-04 | 1 | 0 | 0 | 0 | 17 | 0.2s |
| 2026-11-05 | 0 | 0 | 0 | 0 | 5 | 0.2s |
| 2026-11-06 | 1 | 0 | 0 | 0 | 5 | 0.2s |
| 2026-11-07 | 2 | 0 | 0 | 0 | 6 | 0.3s |
| 2026-11-08 | 0 | 0 | 0 | 0 | 8 | 0.3s |
| 2026-11-09 | 0 | 0 | 0 | 0 | 2 | 0.3s |
| 2026-11-10 | 0 | 0 | 0 | 0 | 2 | 0.3s |
| 2026-11-11 | 0 | 0 | 0 | 0 | 1 | 0.2s |
| 2026-11-12 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-13 | 0 | 0 | 0 | 0 | 1 | 0.2s |
| 2026-11-14 | 0 | 0 | 0 | 0 | 1 | 0.2s |
| 2026-11-15 | 0 | 0 | 0 | 0 | 1 | 0.2s |
| 2026-11-16 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-17 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-18 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-19 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-20 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-21 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-22 | 0 | 0 | 0 | 0 | 0 | 0.3s |
| 2026-11-23 | 0 | 0 | 0 | 0 | 0 | 0.3s |
| 2026-11-24 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-25 | 0 | 0 | 0 | 0 | 0 | 0.3s |
| 2026-11-26 | 0 | 0 | 0 | 0 | 0 | 0.4s |
| 2026-11-27 | 0 | 0 | 0 | 0 | 0 | 0.4s |
| 2026-11-28 | 0 | 0 | 0 | 0 | 0 | 0.4s |
| 2026-11-29 | 0 | 0 | 0 | 0 | 0 | 0.4s |
| 2026-11-30 | 0 | 0 | 0 | 0 | 0 | 0.3s |
