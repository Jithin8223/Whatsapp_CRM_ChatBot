from django.db import migrations

TABLES = ['whatsapp_connection', 'whatsapp_connectionoperation', 'conversations_contact', 'conversations_conversation', 'conversations_message', 'conversations_deliveryevent', 'conversations_webhookevent', 'tenants_membership']
SQL = """
CREATE FUNCTION crm_immutable_ownership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.business_id IS DISTINCT FROM NEW.business_id THEN
    RAISE EXCEPTION 'Workspace ownership cannot be changed' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END; $$;
""" + '\n'.join(f'CREATE TRIGGER crm_ownership BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION crm_immutable_ownership();' for table in TABLES)


class Migration(migrations.Migration):
    dependencies = [('conversations', '0002_tenant_integrity')]
    operations = [migrations.RunSQL(SQL, 'DROP FUNCTION crm_immutable_ownership() CASCADE;')]
