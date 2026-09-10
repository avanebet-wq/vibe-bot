# -*- coding: utf-8 -*-
"""Forward-compatible normalized storage helpers without breaking legacy JSON keys."""
from database import db_get, db_set

def get_table(name, default=None): return db_get("table:"+str(name), default if default is not None else {})
def set_table(name, value): db_set("table:"+str(name), value)
