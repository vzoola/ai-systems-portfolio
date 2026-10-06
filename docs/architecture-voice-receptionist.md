# Architecture: AI overflow receptionist (Patient Catch)

**Live page:** [patientcatch.com](https://patientcatch.com) · Astro (Cloudflare Pages) · Vapi voice agents · Twilio · Supabase
**Status:** launched, then paused to focus elsewhere. The architecture carries over to any small office.

## The design problem was trust, not tech
Ten cold calls to dental offices brought ten rejections, and all of them were fear: data theft, staff replacement, calls hijacked, "what if it breaks." So I designed the product around those fears:

1. **You keep your number.** Nothing gets ported.
2. **The front desk answers first.** Only calls nobody picks up roll to the AI, the same way they'd go to voicemail.
3. **Failsafe:** if the AI line fails, the call drops back to ordinary voicemail.
4. **Narrow, honest claims.** The agent never promises integrations it doesn't have, never claims compliance it can't prove, and quotes only the real price and the real callback number. Those guardrails live in the agent's prompt and get re-checked after every publish.

## Call flow
`missed call → forward on no-answer → Vapi assistant (STT → LLM → TTS) → structured intake (name, reason, urgency, callback) → stored in Supabase → office notified → human calls back`

## How I run voice agents
- Two lines with the same prompt structure: a Q&A line and a demo line, so prospects can call and try it.
- Every prompt change gets a **publish diff plus a post-publish grep** for stale facts (it once caught an outdated figure still being quoted by a live agent).
- The same pattern later powered an outbound agent for small-business outreach, using a hand-screened test batch before any real campaign.
