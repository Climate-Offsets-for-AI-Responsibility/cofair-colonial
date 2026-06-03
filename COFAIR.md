# COFAIR platform link

| Repo | Role |
|------|------|
| **cofair-colonial** (this) | Upstream list prices → `pricing_history/` |
| [cofair-platform](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-platform) | Docs hub, local dev, GitHub setup |
| [cofair-exchange](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-exchange) | Consumes snapshots; attribution + billing |
| [cofair-contracts](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-contracts) | OpenAPI + pricing types |

Do not duplicate pricing scrape logic in exchange — ingest snapshots from this repo.
