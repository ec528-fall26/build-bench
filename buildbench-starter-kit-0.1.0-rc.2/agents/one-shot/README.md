# One-Shot Build-Bench Agent

## Purpose
This agent will read build-failure evidence, request one repair proposal
from an LLM, and apply permitted edits to the package worktree.

## Current implementation
The agent shell checks that the required input files and package worktree
exist, then writes a completion record without modifying package files.
Model integration and repair execution are not connected yet.

## Runtime
Python 3.11. The current implementation uses only the Python standard library.

## Workspace
The workspace defaults to `/workspace`. For local testing, its location
can be overridden using the `BB_WORKSPACE` environment variable.

Required inputs:
- `input/task.json`
- `input/initial-build.log`
- `work/repo/`

Output:
- `output/agent-result.json`

A status of `completed` indicates that the agent finished running.
Repair success requires independent build and artifact validation.

## Entry point
Run `python -m src.main` from the agent directory.