# 🏥 Κέντρο Αεροπορικής Ιατρικής (K.A.I.) - Medical Management System
### *Enterprise Interoperability & Medical Security (ISO 27799)*

![HL7 FHIR](https://img.shields.io/badge/HL7-FHIR%20R4-green.svg)
![HL7 v2.x](https://img.shields.io/badge/HL7-v2.x%20Legacy-blue.svg)
![Security](https://img.shields.io/badge/Security-ISO%2027799-red.svg)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED.svg)

Το σύστημα Κ.Α.Ι. είναι μια προηγμένη πλατφόρμα διαχείρισης υγειονομικών εξετάσεων, σχεδιασμένη για την Πολεμική Αεροπορία. Συνδυάζει την αυστηρή ασφάλεια δεδομένων με την πλήρη διαλειτουργικότητα.

---

## 🔌 Διαλειτουργικότητα (HL7 Interoperability)

Το σύστημα είναι πλήρως συμβατό με το διεθνές ιατρικό πρωτόκολλο **HL7**, επιτρέποντας τη διασύνδεση με συστήματα HIS.

### 🟢 Modern Interface: HL7 FHIR (R4)
* **Resource Mapping:** Πλήρης υποστήριξη για `Patient` και `DiagnosticReport`.
* **FHIR Bundles:** Εξαγωγή πλήρους ιατρικού ιστορικού σε δομημένη μορφή JSON.
* **REST API:** Πρόσβαση μέσω του endpoint `/api/fhir/Patient/<AMKA>`.

### 🔵 Legacy Interface: HL7 v2.x (ER7)
* **ORU Export:** Εξαγωγή ιστορικού σε μορφή `ORU^R01` (Segments: MSH, PID, OBR, OBX).
* **Greek Support:** Υποστήριξη Ελληνικών (UTF-8 / ISO-8859-7) μέσω του πεδίου `MSH-18`.

---

## 🚀 Βασικά Χαρακτηριστικά

* **ISO 27799 Compliance:** Πλήρες Audit Trail (Ιχνηλασιμότητα) για κάθε ενέργεια.
* **HIS Integration:** Ενσωματωμένο RDP (Guacamole) για άμεση πρόσβαση στο HIS.
* **Smart Pre-fill:** Αυτόματη προσυμπλήρωση στοιχείων από το ιστορικό εξετάσεων.
* **Centralized Dashboard:** Διαχείριση εξετάσεων, e-Ραντεβού και Google Forms.

---

## 🏗️ Τεχνική Αρχιτεκτονική

* **Backend:** Python 3.11 / Flask.
* **Database:** PostgreSQL.
* **Security:** API Key Authentication (X-API-KEY).
* **Infrastructure:** Docker Containerization.

---

## 📦 Οδηγίες Εγκατάστασης & Δοκιμής

<br>

<b>1. Εκκίνηση Υπηρεσιών</b>
<p>Χρησιμοποιήστε το Docker Compose για να σηκώσετε όλο το stack:</p>

<pre><code>docker compose -f docker-compose.local.yml up -d --build</code></pre>

<hr>

<b>2. Πρόσβαση στο FHIR API</b>
<p>Ανάκτηση ιστορικού σε μορφή JSON Bundle:</p>

<pre><code>GET /api/fhir/Patient/[AMKA]</code></pre>

---

*Αναπτύχθηκε για το Κέντρο Αεροπορικής Ιατρικής με έμφαση στη διαλειτουργικότητα και την ασφάλεια.*
