# Demo transcripts (STT-down path: paste these as text messages — the pipeline after STT is identical)

| # | Say / type | Expected |
|---|---|---|
| 1 | Ramesh Kumar ne paanch kilo chawal udhaar pe liya | ✅ Udhaar: Ramesh Kumar — Rice 5 kg × ₹50 = ₹250 [Undo] |
| 2 | Ramesh ne 2 kilo cheeni udhaar liya | Kaunse customer? [Ramesh Kumar] [Ramesh Sharma] |
| 3 | Suresh ne 1 litre tel udhaar liya | Kaunsa item? [Mustard Oil] [Refined Oil] |
| 4 | Ramesh Kumar ne 2 kilo moong dal udhaar liya | rate catalog mein nahi hai — reply "ek sau bees" → ₹240 (USER_REPLY) |
| 5 | Ramesh Sharma ne 5 kilo chawal 280 rupaye mein udhaar liya | explicit total wins: ₹280 |
| 6 | Sunita Devi ne teen sau pachas rupaye chukaye | ✅ Wapasi ₹350, baaki udhaar shown |
| 7 | 50 kilo chawal aaya 2000 rupaye ka | ✅ Stock aaya: Rice +50 kg |
| 8 | 10 packet biscuit expire ho gaye | ✅ Stock adjust: Biscuit -10 pkt |
| 9 | Mohan Lal ne 20 kilo chawal udhaar liya 12000 rupaye ka | ⚠️ Badi rakam ₹12,000 — confirm |
| 10 | Ramesh Kumar ne 2 kilo chawal udhaar liya aur 100 rupaye chukaye | Ek message mein sirf ek entry |
| 11 | Kavita ne 2 kilo chawal udhaar liya | customer nahi mila — [➕ Naya: Kavita] |
| 12 | Sunita Devi ka hisaab kitna hai | Sunita Devi ka udhaar: ₹X (no ledger write) |
| 13 | Ramesh Kumar ko 500 rupaye udhaar diye | rejected: cash udhaar not supported (would have been booked as a repayment — sign trap) |

Kill-the-worker demo: send #1, `docker compose kill worker` before the reply, `docker compose up -d worker` → the
reaper re-queues within the stale window and exactly one transaction exists (`/dev/stats`, `/dev/trace`).
