require 'json'
input = JSON.parse(STDIN.read)
account = Account.find(input.fetch('account'))
inbox = account.inboxes.find(input.fetch('inbox'))
raise 'Unexpected pilot scope' unless account.name == 'PilotFixture0001' && inbox.name == 'Integrated pilot API' && inbox.channel.is_a?(Channel::Api) && inbox.channel.identifier == input.fetch('channel')
identifier = 'authorized-personal-test-v1'
result = ActiveRecord::Base.transaction do
  account.lock!
  contact = account.contacts.find_by(identifier: identifier)
  if contact
    entries = ContactInbox.where(contact_id: contact.id, inbox_id: inbox.id).to_a
    raise 'Contact association conflict' unless entries.one?
    ci = entries.first
    convs = account.conversations.where(inbox_id: inbox.id, contact_id: contact.id, contact_inbox_id: ci.id).to_a
    raise 'Conversation conflict' unless convs.one?
    conv = convs.first
  else
    contact = account.contacts.create!(name: 'Homologação pessoal autorizada', identifier: identifier)
    ci = ContactInbox.create!(contact: contact, inbox: inbox, source_id: SecureRandom.uuid)
    conv = Conversation.create!(account: account, inbox: inbox, contact: contact, contact_inbox: ci).reload
  end
  {source: ci.source_id, conversation: conv.display_id, contact: contact.id}
end
puts 'PERSONAL_CONFIG=' + JSON.generate(result)
