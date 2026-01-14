# ========================================================================================
# File: config.py
# Description: Configuration and Environment Management.
#
# Purpose:
#   - Loads environment variables using `dotenv`.
#   - Defines global constants for the application, such as email credentials and
#     server settings.
#   - Centralizes configuration to separate settings from application logic.
#
# Key Constants:
#   - COORD_EMAIL: Default coordinator email for risk summaries.
#   - SMTP_*: Email server credentials for notifications.
# ========================================================================================

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration Constants
COORD_EMAIL = os.getenv("COORD_EMAIL")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
