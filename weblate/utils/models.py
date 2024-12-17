# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# mypy: disable-error-code="var-annotated"

from appconf import AppConf


class WeblateConf(AppConf):
    WEBLATE_GPG_IDENTITY = None
    WEBLATE_GPG_ALGO = "default"

    RATELIMIT_ATTEMPTS = 5
    RATELIMIT_WINDOW = 300
    RATELIMIT_LOCKOUT = 600

    RATELIMIT_SEARCH_ATTEMPTS = 30
    RATELIMIT_SEARCH_WINDOW = 60
    RATELIMIT_SEARCH_LOCKOUT = 60

    RATELIMIT_COMMENT_ATTEMPTS = 30
    RATELIMIT_COMMENT_WINDOW = 60

    RATELIMIT_TRANSLATE_ATTEMPTS = 30
    RATELIMIT_TRANSLATE_WINDOW = 60

    RATELIMIT_GLOSSARY_ATTEMPTS = 30
    RATELIMIT_GLOSSARY_WINDOW = 60

    RATELIMIT_LANGUAGE_ATTEMPTS = 2
    RATELIMIT_LANGUAGE_WINDOW = 300
    RATELIMIT_LANGUAGE_LOCKOUT = 600

    RATELIMIT_MESSAGE_ATTEMPTS = 2

    RATELIMIT_TRIAL_ATTEMPTS = 1
    RATELIMIT_TRIAL_WINDOW = 60
    RATELIMIT_TRIAL_LOCKOUT = 600

    RATELIMIT_PROJECT_ATTEMPTS = 5
    RATELIMIT_PROJECT_WINDOW = 600
    RATELIMIT_PROJECT_LOCKOUT = 600

    SENTRY_DSN = None
    SENTRY_SECURITY = None
    SENTRY_ENVIRONMENT = "devel"
    SENTRY_TOKEN = None
    SENTRY_SEND_PII = False
    SENTRY_PROJECTS = ["weblate"]
    SENTRY_RELEASES_API_URL = (
        "https://sentry.io/api/0/organizations/4507304895905792/releases/"
    )
    SENTRY_EXTRA_ARGS = {}
    SENTRY_TRACES_SAMPLE_RATE = 0
    SENTRY_PROFILES_SAMPLE_RATE = 0

    ZAMMAD_URL = None

    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_BROKER_URL = "memory://"

    STATS_LAZY = False

    DATABASE_BACKUP = "plain"

    BORG_EXTRA_ARGS = None

    HIDE_VERSION = False

    CSP_SCRIPT_SRC = []
    CSP_IMG_SRC = []
    CSP_CONNECT_SRC = []
    CSP_STYLE_SRC = []
    CSP_FONT_SRC = []
    CSP_FORM_SRC = []

    INTERLEDGER_PAYMENT_POINTERS = ["$ilp.uphold.com/ENU7fREdeZi9"]

    PROJECT_NAME_RESTRICT_RE = None
    PROJECT_WEB_RESTRICT_RE = None
    PROJECT_WEB_RESTRICT_HOST = {"localhost"}
    PROJECT_WEB_RESTRICT_NUMERIC = True

    class Meta:
        prefix = ""
