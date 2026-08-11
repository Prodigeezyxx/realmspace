# realmspace API Reference

> Full REST API for the edge kit. All endpoints are tenant-scoped (`tenant_id`).
> Base URL: `http://localhost:8000`

## Endpoints

### Health & System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Basic health check |
| GET | `/v1/platform/health` | Full system health: tables, bus lag, checks |
| GET | `/v1/platform/tenant/{tid}` | Tenant aggregate stats |

### Event Bus (Phase 1)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/events` | Append single event |
| POST | `/v1/events/batch` | Append event batch |
| GET | `/v1/events?tenantId=&sessionId=&afterSeq=&limit=` | Read events |
| GET | `/v1/graph/{tid}/{sid}` | Graph snapshot |
| GET | `/v1/sessions/{tid}` | List recorded sessions |
| GET | `/v1/sessions/{tid}/{sid}/outcome` | 4-layer ROI scorecard |
| WS | `/v1/ws/{tid}/{sid}` | Real-time event stream |

### Ask the Room (Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/ask` | Natural language → SQL query (14 templates) |

### Rules Engine (Phase 3)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/rules/{tid}` | List tenant rules |
| POST | `/v1/rules` | Create rule |
| PUT | `/v1/rules/{id}` | Update rule (enable/disable, modify) |
| DELETE | `/v1/rules/{id}` | Delete rule |
| POST | `/v1/rules/test` | Dry-run rule against real data |

### Attribute (Phase 4)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/consent` | Capture consent (t1/t2/t3) |
| POST | `/v1/consent/{id}/withdraw` | Withdraw consent |
| GET | `/v1/consent/{tid}` | List consents |
| POST | `/v1/identity/resolve` | Link anon → contact (consent-gated) |
| GET | `/v1/identity/{tid}` | List resolved identities |
| POST | `/v1/intent/score` | Score visitor intent (0-10) |
| POST | `/v1/handoff` | Create lead handoff |
| GET | `/v1/handoff/{tid}` | List handoffs |
| POST | `/v1/handoff/{id}/sync` | Sync to CRM (hubspot/webhook) |
| PUT | `/v1/crm/connection` | Configure CRM connection |
| GET | `/v1/crm/connection/{tid}` | List CRM connections |

### Intelligence (Phase 5)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/insights/generate` | Generate LLM insights from session data |
| GET | `/v1/insights/{tid}` | List insights |
| POST | `/v1/sdr/draft` | Generate follow-up email draft |
| GET | `/v1/sdr/draft/{tid}` | List SDR drafts |

### Platform (Phase 6)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/check` | Check RBAC permission |
| GET | `/v1/auth/users` | List users |
| POST | `/v1/auth/users` | Add user |
| GET | `/v1/export/{tid}/{sid}?format=json|csv` | Export session |
| GET | `/v1/export/ledger/{tid}` | Attribution ledger CSV |
| GET | `/v1/billing/usage/{tid}` | Usage + limits |

## Database (SQLite, 16 tables)

event_log, graph_nodes, graph_edges, consumer_cursor, dead_letter,
auth_users, rules, action_log, consents, identities, intent_scores,
lead_handoffs, crm_connections, insights, sdr_drafts

## Running

```bash
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd ../dashboard
npm run dev
# → http://localhost:3000
```

## Model

Default: `poolside/laguna-xs-2.1:free` via OpenRouter.
Set `OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL` in `backend/.env`.
