from django.db import migrations

SQL = """
CREATE FUNCTION crm_tenant_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'conversations_conversation' THEN
    IF NOT EXISTS (SELECT 1 FROM whatsapp_connection WHERE id=NEW.connection_id AND business_id=NEW.business_id)
       OR NOT EXISTS (SELECT 1 FROM conversations_contact WHERE id=NEW.contact_id AND business_id=NEW.business_id)
       OR (NEW.assigned_agent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tenants_membership WHERE user_id=NEW.assigned_agent_id AND business_id=NEW.business_id))
       OR (NEW.last_message_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM conversations_message WHERE id=NEW.last_message_id AND conversation_id=NEW.id AND business_id=NEW.business_id)) THEN
      RAISE EXCEPTION 'Tenant relationship mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME = 'conversations_message' THEN
    IF NOT EXISTS (SELECT 1 FROM conversations_conversation WHERE id=NEW.conversation_id AND business_id=NEW.business_id AND connection_id=NEW.connection_id) THEN
      RAISE EXCEPTION 'Tenant relationship mismatch' USING ERRCODE='23514';
    END IF;
  ELSE
    IF NOT EXISTS (SELECT 1 FROM whatsapp_connection WHERE id=NEW.connection_id AND business_id=NEW.business_id) THEN
      RAISE EXCEPTION 'Tenant relationship mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER crm_conversation_tenant BEFORE INSERT OR UPDATE ON conversations_conversation FOR EACH ROW EXECUTE FUNCTION crm_tenant_integrity();
CREATE TRIGGER crm_message_tenant BEFORE INSERT OR UPDATE ON conversations_message FOR EACH ROW EXECUTE FUNCTION crm_tenant_integrity();
CREATE TRIGGER crm_webhook_tenant BEFORE INSERT OR UPDATE ON conversations_webhookevent FOR EACH ROW EXECUTE FUNCTION crm_tenant_integrity();
CREATE TRIGGER crm_delivery_tenant BEFORE INSERT OR UPDATE ON conversations_deliveryevent FOR EACH ROW EXECUTE FUNCTION crm_tenant_integrity();
CREATE TRIGGER crm_operation_tenant BEFORE INSERT OR UPDATE ON whatsapp_connectionoperation FOR EACH ROW EXECUTE FUNCTION crm_tenant_integrity();
"""


class Migration(migrations.Migration):
    dependencies = [('conversations', '0001_initial'), ('whatsapp', '0001_initial')]
    operations = [migrations.RunSQL(SQL, 'DROP FUNCTION crm_tenant_integrity() CASCADE;')]
