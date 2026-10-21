# Full run: 10,000 contacts, 60 simulated days

Command: `python scripts/full_run.py --n 10000 --days 60`  
Machine: Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 186 Stepping 2, GenuineIntel, 12 logical CPUs, Python 3.12.7, SQLite 3.45.3  
AI: OpenRouter google/gemma-4-31b-it:free → minimax/minimax-m3.1-flash:free → google/gemma-4-26b-a4b-it:free

## Time taken

| Phase | Wall time |
|---|---|
| Seed 10,000 contacts with history | 2.7s |
| Advance 60 days (1440 hourly cycles) | 344.3s (5.74s per simulated day) |
| Verify (12 invariant checks) | 1.0s |

## Final counts

| Status | Contacts | Share |
|---|---:|---:|
| New | 25 | 0.2% |
| First sent | 31 | 0.3% |
| Follow-up 1 sent | 34 | 0.3% |
| Follow-up 2 sent | 38 | 0.4% |
| Follow-up 3 sent | 0 | 0.0% |
| Replied | 1,364 | 13.6% |
| Bounced | 778 | 7.8% |
| Unsubscribed | 534 | 5.3% |
| Deactivated | 7,196 | 72.0% |
| **Total** | **10,000** | |

- Emails sent during the run: **20,866** (plus 13,387 imported history sends)
- Replies 564 · out-of-office 252 (sequence continued) · unsubscribed 234 · hard bounces 378 · soft bounces 324 · put on hold for review 128 · deactivated 6,696
- Reply rate 14.3% (1,364 of 9,554 contacted, OOO excluded) · bounce rate 1.8% over the run
- Open review items: 128 · on hold: 128 · suppression list: 1,949
- Paused mailboxes at the end: 4 of 20
- Classifier: rule 1,738, llm 103, fallback 0, human 0, pending 0; accuracy vs ground truth 97.6% of 951
- LLM calls 121: ok 95, 429s 26, answered by a fallback model 95

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
| 2026-10-02 | 1,710 | 7 | 4 | 32 | 260 | 4.2s |
| 2026-10-03 | 911 | 8 | 10 | 24 | 74 | 3.1s |
| 2026-10-04 | 854 | 30 | 16 | 14 | 73 | 23.5s |
| 2026-10-05 | 790 | 24 | 19 | 16 | 81 | 8.3s |
| 2026-10-06 | 858 | 29 | 8 | 12 | 78 | 24.8s |
| 2026-10-07 | 1,127 | 33 | 9 | 20 | 81 | 13.5s |
| 2026-10-08 | 855 | 22 | 12 | 13 | 53 | 11.9s |
| 2026-10-09 | 993 | 27 | 10 | 21 | 283 | 24.7s |
| 2026-10-10 | 861 | 24 | 9 | 24 | 69 | 9.0s |
| 2026-10-11 | 1,192 | 27 | 9 | 24 | 111 | 19.6s |
| 2026-10-12 | 908 | 33 | 10 | 18 | 91 | 10.9s |
| 2026-10-13 | 750 | 21 | 16 | 21 | 74 | 13.4s |
| 2026-10-14 | 1,214 | 24 | 13 | 15 | 74 | 20.1s |
| 2026-10-15 | 924 | 28 | 11 | 19 | 49 | 9.1s |
| 2026-10-16 | 662 | 26 | 9 | 7 | 358 | 12.6s |
| 2026-10-17 | 719 | 27 | 11 | 10 | 119 | 22.4s |
| 2026-10-18 | 916 | 39 | 12 | 12 | 149 | 22.3s |
| 2026-10-19 | 746 | 26 | 11 | 19 | 157 | 18.1s |
| 2026-10-20 | 782 | 11 | 13 | 14 | 92 | 12.5s |
| 2026-10-21 | 358 | 21 | 5 | 5 | 586 | 9.3s |
| 2026-10-22 | 163 | 13 | 5 | 4 | 275 | 3.9s |
| 2026-10-23 | 330 | 5 | 2 | 4 | 240 | 2.7s |
| 2026-10-24 | 289 | 8 | 7 | 7 | 224 | 2.6s |
| 2026-10-25 | 503 | 8 | 4 | 4 | 368 | 3.8s |
| 2026-10-26 | 405 | 8 | 2 | 5 | 247 | 5.1s |
| 2026-10-27 | 313 | 8 | 3 | 5 | 225 | 4.2s |
| 2026-10-28 | 90 | 9 | 4 | 3 | 169 | 4.3s |
| 2026-10-29 | 52 | 3 | 0 | 0 | 125 | 0.7s |
| 2026-10-30 | 155 | 2 | 3 | 0 | 164 | 3.1s |
| 2026-10-31 | 125 | 2 | 1 | 1 | 153 | 3.5s |
| 2026-11-01 | 204 | 4 | 0 | 3 | 248 | 2.0s |
| 2026-11-02 | 83 | 5 | 1 | 2 | 313 | 3.7s |
| 2026-11-03 | 7 | 1 | 2 | 0 | 320 | 5.9s |
| 2026-11-04 | 1 | 0 | 1 | 0 | 97 | 0.2s |
| 2026-11-05 | 7 | 1 | 0 | 0 | 47 | 0.3s |
| 2026-11-06 | 3 | 0 | 0 | 0 | 145 | 0.3s |
| 2026-11-07 | 2 | 0 | 0 | 0 | 114 | 0.2s |
| 2026-11-08 | 1 | 0 | 0 | 0 | 204 | 0.3s |
| 2026-11-09 | 1 | 0 | 0 | 0 | 81 | 0.2s |
| 2026-11-10 | 0 | 0 | 0 | 0 | 7 | 0.2s |
| 2026-11-11 | 0 | 0 | 0 | 0 | 4 | 0.2s |
| 2026-11-12 | 2 | 0 | 0 | 0 | 5 | 0.2s |
| 2026-11-13 | 0 | 0 | 0 | 0 | 3 | 0.2s |
| 2026-11-14 | 0 | 0 | 0 | 0 | 1 | 0.2s |
| 2026-11-15 | 0 | 0 | 0 | 0 | 2 | 0.2s |
| 2026-11-16 | 0 | 0 | 0 | 0 | 1 | 0.2s |
| 2026-11-17 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-18 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-19 | 0 | 0 | 0 | 0 | 2 | 0.2s |
| 2026-11-20 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-21 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-22 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-23 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-24 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-25 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-26 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-27 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-28 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-29 | 0 | 0 | 0 | 0 | 0 | 0.2s |
| 2026-11-30 | 0 | 0 | 0 | 0 | 0 | 0.2s |
