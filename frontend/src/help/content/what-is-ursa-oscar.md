# What is URSA-OSCAR?

URSA-OSCAR (Unified Rest & Somatic Analytics — OSCAR) is a self-hosted analytics platform for your CPAP machine's recorded therapy data. It ingests the SD card output from ResMed AirSense 10 and 11 devices, computes nightly summaries that match what OSCAR's desktop application shows, and surfaces the data through a web UI, a built-in AI assistant, and an MCP server that any AI assistant (like Claude) can connect to.

It is **not** a medical device. It does not diagnose, prescribe, or replace your sleep medicine provider. It is a tool that helps you understand your own data — the same data your CPAP already collects every night.

## What URSA-OSCAR does

1. **Ingests** the raw EDF and JSON files your CPAP writes to its SD card
2. **Computes** nightly summaries: AHI broken into central, obstructive, hypopnea, and RERA components; pressure percentiles; leak statistics; mask-on time
3. **Serves** a web UI for reviewing daily detail, comparing periods, and exploring trends
4. **Exposes** an MCP server so AI assistants can query your data conversationally
5. **Brings** the AI experience in-app via a chat panel — bring your own API key for Claude, OpenAI, Gemini, OpenRouter, Groq, or any OpenAI-compatible local LLM

## What URSA-OSCAR is built on

URSA-OSCAR builds on the file-format work of the [OSCAR project](https://www.sleepfiles.com/OSCAR/) — the open-source CPAP data viewer that figured out how to read ResMed's proprietary SD card format. Without OSCAR, this wouldn't exist. The "OSCAR" in URSA-OSCAR is that attribution.

## Who URSA-OSCAR is for

You, if:

- You use a ResMed AirSense 10 or 11 CPAP machine
- You want to review your therapy data outside the manufacturer's app
- You want to give an AI assistant access to your CPAP data for conversational analysis
- You're comfortable running Docker containers on a home server, NAS, or similar
- You're willing to read your own data and form your own questions

You, if not:

- You want a fully managed service (this is self-hosted; you run it)
- You expect cloud sync between devices (everything stays on your hardware)
- You want clinical interpretation of your data (URSA-OSCAR explains what your data shows, not what to do about it)

## What this Help section covers

Use the topic tree on the left to navigate. The sections build on each other but each topic stands alone — jump straight to what you need. The AI assistant in the chat panel can also answer questions by reading these same topics; ask it directly if you'd rather not browse.
