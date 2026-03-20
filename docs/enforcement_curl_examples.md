# Enforcement API curl examples

Set these before running the examples:

```bash
export SABI_API_BASE_URL="http://127.0.0.1:8000"
export SABI_API_KEY="replace-with-real-api-key"
```

## Health

```bash
curl -X GET "$SABI_API_BASE_URL/enforcement/health" \
  -H "X-API-Key: $SABI_API_KEY"
```

## Run all enforcement checks

```bash
curl -X GET "$SABI_API_BASE_URL/enforcement/check" \
  -H "X-API-Key: $SABI_API_KEY"
```

## Get one teacher's enforcement status

```bash
curl -X GET "$SABI_API_BASE_URL/enforcement/status/12" \
  -H "X-API-Key: $SABI_API_KEY"
```

## Resolve an enforcement log

```bash
curl -X POST "$SABI_API_BASE_URL/enforcement/resolve/42" \
  -H "X-API-Key: $SABI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "resolved_by": "principal.bot"
  }'
```

## Confirm an HR query draft

```bash
curl -X POST "$SABI_API_BASE_URL/enforcement/query/confirm/42" \
  -H "X-API-Key: $SABI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "confirmed_by": "HR Lead"
  }'
```

## List all enforcement rules

```bash
curl -X GET "$SABI_API_BASE_URL/enforcement/rules" \
  -H "X-API-Key: $SABI_API_KEY"
```

## Update an enforcement rule

```bash
curl -X PATCH "$SABI_API_BASE_URL/enforcement/rules/1" \
  -H "X-API-Key: $SABI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "is_active": true,
    "reminder_days_before": 2,
    "escalate_l1_days_after": 3,
    "escalate_l2_days_after": 2,
    "reminder_message": "Hello {teacher_name}. Please resolve {reference} before {deadline}.",
    "due_today_message": "Warning: {teacher_name}, {reference} is due today.",
    "defaulted_message": "Default notice: {teacher_name}, {reference} is overdue.",
    "l1_report_template": "Principal alert: {teacher_name} defaulted on {reference}.",
    "l2_query_template": "Formal query draft for {teacher_name}: non-compliance on {reference}."
  }'
```

## Optional jq formatting

```bash
curl -X GET "$SABI_API_BASE_URL/enforcement/check" \
  -H "X-API-Key: $SABI_API_KEY" | jq
```
