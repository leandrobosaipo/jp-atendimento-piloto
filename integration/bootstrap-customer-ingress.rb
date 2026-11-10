require 'json'
name='JP Customer Intake Isolado 2026-11-10'
raise 'Existing isolated account; reconcile receipt' if Account.exists?(name:name)
result=ActiveRecord::Base.transaction do
  account=Account.create!(name:name)
  user=User.new(name:'JP isolated intake agent',email:'jp-isolated-intake-20261110@example.invalid',password:SecureRandom.hex(32)+'Aa!')
  user.skip_confirmation!
  user.save!
  AccountUser.create!(account:account,user:user,role: :agent)
  channel=Channel::Api.create!(account:account)
  inbox=Inbox.create!(account:account,channel:channel,name:'JP Customer Intake Isolado',enable_auto_assignment:false)
  InboxMember.create!(user:user,inbox:inbox)
  {account:account.id,inbox:inbox.id,channel:channel.identifier,agent_token:user.access_token.token}
end
puts 'JP_ISOLATED_CONFIG='+JSON.generate(result)
