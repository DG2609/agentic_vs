---
name: get-api-docs
description: >
  Use this skill when you need documentation for a third-party library, SDK, or API
  before writing code that uses it — for example, "use the OpenAI API", "call the
  Stripe API", "use the Anthropic SDK", "query Pinecone", or any time the user asks
  you to write code against an external service and you need current API reference.
  Fetch the docs with chub_search + chub_get before answering, rather than relying
  on training knowledge.
access: planner
---

# Get API Docs via Context Hub

When you need documentation for a library or API, fetch it with Context Hub
rather than guessing from training data. This gives you the current, correct API.

## Available Docs (68+ services)

AI/ML: openai, anthropic, gemini, cohere, deepseek, huggingface, replicate
Cloud: aws, cloudflare, vercel, firebase, supabase
Database: mongodb, redis, elasticsearch, chromadb, pinecone, qdrant, weaviate
Auth: auth0, clerk, okta, stytch
Payments: stripe, paypal, braintree, square, razorpay
Messaging: slack, discord, twilio, postmark, resend, sendgrid, mailchimp
DevTools: github, jira, linear, sentry, datadog, launchdarkly
And many more...

## Step 1 — Find the right doc

```tool
chub_search(query="<library or API name>")
```

Pick the best-matching `id` from the results (e.g. `openai/chat-api`, `stripe/api`).

## Step 2 — Fetch the docs

```tool
chub_get(entry_id="<id>", lang="py")
```

Use lang="py" for Python, "js" for JavaScript, "ts" for TypeScript.
Omit lang if the doc has only one language variant.

## Step 3 — Use the docs

Read the fetched content and use it to write accurate code.
Do NOT rely on memorized API shapes — use what the docs say.

## Step 4 — Annotate what you learned

After completing the task, if you discovered something not in the doc —
a gotcha, workaround, version quirk, or project-specific detail:

```tool
chub_annotate(entry_id="<id>", note="<concise actionable note>")
```

Annotations persist across sessions and appear automatically on future fetches.

## Step 5 — Give feedback (ask user first)

```tool
chub_feedback(entry_id="<id>", rating="up", comment="Clear examples")
```

Labels: accurate, well-structured, helpful, good-examples,
outdated, inaccurate, incomplete, wrong-examples, wrong-version, poorly-structured

## Quick Reference

| Goal | Tool call |
|------|-----------|
| Find a doc | `chub_search(query="stripe")` |
| List all | `chub_search()` |
| Fetch Python docs | `chub_get(entry_id="stripe/api", lang="py")` |
| Fetch everything | `chub_get(entry_id="stripe/api", full=True)` |
| Fetch specific ref | `chub_get(entry_id="stripe/api", file="references/auth.md")` |
| Save a note | `chub_annotate(entry_id="stripe/api", note="needs raw body")` |
| List all notes | `chub_annotate(entry_id="", list_all=True)` |
| Rate a doc | `chub_feedback(entry_id="stripe/api", rating="up")` |
