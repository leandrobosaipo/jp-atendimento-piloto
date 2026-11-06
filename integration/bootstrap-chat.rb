require 'json'
account=Account.find_by!(name: 'PilotFixture0001')
raise 'Existing integrated inbox' if account.inboxes.where(name:'Integrated pilot API').exists?
r=ActiveRecord::Base.transaction do
  user=User.new(name:'Integrated pilot agent',email:'integrated-pilot@example.invalid',password:SecureRandom.hex(32)+'Aa!')
  user.skip_confirmation!
  user.save!
  AccountUser.create!(account:account,user:user,role: :agent)
  channel=Channel::Api.create!(account:account)
  inbox=Inbox.create!(account:account,channel:channel,name:'Integrated pilot API')
  InboxMember.create!(user:user,inbox:inbox)
  actors={}
  %w[A B C].each do |k|
    contact=account.contacts.create!(name:"Cliente fictício #{k}",identifier:"pilot-integrated-#{k}")
    ci=ContactInbox.create!(contact:contact,inbox:inbox,source_id:SecureRandom.uuid)
    conv=Conversation.create!(account:account,inbox:inbox,contact:contact,contact_inbox:ci).reload
    actors[k]={source:ci.source_id,conversation:conv.display_id,contact:contact.id}
  end
  {account:account.id,inbox:inbox.id,channel:channel.identifier,agent_token:user.access_token.token,actors:actors}
end
puts 'PILOT_CONFIG='+JSON.generate(r)
