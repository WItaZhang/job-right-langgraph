"""Vercel's Flask entry point. Local workspaces use job-right web."""

from job_right.hosted import production_app

app = production_app()
