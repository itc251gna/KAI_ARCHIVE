FROM python:3.11-slim

WORKDIR /app

# Εγκατάσταση απαραίτητων εργαλείων για την PostgreSQL
RUN apt-get update && apt-get install -y libpq-dev gcc postgresql-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Δημιουργία Self-Signed SSL πιστοποιητικού για HTTPS
#RUN openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 3650 -subj "/CN=KAI_System"

EXPOSE 443

# Τρέχουμε τον Gunicorn
EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "--workers", "3", "--threads", "4", "app:app"]
