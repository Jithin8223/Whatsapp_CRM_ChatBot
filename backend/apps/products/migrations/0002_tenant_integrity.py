from django.db import migrations


SQL = """
CREATE FUNCTION crm_product_tenant_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.business_id IS DISTINCT FROM OLD.business_id THEN
    RAISE EXCEPTION 'Tenant ownership is immutable' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME = 'products_productfaq' THEN
    IF NOT EXISTS (SELECT 1 FROM products_product WHERE id=NEW.product_id AND business_id=NEW.business_id) THEN
      RAISE EXCEPTION 'Tenant relationship mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME = 'products_productdocument' THEN
    IF NOT EXISTS (SELECT 1 FROM products_product WHERE id=NEW.product_id AND business_id=NEW.business_id) THEN
      RAISE EXCEPTION 'Tenant relationship mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME = 'products_productlead' THEN
    IF NOT EXISTS (SELECT 1 FROM conversations_conversation WHERE id=NEW.conversation_id AND business_id=NEW.business_id)
       OR (NEW.product_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM products_product WHERE id=NEW.product_id AND business_id=NEW.business_id))
       OR (NEW.assigned_agent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tenants_membership WHERE user_id=NEW.assigned_agent_id AND business_id=NEW.business_id)) THEN
      RAISE EXCEPTION 'Tenant relationship mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER crm_product_owner BEFORE UPDATE ON products_product FOR EACH ROW EXECUTE FUNCTION crm_product_tenant_integrity();
CREATE TRIGGER crm_product_faq_tenant BEFORE INSERT OR UPDATE ON products_productfaq FOR EACH ROW EXECUTE FUNCTION crm_product_tenant_integrity();
CREATE TRIGGER crm_product_document_tenant BEFORE INSERT OR UPDATE ON products_productdocument FOR EACH ROW EXECUTE FUNCTION crm_product_tenant_integrity();
CREATE TRIGGER crm_product_lead_tenant BEFORE INSERT OR UPDATE ON products_productlead FOR EACH ROW EXECUTE FUNCTION crm_product_tenant_integrity();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS crm_product_owner ON products_product;
DROP TRIGGER IF EXISTS crm_product_faq_tenant ON products_productfaq;
DROP TRIGGER IF EXISTS crm_product_document_tenant ON products_productdocument;
DROP TRIGGER IF EXISTS crm_product_lead_tenant ON products_productlead;
DROP FUNCTION IF EXISTS crm_product_tenant_integrity();
"""


class Migration(migrations.Migration):
    dependencies = [('products', '0001_initial'), ('conversations', '0010_guest_mobile_and_internal_notes'), ('tenants', '0001_initial')]
    operations = [migrations.RunSQL(SQL, REVERSE_SQL)]
