from rest_framework.exceptions import ValidationError
from apps.whatsapp.providers import provider_for


class WhatsAppChannel:
    local_delivery = False

    @staticmethod
    def validate(contact):
        if not (contact.whatsapp_id or '').endswith(('@c.us', '@lid')):
            raise ValidationError('This contact has no WhatsApp address.')

    @staticmethod
    def sender(connection):
        return provider_for(connection).send_text

    @staticmethod
    def resolver(connection):
        return provider_for(connection).resolve_recipient

    @staticmethod
    def file_sender(connection):
        return provider_for(connection).send_file


class WebsiteChannel:
    # Once committed, the reply is available in the guest's conversation history.
    local_delivery = True

    @staticmethod
    def validate(contact):
        if not contact.website_visitor_id:
            raise ValidationError('This contact has no website chat identity.')


CHANNELS = {'whatsapp': WhatsAppChannel(), 'website': WebsiteChannel()}


def channel_for(connection):
    if connection.simulated:
        return SIMULATION_CHANNEL
    return CHANNELS[connection.channel]


class WhatsAppSimulationChannel(WhatsAppChannel):
    local_delivery = True

    @staticmethod
    def validate(contact):
        if not (contact.whatsapp_id or '').endswith('@simulation'):
            raise ValidationError('Simulation requires a test contact.')

    @staticmethod
    def sender(connection):
        raise RuntimeError('Simulated messages cannot use a live provider.')


SIMULATION_CHANNEL = WhatsAppSimulationChannel()
