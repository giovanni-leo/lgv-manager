"""Database-level referential actions that Django 5.2 does not generate itself.

Django emulates ON DELETE in Python and creates foreign keys without an
ON DELETE clause. Since the database is meant to be queried and edited freely
with SQL, the referential actions of the reference schema are applied to the
real constraints here, plus composite foreign keys that guarantee a share class
belongs to the same company as the row that references it.

``apply_sql()`` is idempotent; it is run by migration 0002 and can be re-run
with ``manage.py sync_db_constraints`` after a migration alters a foreign key.
"""

# (table, column, referenced table, ON DELETE action)
FK_ACTIONS = [
    ("contacts", "company_id", "companies", "SET NULL"),
    ("contacts", "co_investor_id", "co_investors", "SET NULL"),
    ("round_participants", "deal_id", "deals", "CASCADE"),
    ("round_participants", "co_investor_id", "co_investors", "NO ACTION"),
    ("round_participants", "contact_id", "contacts", "SET NULL"),
    ("deals", "company_id", "companies", "CASCADE"),
    ("deals", "source_contact_id", "contacts", "SET NULL"),
    ("deal_stage_history", "deal_id", "deals", "CASCADE"),
    ("share_classes", "company_id", "companies", "CASCADE"),
    ("documents", "company_id", "companies", "CASCADE"),
    ("documents", "deal_id", "deals", "CASCADE"),
    ("share_events", "company_id", "companies", "CASCADE"),
    ("share_events", "share_class_id", "share_classes", "CASCADE"),
    ("share_events", "source_document_id", "documents", "SET NULL"),
    ("option_pools", "company_id", "companies", "CASCADE"),
    ("investments", "company_id", "companies", "CASCADE"),
    ("investments", "entity_id", "investing_entities", "NO ACTION"),
    ("investments", "deal_id", "deals", "SET NULL"),
    ("investments", "share_class_id", "share_classes", "NO ACTION"),
    ("investments", "converted_from_id", "investments", "NO ACTION"),
    ("investments", "source_document_id", "documents", "SET NULL"),
    ("convertible_terms", "investment_id", "investments", "CASCADE"),
    ("realizations", "company_id", "companies", "CASCADE"),
    ("realizations", "entity_id", "investing_entities", "NO ACTION"),
    ("realizations", "share_class_id", "share_classes", "NO ACTION"),
    ("valuations", "company_id", "companies", "CASCADE"),
    ("valuations", "share_class_id", "share_classes", "NO ACTION"),
    ("company_metrics", "company_id", "companies", "CASCADE"),
    ("milestones", "company_id", "companies", "CASCADE"),
    ("interactions", "company_id", "companies", "CASCADE"),
    ("interactions", "deal_id", "deals", "CASCADE"),
    ("interactions", "contact_id", "contacts", "SET NULL"),
    ("document_links", "document_id", "documents", "CASCADE"),
    ("document_links", "investment_id", "investments", "CASCADE"),
    ("document_links", "share_event_id", "share_events", "CASCADE"),
    ("document_links", "interaction_id", "interactions", "CASCADE"),
]

# Tables whose (share_class_id, company_id) must match share_classes(id, company_id).
CLASS_COMPANY_FKS = [
    ("share_events", "CASCADE"),
    ("investments", "NO ACTION"),
    ("realizations", "NO ACTION"),
    ("valuations", "NO ACTION"),
]

_RECREATE_FK = """
DO $$
DECLARE r record;
BEGIN
  -- Skip tables and columns that a later migration has not created yet.
  IF NOT EXISTS (SELECT 1 FROM pg_attribute
                 WHERE attrelid = to_regclass('{table}') AND attname = '{column}' AND NOT attisdropped)
     OR to_regclass('{ref}') IS NULL THEN
    RETURN;
  END IF;
  FOR r IN
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
    WHERE con.contype = 'f' AND con.conrelid = '{table}'::regclass
      AND array_length(con.conkey, 1) = 1 AND att.attname = '{column}'
  LOOP
    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', r.conname);
  END LOOP;
  ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_fkey
    FOREIGN KEY ({column}) REFERENCES {ref}(id) ON DELETE {action} DEFERRABLE INITIALLY DEFERRED;
END $$;
"""

_COMPOSITE_FK = """
ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_class_company_fkey;
ALTER TABLE {table} ADD CONSTRAINT {table}_class_company_fkey
  FOREIGN KEY (share_class_id, company_id) REFERENCES share_classes(id, company_id)
  ON DELETE {action} DEFERRABLE INITIALLY DEFERRED;
"""


def statements():
    for table, column, ref, action in FK_ACTIONS:
        yield _RECREATE_FK.format(table=table, column=column, ref=ref, action=action)
    for table, action in CLASS_COMPANY_FKS:
        yield _COMPOSITE_FK.format(table=table, action=action)


def apply_sql(cursor):
    for sql in statements():
        cursor.execute(sql)
