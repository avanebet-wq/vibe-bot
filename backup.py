# -*- coding: utf-8 -*-
"""Legacy backup compatibility shim.

Liza now uses Railway PostgreSQL as its primary database. The previous
SQLite-file backup copied bot.db, which is no longer the runtime database and
therefore cannot provide a valid PostgreSQL backup. Database backups should be
handled by Railway/PostgreSQL backup facilities rather than pretending a local
SQLite snapshot is a production backup.
"""
import logging

LOG = logging.getLogger(__name__)


def create_backup():
    """Compatibility no-op retained for old imports.

    It deliberately does not create or touch bot.db.
    """
    LOG.debug("SQLite backup skipped: Liza uses PostgreSQL")
    return None

# updated 2026-09-18
