-- 010: a second mailbox for outreach, the HIOX India cPanel mailbox (partnership@weoneaviation.in), sent over SMTP and
-- read over IMAP. Its password is stored encrypted like the other keys; the address, server and the choice of sending
-- mailbox ("send_from": hostinger | hiox) live in system_setting 'integrations'.
ALTER TABLE flightbridge.integration_secret DROP CONSTRAINT IF EXISTS integration_secret_name_check;
ALTER TABLE flightbridge.integration_secret ADD CONSTRAINT integration_secret_name_check CHECK (name = ANY (ARRAY[
  'google_places_api_key', 'groq_api_key', 'gemini_api_key', 'hostinger_mail_api_token', 'hostinger_mailbox_resource_id',
  'tavily_api_key', 'hiox_mail_password']));
