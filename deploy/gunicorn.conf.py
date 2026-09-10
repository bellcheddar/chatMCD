"""Gunicorn config for chatMCD.

Threads, not processes. Every /api/chat request is a long-lived SSE stream that
spends its whole life waiting on the Hugging Face Space, so it is I/O bound and
costs almost no CPU. Sync workers would give one concurrent conversation per
worker, and the droplet has 3.8 GB shared with several other apps, so more
workers is not the lever. Two gthread workers with eight threads each is sixteen
concurrent conversations in about the memory of two.

The timeout is long for the same reason: a cold ZeroGPU start plus a 512-token
answer can legitimately take two minutes, and killing that worker would turn a
slow answer into an error.
"""
import os

bind = os.environ.get("BIND_ADDR", "127.0.0.1:8010")
workers = int(os.environ.get("WEB_WORKERS", "2"))
threads = int(os.environ.get("WEB_THREADS", "8"))
worker_class = "gthread"
timeout = int(os.environ.get("WEB_TIMEOUT", "300"))
graceful_timeout = 60
keepalive = 65
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
proc_name = "chatmcd-web"

# gunicorn 26 opens a control socket at /run/user/<uid>/gunicorn.ctl, which does
# not exist for a system user, and falls back to the working directory. That is
# /opt/chatmcd, owned by root while the service runs as chatmcd, so every start
# logged "Control server error: [Errno 13] Permission denied". Harmless, but it
# is noise in the one log that has to be readable at a glance. The unit sets
# PrivateTmp=true, so this /tmp is the service's own and nothing else sees it.
control_socket = os.environ.get("CONTROL_SOCKET", "/tmp/chatmcd-gunicorn.ctl")
