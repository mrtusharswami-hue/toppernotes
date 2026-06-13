-- 🪙 यूज़र्स और उनके पॉइंट्स का रजिस्टर
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    points INTEGER DEFAULT 3
);

-- 📁 नोट्स और उनके ड्राइव लिंक्स का रजिस्टर
CREATE TABLE IF NOT EXISTS notes (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    subject TEXT NOT NULL,
    filename TEXT NOT NULL UNIQUE,
    uploader_email TEXT,
    drive_link TEXT,
    description TEXT
);