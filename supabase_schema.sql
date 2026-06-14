CREATE TABLE IF NOT EXISTS patients (
  phone TEXT PRIMARY KEY,
  name TEXT,
  email TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversations (
  patient_phone TEXT PRIMARY KEY REFERENCES patients(phone),
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'bot_off', 'recado')),
  recado_count INT DEFAULT 0,
  last_seen_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  patient_phone TEXT REFERENCES patients(phone),
  role TEXT CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  msg_type TEXT DEFAULT 'text' CHECK (msg_type IN ('text', 'audio', 'image')),
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_patient_created
  ON messages (patient_phone, created_at DESC);

CREATE TABLE IF NOT EXISTS appointment_reminders (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  cal_booking_uid TEXT UNIQUE,
  patient_phone TEXT REFERENCES patients(phone),
  doctor_name TEXT,
  specialty TEXT,
  appointment_at TIMESTAMPTZ,
  status TEXT DEFAULT 'pending' CHECK (
    status IN ('pending', 'reminder_sent', 'confirmed', 'recado', 'rescheduled')
  ),
  attempts INT DEFAULT 0,
  reminded_at TIMESTAMPTZ,
  responded_at TIMESTAMPTZ
);
