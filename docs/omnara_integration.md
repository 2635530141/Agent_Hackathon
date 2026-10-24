# Omnara REST integration

Implemented in `schedule_service/omnara.py`, `omnara_routes.py` and `/demo`. The user chose to continue skeleton work before completing authorization. **No verified real account connection or live model run is claimed.**

Run `python -m schedule_service.demo --port 8013`, open `/demo`, click Connect Omnara and complete sign-in and approval on the official page. The server verifies `/v1/me` before displaying connected. Select an accessible organization, project and a model already granted to that project. No model grants or billing settings are changed.

The documented device flow uses discovery at `https://app.omnara.com/.well-known/oauth-authorization-server` and public client `omnara-cli`. Its personal access token has the account's permissions, not separate tool scopes. This local bridge keeps it only in process memory and restricts its own behavior. Restart clears the local copy; revoke the token in Omnara to invalidate the provider grant. Never paste tokens in chat or frontend code. Optional server-managed `OMNARA_TOKEN` is supported; no credential file is loaded or created.

One agent is created per request at `https://api.omnara.com/v1`, with an idempotency key and local successful-result cache. Its only custom tools are get_profile, find_matches, suggest_places and check_booking. There are no shell, machine, subagent, email, calendar-write or reservation tools. Projection includes synthetic names, course and exact-duration common slots; no private titles, raw calendar rows, emails, addresses or credentials. The location is explicitly synthetic and booking unsupported.

Limits: six tool results, eight model responses, a 30-second work deadline with bounded HTTP timeouts and best-effort cancellation. Cancellation may itself fail. Candidate ids, slot indexes, place ids and reason codes are validated; unknown ids/tools abort. Reasoning is not displayed. Invitation prose remains an untrusted draft and must not be treated as an approved invitation. Browser setup routes require local host, demo authentication and same-origin requests; secret device codes and provider tokens stay server-side.

24 mock cases cover device binding, pending/slow polling, expiration, trusted endpoints, sanitized errors, grounded output, forbidden tools, budgets, deduplication and private-data projection. Discovery and device-code creation were exercised live. Provider approval, real `/me` verification and inference were not completed. This is an isolated synthetic demo; the team's full Interpret/Score/Advise loop, cost ceiling, restart recovery and multi-user credential storage remain future work.

Official references: [authentication](https://docs.omnara.com/api/authentication), [quickstart](https://docs.omnara.com/quickstart), [custom tools](https://docs.omnara.com/tools/custom), [events](https://docs.omnara.com/events/streaming), [configuration](https://docs.omnara.com/agents/configuration).
