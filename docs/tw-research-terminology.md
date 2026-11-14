# TW Research terminology (#356)

Stable investor-facing terms. Keep Advanced panels for technical IDs / digests / raw JSON.

| Concept (internal) | English (default UI) | zh-TW (default UI) | Notes |
| --- | --- | --- | --- |
| universe | research stocks / stock selection / stock set | 研究股票 / 股票範圍 | Never 「宇宙」 in default UI |
| candidate set | saved selection | 已儲存選取 / 選股清單 | Never 「候選集」 in default UI |
| scan id | research record id (Advanced) | 研究紀錄編號（進階） | Hide raw `scan_…` unless Advanced |
| enrichment | fill missing data / data-fill | 補齊缺漏資料 / 補齊 | Never English “enrichment” / 「游標」 |
| snapshot (first-pass) | saved first-round data / results | 已儲存的第一輪資料／結果 | Never 「快照」 in default UI |
| complete_attempted | all symbols attempted (gaps may remain) | 已全部嘗試（可能仍有缺口） | Quality honesty |
| archive | archive / restore | 封存／還原 | Soft-hide; rows retained (#355) |
| permanent delete | delete permanently | 永久刪除 | Hard DB delete only when eligible (#355) |
| cancel | stop collection | 取消執行 | Keeps evidence; fences leases (#355) |

Advanced-only: job/run IDs (`fpj_…`, `rr_…`), digests, manifest/chunk JSON, lease/worker wording.
