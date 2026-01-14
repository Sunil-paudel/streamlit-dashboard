#!/bin/bash
mkdir -p ~/.streamlit/
echo "[server]
enableCORS = false
enableXsrfProtection = false
headless = true
[browser]
gatherUsageStats = false
" > ~/.streamlit/config.toml