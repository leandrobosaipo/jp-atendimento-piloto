require 'json'
account = Account.find_by!(name: 'PilotFixture0001')
known = account.contacts.find_by!(identifier: 'PilotFixture0001')
raise 'Fixture inbox exists' if account.inboxes.where(name: 'Synthetic review v1').exists?
result = ActiveRecord::Base.transaction do
  agent = User.new(email: 'synthetic-reviewer@example.invalid', name: 'Synthetic reviewer', password: SecureRandom.hex(32) + 'Aa!')
  agent.skip_confirmation!
  agent.save!
  AccountUser.create!(account: account, user: agent, role: :agent)
  channel = Channel::Api.create!(account: account)
  inbox = Inbox.create!(account: account, channel: channel, name: 'Synthetic review v1')
  InboxMember.create!(inbox: inbox, user: agent)
  unknown = account.contacts.create!(name: 'Unidentified synthetic input', identifier: 'PilotIdentityReview0001')
  ids = [known, unknown].map do |contact|
    ci = ContactInbox.create!(contact: contact, inbox: inbox, source_id: SecureRandom.uuid)
    # The account-scoped display ID is populated by a PostgreSQL trigger.
    Conversation.create!(account: account, inbox: inbox, contact: contact, contact_inbox: ci).reload.display_id
  end
  { accountId: account.id, knownConversation: ids[0], reviewConversation: ids[1], inboxId: inbox.id, chatToken: agent.access_token.token }
end
puts 'PILOT_CONFIG=' + JSON.generate(result)
